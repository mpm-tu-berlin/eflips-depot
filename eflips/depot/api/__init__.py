"""
This package contains the public API for eFLIPS-Depot.

It is to be used in conjunction with the
`eflips.model <https://github.com/mpm-tu-berlin/eflips-model>`_ package, where the Scenario is defined.

Notes on the usage of the API
-----------------------------

The following steps are recommended for using the API:

1. Check if there are already "driving events" in the database. They come from a "consumption simulation" and are
   associated with a vehicle. If there are no driving events, you may use the :func:`simple_consumption_simulation`
   (with ``initialize_vehicles=True``) to create them. This function will also initialize the vehicles in the database
   with the correct vehicle type and assign them to rotations.
2. Check if there is already a depot layout in the database. If there is not, you may use the
   :func:`generate_depot_layout` function to create a simple depot layout and plan.
3. Either use the :func:`simulate_scenario` function to run the whole simulation in one go, or use the following steps:
    a. Use the :func:`init_simulation` function to create a simulation host, which is a "black box" object containing
       all input data for the simulation.
    b. Use the :func:`run_simulation` function to run the simulation and obtain the results.
    c. Use the :func:`add_evaluation_to_database` function to add the results to the database.
4. For the results to be valid, the consumption simulation should now be run again.
    a. If you are using an external consumption model, run it again making sure it does not create new vehicles.
    b. Run the :func:`simple_consumption_simulation` function again, this time with ``initialize_vehicles=False``.
"""
import copy
import os
import warnings
from datetime import timedelta
from enum import Enum, auto
from math import ceil
from typing import Any, Dict, Optional, Union

import numpy as np
import sqlalchemy.orm
from eflips.model import (
    Area,
    Depot,
    Event,
    EventType,
    Rotation,
    Scenario,
    Trip,
    Vehicle,
    Process,
    AssocAreaProcess,
    Station,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import select

import eflips.depot
from eflips.depot import DepotEvaluation, ProcessStatus, SimulationHost
from eflips.depot.api.private.depot import (
    create_simple_depot,
    delete_depots,
    depot_to_template,
    group_rotations_by_start_end_stop,
)
from eflips.depot.api.private.smart_charging import optimize_charging_events_even
from eflips.depot.api.private.util import (
    create_session,
    repeat_vehicle_schedules,
    start_and_end_times,
    vehicle_type_to_global_constants_dict,
    VehicleSchedule,
)


class SmartChargingStragegy(Enum):
    """Enum class for different smart charging strategies."""

    NONE = auto
    """
    Do not use smart charging.

    Buses are charged with the maximum power available, from the time they arrive at the depot
    until they are full (or leave the depot).
    """
    EVEN = auto
    """
    Use smart charging with an even distribution of charging power over the time the bus is at the depot.

    This aims to
    minimize the peak power demand.
    """
    MIN_PRICE = auto
    """
    Use smart charging in order to minimize the cost of charging.

    The price profile can be specified using the
    PRICE_PROFILE environment variable. If this is not set, the price is loaded using an API.
    """


def simple_consumption_simulation(
    scenario: Union[Scenario, int, Any],
    initialize_vehicles: bool,
    database_url: Optional[str] = None,
    calculate_timeseries: bool = False,
) -> None:
    """
    A simple consumption simulation and vehicle initialization.

    Energy consumotion is calculated by multiplying the vehicle's total distance by a constant
    ``VehicleType.consumption``.

    If run with ``initialize_vehicles=True``, the method will also initialize the vehicles in the database with the
    correct vehicle type and assign them to rotations. If this is false, it will assume that there are already vehicle
    entries and ``Rotation.vehicle_id`` is already set.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
            an integer specifying the ID of a scenario in the database. Or any other object that has an attribute
            ``id`` that is an integer. If no :class:`eflips.model.Scenario` object is passed, the ``database_url``
            parameter must be set to a valid database URL ot the environment variable ``DATABASE_URL`` must be set to a
            valid database URL.
    :param initialize_vehicles: A boolean flag indicating whether the vehicles should be initialized in the database.
            When running this function for the first time, this should be set to True. When running this function again
            after the vehicles have been initialized, this should be set to False.
    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
            :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a
            valid database URL.
    :param calculate_timeseries: A boolean flag indicating whether the timeseries should be calculated. If this is set
            to True, the SoC at each stop is calculated and added to the "timeseries" column of the Event table. If this
            is set to False, the "timeseries" column of the Event table will be set to ``None``. Setting this to false
            may significantly speed up the simulation.
    :return: Nothing. The results are added to the database.
    """
    with create_session(scenario, database_url) as (session, scenario):
        rotations = (
            session.query(Rotation)
            .filter(Rotation.scenario_id == scenario.id)
            .order_by(Rotation.id)
            .options(sqlalchemy.orm.joinedload(Rotation.trips).joinedload(Trip.route))
            .options(sqlalchemy.orm.joinedload(Rotation.vehicle_type))
            .options(sqlalchemy.orm.joinedload(Rotation.vehicle))
        )
        if initialize_vehicles:
            for rotation in rotations:
                vehicle = Vehicle(
                    vehicle_type_id=rotation.vehicle_type_id,
                    scenario_id=scenario.id,
                    name=f"Vehicle for rotation {rotation.id}",
                )
                session.add(vehicle)
                rotation.vehicle = vehicle

                # Additionally, add a short STANDBY event with 100% SoC immediately before the first trip
                first_trip_start = rotation.trips[0].departure_time
                standby_start = first_trip_start - timedelta(seconds=1)
                standby_event = Event(
                    scenario_id=scenario.id,
                    vehicle_type_id=rotation.vehicle_type_id,
                    vehicle=vehicle,
                    station_id=rotation.trips[0].route.departure_station_id,
                    subloc_no=0,
                    time_start=standby_start,
                    time_end=first_trip_start,
                    soc_start=1,
                    soc_end=1,
                    event_type=EventType.CHARGING_OPPORTUNITY,
                    description=f"DUMMY Initial standby event for rotation {rotation.id}.",
                    timeseries=None,
                )
                session.add(standby_event)
        else:
            for rotation in rotations:
                if rotation.vehicle is None:
                    raise ValueError(
                        "The rotation does not have a vehicle assigned to it."
                    )

            vehicles = (
                session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).all()
            )
            for vehicle in vehicles:
                if (
                    session.query(Event).filter(Event.vehicle_id == vehicle.id).count()
                    == 0
                ):
                    # Also add a dummy standby-departure event if this vehicle has no events
                    rotation_per_vehicle = sorted(
                        vehicle.rotations, key=lambda r: r.trips[0].departure_time
                    )
                    earliest_trip = rotation_per_vehicle[0].trips[0]
                    area = (
                        session.query(Area)
                        .filter(Area.scenario_id == scenario.id)
                        .filter(Area.vehicle_type_id == Vehicle.vehicle_type_id)
                        .first()
                    )

                    standby_start = earliest_trip.departure_time - timedelta(seconds=1)
                    standby_event = Event(
                        scenario_id=scenario.id,
                        vehicle_type_id=vehicle.vehicle_type_id,
                        vehicle=vehicle,
                        area_id=area.id,
                        subloc_no=area.capacity,
                        time_start=standby_start,
                        time_end=earliest_trip.departure_time,
                        soc_start=1,
                        soc_end=1,
                        event_type=EventType.STANDBY_DEPARTURE,
                        description=f"DUMMY Initial standby event for rotation {earliest_trip.rotation_id}.",
                        timeseries=None,
                    )
                    session.add(standby_event)

        # Since we are doing no_autoflush blocks later, we need to flush the session once here so that unflushed stuff
        # From preceding functions is visible in the database
        session.flush()

        for rotation in rotations:
            with session.no_autoflush:
                vehicle_type = rotation.vehicle_type
                vehicle = rotation.vehicle
            if vehicle_type.consumption is None:
                raise ValueError(
                    "The vehicle type does not have a consumption value set."
                )
            consumption = vehicle_type.consumption

            # The departure SoC for this rotation is the SoC of the last event preceding the first trip
            with session.no_autoflush:
                current_soc = (
                    session.query(Event.soc_end)
                    .filter(Event.vehicle_id == rotation.vehicle_id)
                    .filter(Event.time_end <= rotation.trips[0].departure_time)
                    .order_by(Event.time_end.desc())
                    .first()[0]
                )

            for trip in rotation.trips:
                # Set up a timeseries
                soc_start = current_soc
                if calculate_timeseries and len(trip.stop_times) > 0:
                    timeseries = {
                        "time": [],
                        "soc": [],
                        "distance": [],
                    }
                    for i in range(len(trip.stop_times)):
                        current_time = trip.stop_times[i].arrival_time
                        dwell_duration = trip.stop_times[i].dwell_duration
                        elapsed_distance = trip.route.assoc_route_stations[
                            i
                        ].elapsed_distance
                        elapsed_energy = consumption * (elapsed_distance / 1000)  # kWh
                        soc = (
                            current_soc - elapsed_energy / vehicle_type.battery_capacity
                        )
                        timeseries["time"].append(current_time.isoformat())
                        timeseries["soc"].append(soc)
                        timeseries["distance"].append(elapsed_distance)
                        if dwell_duration > timedelta(seconds=0):
                            timeseries["time"].append(
                                (current_time + dwell_duration).isoformat()
                            )
                            timeseries["soc"].append(soc)
                            timeseries["distance"].append(elapsed_distance)
                else:
                    timeseries = None
                energy_used = consumption * trip.route.distance / 1000  # kWh
                current_soc = soc_start - energy_used / vehicle_type.battery_capacity

                # Create a driving event
                current_event = Event(
                    scenario_id=scenario.id,
                    vehicle_type_id=rotation.vehicle_type_id,
                    vehicle=vehicle,
                    trip_id=trip.id,
                    time_start=trip.departure_time,
                    time_end=trip.arrival_time,
                    soc_start=soc_start,
                    soc_end=current_soc,
                    event_type=EventType.DRIVING,
                    description=f"`VehicleType.consumption`-based driving event for trip {trip.id}.",
                    timeseries=timeseries,
                )
                session.add(current_event)


def generate_depot_layout(
    scenario: Union[Scenario, int, Any],
    charging_power: float = 150,
    database_url: Optional[str] = None,
    delete_existing_depot: bool = False,
):
    """
    Generates one or more depots for the scenario.

    First, the rotations are scanned to identify all the spots that serve as start *and* end of a rotation. Then the set
    of rotations for these spots are checked for vehicle types that are used there. Next, the amount of vehicles that
    are simultaneously present at the depot is calculated. Then a depot layout with an arrival and a charging area for
    each vehicle type is created. The capacity of each area is taken from the calculated amount of vehicles.
    The depot layout is then added to the database.

    A default plan will also be generated, which includes the following default processes: standby_arrival, cleaning,
    charging and standby_departure. Each vehicle will be processed with this exact order (standby_arrival is optional
    because it only happens if a vehicle needs to wait for the next process).

    The function only deletes the depot if the `delete_existing_depot` parameter is set to True. If there is already a
    depot existing in this scenario and this parameter is set to False, a ValueError will be raised.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
        an integer specifying the ID of a scenario in the database. Or any other object that has an attribute
        ``id`` that is an integer. If no :class:`eflips.model.Scenario` object is passed, the ``database_url``
        parameter must be set to a valid database URL ot the environment variable ``DATABASE_URL`` must be set to a
        valid database URL.
    :param charging_power: the charging power of the charging area in kW
    :param delete_existing_depot: if there is already a depot existing in this scenario, set True to delete this
        existing depot. Set to False and a ValueError will be raised if there is a depot in this scenario.

    :return: None. The depot layout will be added to the database.
    """
    CLEAN_DURATION = 30 * 60  # 30 minutes in seconds

    with create_session(scenario, database_url) as (session, scenario):
        # Handles existing depot
        if session.query(Depot).filter(Depot.scenario_id == scenario.id).count() != 0:
            if delete_existing_depot is False:
                raise ValueError("Depot already exists.")
            delete_depots(scenario, session)

        # Identify all the spots that serve as start *and* end of a rotation
        for (
            first_last_stop_tup,
            vehicle_type_dict,
        ) in group_rotations_by_start_end_stop(scenario.id, session).items():
            first_stop, last_stop = first_last_stop_tup
            if first_stop != last_stop:
                raise ValueError("First and last stop of a rotation are not the same.")
            max_occupancies: Dict[eflips.model.VehicleType, int] = {}
            max_clean_occupancies: Dict[eflips.model.VehicleType, int] = {}
            for vehicle_type, rotations in vehicle_type_dict.items():
                # Slightly convoluted vehicle summation
                start_time = min(
                    [rotation.trips[0].departure_time for rotation in rotations]
                ).timestamp()
                end_time = max(
                    [rotation.trips[-1].arrival_time for rotation in rotations]
                ).timestamp()
                timestamps_to_sample = np.arange(start_time, end_time, 60)
                occupancy = np.zeros_like(timestamps_to_sample)
                clean_occupancy = np.zeros_like(timestamps_to_sample)
                for rotation in rotations:
                    rotation_start = rotation.trips[0].departure_time.timestamp()
                    rotation_end = rotation.trips[-1].arrival_time.timestamp()
                    occupancy += np.interp(
                        timestamps_to_sample,
                        [rotation_start, rotation_end],
                        [1, 1],
                        left=0,
                        right=0,
                    )
                    clean_occupancy += np.interp(
                        timestamps_to_sample,
                        [rotation_end, rotation_end + CLEAN_DURATION],
                        [1, 1],
                        left=0,
                        right=0,
                    )
                max_occupancies[vehicle_type] = max(
                    max(occupancy), 1
                )  # To avoid zero occupancy
                max_clean_occupancies[vehicle_type] = max(max(clean_occupancy), 1)

            # Create a simple depot at this station
            create_simple_depot(
                scenario=scenario,
                station=first_stop,
                charging_capacities=max_occupancies,
                cleaning_capacities=max_clean_occupancies,
                charging_power=charging_power,
                session=session,
                cleaning_duration=timedelta(seconds=CLEAN_DURATION),
            )


def apply_even_smart_charging(
    scenario: Union[Scenario, int, Any],
    database_url: Optional[str] = None,
    standby_departure_duration: timedelta = timedelta(minutes=5),
) -> None:
    """
    Takes a scenario where depot simulation has been run and applies smart charging to the depot.

    This modifies the
    time and power of the charging events in the database. The arrival and departure times and SoCs at these times are
    not modified.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.
    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
        :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a valid database
        URL.
    :param standby_departure_duration: The duration of the STANDBY_DEPARTURE event. This is the time the vehicle is
        allowed to wait at the depot before it has to leave. The default is 5 minutes.
    :return: None. The results are added to the database.
    """
    with create_session(scenario, database_url) as (session, scenario):
        depots = session.query(Depot).filter(Depot.scenario_id == scenario.id).all()
        for depot in depots:
            # Load all the charging events at this depot
            charging_events = (
                session.query(Event)
                .join(Area)
                .filter(Area.depot_id == depot.id)
                .filter(Event.event_type == EventType.CHARGING_DEPOT)
                .all()
            )

            # For each event, take the subsequent STANDBY_DEPARTURE event of the same vehicle
            # Reduce the STANDBY_DEPARTURE events duration to 5 minutes
            # Move the end time of the charging event to the start time of the STANDBY_DEPARTURE event
            for charging_event in charging_events:
                next_event = (
                    session.query(Event)
                    .filter(Event.time_start >= charging_event.time_end)
                    .filter(Event.vehicle_id == charging_event.vehicle_id)
                    .order_by(Event.time_start)
                    .first()
                )
                assert next_event is not None
                assert next_event.event_type == EventType.STANDBY_DEPARTURE
                assert next_event.time_start == charging_event.time_end

                if (
                    next_event.time_end - next_event.time_start
                ) > standby_departure_duration:
                    next_event.time_start = (
                        next_event.time_end - standby_departure_duration
                    )
                    session.flush()
                    # Add a timeseries to the charging event
                    assert charging_event.timeseries is None
                    charging_event.timeseries = {
                        "time": [
                            charging_event.time_start.isoformat(),
                            charging_event.time_end.isoformat(),
                            next_event.time_start.isoformat(),
                        ],
                        "soc": [
                            charging_event.soc_start,
                            charging_event.soc_end,
                            charging_event.soc_end,
                        ],
                    }
                    charging_event.time_end = next_event.time_start
                    session.flush()

            optimize_charging_events_even(charging_events)


def simulate_scenario(
    scenario: Union[Scenario, int, Any],
    repetition_period: Optional[timedelta] = None,
    database_url: Optional[str] = None,
    smart_charging_strategy: SmartChargingStragegy = SmartChargingStragegy.EVEN,
) -> None:
    """
    This method simulates a scenario and adds the results to the database.

    It fills in the "Charging Events" in the :class:`eflips.model.Event` table and associates
    :class:`eflips.model.Vehicle` objects with all the existing "Driving Events" in the :class:`eflips.model.Event`
    table.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
        an integer specifying the ID of a scenario in the database. Or any other object that has an attribute
        ``id`` that is an integer. If no :class:`eflips.model.Scenario` object is passed, the ``database_url``
        parameter must be set to a valid database URL ot the environment variable ``DATABASE_URL`` must be set to a
        valid database URL.
    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. This
        is needed because the result should be a steady-state result. THis can only be achieved by simulating a
        time period before and after our actual simulation, and then only using the "middle". eFLIPS tries to
        automatically detect whether the schedule should be repeated daily or weekly. If this fails, a ValueError is
        raised and repetition needs to be specified manually.
    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
        :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a valid database
        URL.
    :param smart_charging_strategy: An optional parameter specifying the smart charging strategy to be used. The
        default is SmartChargingStragegy.NONE. The following strategies are available:
        - SmartChargingStragegy.NONE: Do not use smart charging. Buses are charged with the maximum power available,
        from the time they arrive at the depot until they are full (or leave the depot).
        - SmartChargingStragegy.EVEN: Use smart charging with an even distribution of charging power over the time the
        bus is at the depot. This aims to minimize the peak power demand.

    :return: Nothing. The results are added to the database.
    """
    with create_session(scenario, database_url) as (session, scenario):
        simulation_host = init_simulation(
            scenario=scenario,
            session=session,
            repetition_period=repetition_period,
        )
        ev = run_simulation(simulation_host)
        add_evaluation_to_database(scenario, ev, session)

    match smart_charging_strategy:
        case SmartChargingStragegy.NONE:
            pass
        case SmartChargingStragegy.EVEN:
            apply_even_smart_charging(scenario, database_url)
        case SmartChargingStragegy.MIN_PRICE:
            raise NotImplementedError("MIN_PRICE strategy is not implemented yet.")
        case _:
            raise NotImplementedError()


def _init_simulation(
    scenario: Scenario,
    session: Session,
    repetition_period: Optional[timedelta] = None,
    vehicle_count_dict: Optional[Dict[str, int]] = None,
) -> SimulationHost:
    """Deprecated stub for init_simulation."""
    raise NotImplementedError(
        "The function _init_simulation is deprecated. Please use init_simulation instead."
    )


def init_simulation(
    scenario: Scenario,
    session: Session,
    repetition_period: Optional[timedelta] = None,
    vehicle_count_dict: Optional[Dict[str, Dict[str, int]]] = None,
) -> SimulationHost:
    """
    This methods checks the input data for consistency, initializes a simulation host object and returns it.

    The simulation host object can then be passed to :func:`run_simulation()`.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.
    :param session: A SQLAlchemy session object.
    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. This
        is needed because the *result* should be a steady-state result. THis can only be achieved by simulating a
        time period before and after our actual simulation, and then only using the "middle". eFLIPS tries to
        automatically detect whether the schedule should be repeated daily or weekly. If this fails, a ValueError is
        raised and repetition needs to be specified manually.

    :param vehicle_count_dict: An optional dictionary specifying the number of vehicles for each vehicle type for each
         depot. The dictionary should have the following structure:

         ::

            {
                "1" (depot.id as str): {
                    "1" (vehicle_type.id as str): 10,
                    "2" (vehicle_type.id as str): 20,
                    ...
                },
                "2" (depot.id as str): {
                    "1" (vehicle_type.id as str): 10,
                    "2" (vehicle_type.id as str): 20,
                    ...
                },

    :return: A :class:`eflips.depot.Simulation.SimulationHost` object. This object should be reagrded as a "black box"
        by the user. It should be passed to :func:`run_simulation()` to run the simulation and obtain the results.
    """
    # Clear the eflips settings
    eflips.settings.reset_settings()

    path_to_this_file = os.path.dirname(__file__)

    # Step 1: Set up the depot
    eflips_depots = []
    for depot in scenario.depots:
        depot_dict = depot_to_template(depot)
        eflips_depots.append(
            eflips.depot.Depotinput(filename_template=depot_dict, show_gui=False)
        )

    # Step 2: eFLIPS initialization
    simulation_host = SimulationHost(eflips_depots, print_timestamps=False)
    # Now we do what is done in `standard_setup()` from the old input files
    # Load the settings
    path_to_default_settings = os.path.join(
        path_to_this_file, "defaults", "default_settings"
    )
    eflips.load_settings(path_to_default_settings)

    # Step 3: Set up the vehicle schedules
    # Turn rotations into vehicleschedules
    # Turn API VehicleSchedule objects into eFLIPS TimeTable object
    # Get correctly repeated vehicle schedules
    # if total duration time is 1 or 2 days, vehicle schedule will be repeated daily
    # if total duration time is 7 or 8 days, vehicle schedule will be repeated weekly
    # However, we need to override quite a lot of the settings
    # The ["general"]["SIMULATION_TIME"] entry is calculated from the difference between the first and last departure
    # time in the vehicle schedule
    vehicle_schedules = [
        VehicleSchedule.from_rotation(
            rotation,
            scenario=scenario,
            session=session,
        )
        for rotation in scenario.rotations
    ]

    first_departure_time = min(
        [vehicle_schedule.departure for vehicle_schedule in vehicle_schedules]
    )
    last_arrival_time = max(
        [vehicle_schedule.arrival for vehicle_schedule in vehicle_schedules]
    )

    # We take first arrival time as simulation start
    total_duration = (last_arrival_time - first_departure_time).total_seconds()
    schedule_duration_days = ceil(total_duration / (24 * 60 * 60))

    if repetition_period is None and schedule_duration_days in [1, 2]:
        repetition_period = timedelta(days=1)
    elif repetition_period is None and schedule_duration_days in [7, 8]:
        repetition_period = timedelta(weeks=1)
    elif repetition_period is None:
        raise ValueError(
            "Could not automatically detect repetition period. Please specify manually."
        )

    # Now, we need to repeat the vehicle schedules

    vehicle_schedules = repeat_vehicle_schedules(vehicle_schedules, repetition_period)

    sim_start_stime, total_duration_seconds = start_and_end_times(vehicle_schedules)
    eflips.globalConstants["general"]["SIMULATION_TIME"] = int(total_duration_seconds)

    eflips.globalConstants["general"]["SIMULATION_START_DATETIME"] = sim_start_stime

    timetable = VehicleSchedule._to_timetable(
        vehicle_schedules, simulation_host.env, sim_start_stime
    )
    simulation_host.timetable = timetable

    # Step 4: Set up the vehicle types
    # We need to calculate roughly how many vehicles we need for each depot
    for depot in scenario.depots:
        depot_id = str(depot.id)
        eflips.globalConstants["depot"]["vehicle_count"][depot_id] = {}
        vehicle_types_for_depot = set(str(area.vehicle_type_id) for area in depot.areas)

        # If we have a vehicle count dictionary, we validate and use ir
        if vehicle_count_dict is not None and depot_id in vehicle_count_dict.keys():
            if vehicle_count_dict[depot_id].keys() != vehicle_types_for_depot:
                raise ValueError(
                    "The vehicle count dictionary does not contain all vehicle types for depot {depot_id}."
                )
            eflips.globalConstants["depot"]["vehicle_count"][
                depot_id
            ] = vehicle_count_dict[depot_id]
        else:
            # Calculate it from the size of the areas (except the area for the first standby process, which is already
            # really large), with a 2x margin
            for vehicle_type in vehicle_types_for_depot:
                vehicle_count = sum(
                    [
                        area.capacity
                        for area in depot.areas
                        if area.vehicle_type_id == int(vehicle_type)
                        and depot.default_plan.processes[0] not in area.processes
                    ]
                )
                eflips.globalConstants["depot"]["vehicle_count"][depot_id][
                    vehicle_type
                ] = (vehicle_count * 2)

    # We  need to put the vehicle type objects into the GlobalConstants
    for vehicle_type in scenario.vehicle_types:
        eflips.globalConstants["depot"]["vehicle_types"][
            str(vehicle_type.id)
        ] = vehicle_type_to_global_constants_dict(vehicle_type)

    # Step 5: Final checks and setup
    # Run the eflips validity checks
    eflips.depot.settings_config.check_gc_validity()

    # Complete the eflips settings
    eflips.depot.settings_config.complete_gc()

    # Set up the depots
    for dh, di in zip(simulation_host.depot_hosts, simulation_host.to_simulate):
        dh.load_and_complete_template(di.filename_template)

    simulation_host.complete()

    return simulation_host


def _run_simulation(simulation_host: SimulationHost) -> DepotEvaluation:
    """Deprecated stub for run_simulation."""
    raise NotImplementedError(
        "The function _run_simulation is deprecated. Please use run_simulation instead."
    )


def run_simulation(simulation_host: SimulationHost) -> Dict[str, DepotEvaluation]:
    """Run simulation and return simulation results.

    :param simulation_host: A "black box" object containing all input data for the simulation.

    :return: A dictionary of :class:`eflips.depot.evaluation.DepotEvaluation` objects. The keys are the depot IDs, as
        strings.
    """
    simulation_host.run()

    results = {}
    for depot_host in simulation_host.depot_hosts:
        depot_id = depot_host.depot.ID
        ev = depot_host.evaluation

        # We need to clean up the timetable, it has trips from all depots
        ev.timetable = copy.copy(ev.timetable)
        ev.timetable.trips = [
            t for t in ev.timetable.trips if t.destination.ID == depot_id
        ]
        ev.timetable.trips_issued = [
            t for t in ev.timetable.trips_issued if t.destination.ID == depot_id
        ]
        ev.timetable.all_trips = [
            t for t in ev.timetable.all_trips if t.destination.ID == depot_id
        ]

        # We also need to clean up the vehicle generator
        ev.vehicle_generator = copy.copy(ev.vehicle_generator)
        ev.vehicle_generator.items = copy.copy(ev.vehicle_generator.items)
        indizes_to_remove = []
        for i in range(len(ev.vehicle_generator.items)):
            vehicle = copy.copy(ev.vehicle_generator.items[i])
            depot_ids = [trip.destination.ID for trip in vehicle.finished_trips]
            if len(set(depot_ids)) > 1:
                raise ValueError("Vehicle has finished trips in multiple depots.")
            if len(depot_ids) == 0 or depot_ids[0] != depot_id:
                indizes_to_remove.append(i)

        for index in sorted(indizes_to_remove, reverse=True):
            del ev.vehicle_generator.items[index]

        results[depot_id] = ev

    return results


def _add_evaluation_to_database(
    scenario_id: int,
    depot_evaluation: DepotEvaluation,
    session: sqlalchemy.orm.Session,
) -> None:
    """Deprecated stub for add_evaluation_to_database."""
    raise NotImplementedError(
        "The function _add_evaluation_to_database is deprecated. Please use add_evaluation_to_database instead."
    )


def add_evaluation_to_database(
    scenario: Scenario,
    depot_evaluations: Dict[str, DepotEvaluation],
    session: sqlalchemy.orm.Session,
) -> None:
    """
    This method adds a simulation result to the database.

    It reads the simulation results from the :class:`eflips.depot.evaluation.DepotEvaluation` object and  adds them into
     the database. Tables of Event, Rotation and Vehicle will be updated.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.

    :param depot_evaluations: A dictionary of :class:`eflips.depot.evaluation.DepotEvaluation` objects. The keys are
             the depot IDs, as strings.

    :param session: a SQLAlchemy session object. This is used to add all the simulation results to the
           database.

    :return: Nothing. The results are added to the database.
    """

    # Read simulation start time

    for depot_evaluation in depot_evaluations.values():
        simulation_start_time = depot_evaluation.sim_start_datetime

        # Initialization of empty lists

        list_of_assigned_schedules = []

        # Read results from depot_evaluation categorized by vehicle
        for current_vehicle in depot_evaluation.vehicle_generator.items:
            vehicle_type_id = int(current_vehicle.vehicle_type.ID)

            # Create a Vehicle object for database
            current_vehicle_db = Vehicle(
                vehicle_type_id=vehicle_type_id,
                scenario=scenario,
                name=current_vehicle.ID,
                name_short=None,
            )
            # Flush the vehicle object to get the vehicle id
            session.add(current_vehicle_db)
            session.flush()

            dict_of_events = {}

            # Generate process log for each
            list_of_finished_trips = current_vehicle.finished_trips
            list_of_finished_trips.sort(key=lambda x: x.atd)

            for i in range(len(list_of_finished_trips)):
                if list_of_finished_trips[i].is_copy is False:
                    current_trip = list_of_finished_trips[i]

                    # Add all non-copy trips to the dictionary
                    dict_of_events[current_trip.atd] = {
                        "type": "Trip",
                        "end": current_trip.ata,
                        "is_copy": current_trip.is_copy,
                        "id": current_trip.ID,
                    }

                    # Match current trip to its serving vehicle
                    assigned_schedule_id = int(current_trip.ID)
                    list_of_assigned_schedules.append(
                        (assigned_schedule_id, current_vehicle_db.id)
                    )
                    # Also add two copy trips before and after the non-copy trip as "time boarders" for the depot process
                    try:
                        if list_of_finished_trips[i + 1].is_copy is True:
                            dict_of_events[list_of_finished_trips[i + 1].atd] = {
                                "type": "Trip",
                                "end": list_of_finished_trips[i + 1].ata,
                                "is_copy": list_of_finished_trips[i + 1].is_copy,
                                "id": list_of_finished_trips[i + 1].ID,
                            }

                        if list_of_finished_trips[i - 1].is_copy is True:
                            dict_of_events[list_of_finished_trips[i - 1].atd] = {
                                "type": "Trip",
                                "end": list_of_finished_trips[i - 1].ata,
                                "is_copy": list_of_finished_trips[i - 1].is_copy,
                                "id": list_of_finished_trips[i - 1].ID,
                            }

                    except IndexError:
                        # In case there are no copy trips before or after the non-copy trip
                        continue

            # The range of time of events to be generated. It is between the copy trip before the first non-copy trip
            # and the copy trip after the last non-copy trip
            earliest_time = sorted(dict_of_events.keys())[0]
            latest_time = sorted(dict_of_events.keys())[-1]

            last_standby_departure_start = 0

            # For convenience
            area_log = current_vehicle.logger.loggedData["dwd.current_area"]
            slot_log = current_vehicle.logger.loggedData["dwd.current_slot"]

            waiting_log = current_vehicle.logger.loggedData["area_waiting_time"]
            battery_log = current_vehicle.battery_logs

            # Create standby events according to waiting_log
            waiting_log_timekeys = sorted(waiting_log.keys())

            for idx in range(len(waiting_log_timekeys)):
                end_time = waiting_log_timekeys[idx]

                # Only extract events if the time is within the upper mentioned range

                if earliest_time < end_time < latest_time:
                    waiting_info = waiting_log[end_time]

                    if waiting_info["waiting_time"] == 0:
                        continue

                    # Vehicle is waiting in the last area in waiting_log and expecting to enter the current area
                    expected_area = waiting_info["area"]
                    # Find the area for standby arrival event

                    # Get the corresponding depot id first by grabbing one of the rotations and get its departure station
                    some_rotation_id = current_vehicle.finished_trips[0].ID
                    some_rotation = (
                        session.query(Rotation)
                        .filter(Rotation.id == some_rotation_id)
                        .one()
                    )
                    start_station = some_rotation.trips[0].route.departure_station_id
                    depot_id = (
                        session.query(Depot.id)
                        .join(Station)
                        .filter(Station.id == start_station)
                        .one()[0]
                    )

                    waiting_area_id = (
                        session.query(Area.id)
                        .join(AssocAreaProcess, AssocAreaProcess.area_id == Area.id)
                        .join(Process, Process.id == AssocAreaProcess.process_id)
                        .filter(
                            Process.dispatchable == False,
                            # Must use "==" instead of "is". Or it would be recongnize as a python statement rather than a SQL statement
                            Process.duration.is_(None),
                            Process.electric_power.is_(None),
                            Area.vehicle_type_id
                            == int(current_vehicle.vehicle_type.ID),
                            Area.scenario_id == scenario.id,
                            Area.depot_id == depot_id,
                        )
                        .one()[0]
                    )

                    # Make sure the vehicle is waiting at an area with enough capacity

                    current_slot = slot_log[waiting_log_timekeys[idx - 1]]

                    start_time = end_time - waiting_info["waiting_time"]

                    warnings.warn(
                        f"Vehicle {current_vehicle.ID} is waiting at {waiting_area_id} because area {expected_area} is full."
                    )

                    dict_of_events[start_time] = {
                        "type": "Standby",
                        "end": end_time,
                        "area": waiting_area_id,
                        "slot": current_slot,
                        "is_area_sink": False,
                    }

            # Create a list of battery log in order of time asc. Convenient for looking up corresponding soc
            battery_log_list = []
            for log in battery_log:
                battery_log_list.append((log.t, log.energy / log.energy_real))

            for start_time, process_log in current_vehicle.logger.loggedData[
                "dwd.active_processes_copy"
            ].items():
                if earliest_time < start_time < latest_time:
                    if len(process_log) == 0:
                        # A departure happens
                        if last_standby_departure_start != 0:
                            # Update the last standby-departure end time
                            dict_of_events[last_standby_departure_start][
                                "end"
                            ] = start_time
                        else:
                            continue

                    else:
                        for process in process_log:
                            match process.status:
                                case ProcessStatus.COMPLETED | ProcessStatus.CANCELLED:
                                    assert (
                                        len(process.starts) == 1
                                        and len(process.ends) == 1
                                    ), (
                                        f"Current process {process.ID} is completed and should only contain one start and "
                                        f"one end time."
                                    )
                                    current_area = area_log[start_time]
                                    current_slot = slot_log[start_time]

                                    if current_area is None or current_slot is None:
                                        raise ValueError(
                                            f"For process {process.ID} Area and slot should not be None."
                                        )

                                    if process.dur > 0:
                                        # Valid duration
                                        dict_of_events[start_time] = {
                                            "type": type(process).__name__,
                                            "end": process.ends[0],
                                            "area": current_area.ID,
                                            "slot": current_slot,
                                            "id": process.ID,
                                        }
                                    else:
                                        # Duration is 0
                                        if current_area.issink is True:
                                            # Standby departure
                                            if start_time in dict_of_events:
                                                # Actual start time should be the end time of the other positive
                                                # duration process starting at the same time
                                                actual_start_time = dict_of_events[
                                                    start_time
                                                ]["end"]
                                            else:
                                                for other_process in process_log:
                                                    if (
                                                        other_process.dur > 0
                                                        and len(other_process.ends) != 0
                                                    ):
                                                        actual_start_time = (
                                                            other_process.ends[0]
                                                        )
                                                    else:
                                                        # Invalid standby before a process in progress will be ignored
                                                        continue

                                            last_standby_departure_start = (
                                                actual_start_time
                                            )

                                            # If this standby event lasts actually 0 seconds, it is not a real event
                                            if (
                                                actual_start_time
                                                in dict_of_events.keys()
                                                and dict_of_events[actual_start_time][
                                                    "type"
                                                ]
                                                == "Trip"
                                            ):
                                                continue
                                            dict_of_events[actual_start_time] = {
                                                "type": type(process).__name__,
                                                "area": current_area.ID,
                                                "is_area_sink": current_area.issink,
                                                "slot": current_slot,
                                                "id": process.ID,
                                            }

                                        else:
                                            # Standby arrival
                                            assert current_area.issink is False, (
                                                f"A bus cannot go from Area {current_area.ID} to other areas. A Parking Area"
                                                f" for standby arrival should be added."
                                            )
                                            dict_of_events[start_time] = {
                                                "type": type(process).__name__,
                                                "area": current_area.ID,
                                                "is_area_sink": current_area.issink,
                                                "slot": current_slot,
                                                "id": process.ID,
                                            }
                                case ProcessStatus.IN_PROGRESS:
                                    assert (
                                        len(process.starts) == 1
                                        and len(process.ends) == 0
                                    ), f"Current process {process.ID} is marked IN_PROGRESS, but has an end."
                                    current_area = area_log[start_time]
                                    current_slot = slot_log[start_time]

                                    if current_area is None or current_slot is None:
                                        raise ValueError(
                                            f"For process {process.ID} Area and slot should not be None."
                                        )

                                    if process.dur > 0:
                                        # Valid duration
                                        dict_of_events[start_time] = {
                                            "type": type(process).__name__,
                                            "end": process.etc,
                                            "area": current_area.ID,
                                            "slot": current_slot,
                                            "id": process.ID,
                                        }
                                    else:
                                        raise NotImplementedError(
                                            "We believe this should never happen. If it happens, handle it here."
                                        )
                                case ProcessStatus.WAITING:
                                    raise NotImplementedError(
                                        f"Current process {process.ID} is waiting. Not implemented yet."
                                    )

                                case ProcessStatus.NOT_STARTED:
                                    raise NotImplementedError(
                                        f"Current process {process.ID} is not started. Not implemented yet."
                                    )

                                case _:
                                    raise ValueError(
                                        f"Invalid process status {process.status} for process {process.ID}."
                                    )

            # Reverse the time keys to make generation of events before the trip easier
            time_keys = sorted(dict_of_events.keys())
            if len(time_keys) != 0:
                # Generating valid event-list

                for start_time in time_keys:
                    process_dict = dict_of_events[start_time]

                    # Generate EventType
                    match process_dict["type"]:
                        case "Serve":
                            event_type = EventType.SERVICE
                        case "Charge":
                            event_type = EventType.CHARGING_DEPOT
                        case "Standby":
                            if process_dict["is_area_sink"] is True:
                                event_type = EventType.STANDBY_DEPARTURE
                            else:
                                event_type = EventType.STANDBY
                        case "Precondition":
                            event_type = EventType.PRECONDITIONING
                        case "Trip":
                            continue
                        case _:
                            raise ValueError(
                                'Invalid process type %s. Valid process types are "Serve", "Charge", '
                                '"Standby", "Precondition"'
                            )

                    # End time of 0-duration processes are start time of the next process

                    if "end" not in process_dict:
                        # End time will be the one time key "later"
                        end_time = time_keys[time_keys.index(start_time) + 1]

                        process_dict["end"] = end_time

                    # Get soc
                    soc_start = None
                    soc_end = None

                    for i in range(len(battery_log_list)):
                        # Access the correct battery log according to time since there is only one battery log for each time
                        log = battery_log_list[i]

                        if log[0] == start_time:
                            soc_start = log[1]
                        if log[0] == process_dict["end"]:
                            soc_end = log[1]
                        if log[0] < start_time < battery_log_list[i + 1][0]:
                            soc_start = log[1]
                        if log[0] < process_dict["end"] < battery_log_list[i + 1][0]:
                            soc_end = log[1]

                    current_event = Event(
                        scenario=scenario,
                        vehicle_type_id=vehicle_type_id,
                        vehicle=current_vehicle_db,
                        station_id=None,
                        area_id=int(process_dict["area"]),
                        subloc_no=int(process_dict["slot"]),
                        trip_id=None,
                        time_start=timedelta(seconds=start_time)
                        + simulation_start_time,
                        time_end=timedelta(seconds=process_dict["end"])
                        + simulation_start_time,
                        soc_start=soc_start if soc_start is not None else soc_end,
                        soc_end=soc_end
                        if soc_end is not None
                        else soc_start,  # if only one battery log is found,
                        # then this is not an event with soc change
                        event_type=event_type,
                        description=None,
                        timeseries=None,
                    )

                    session.add(current_event)

                # For non-copy schedules with no predecessor events, adding a dummy standby-departure
                if (
                    dict_of_events[time_keys[0]]["type"] == "Trip"
                    and dict_of_events[time_keys[0]]["is_copy"] is False
                ):
                    standby_start = time_keys[0] - 1
                    standby_end = time_keys[0]
                    rotation_id = int(dict_of_events[time_keys[0]]["id"])
                    area = (
                        session.query(Area)
                        .filter(Area.vehicle_type_id == vehicle_type_id)
                        .first()
                    )

                    first_trip = (
                        session.query(Trip)
                        .filter(Trip.rotation_id == rotation_id)
                        .order_by(Trip.departure_time)
                        .first()
                    )

                    soc = (
                        session.query(Event.soc_end)
                        .filter(Event.scenario == scenario)
                        .filter(Event.trip_id == first_trip.id)
                        .first()[0]
                    )

                    standby_event = Event(
                        scenario=scenario,
                        vehicle_type_id=vehicle_type_id,
                        vehicle=current_vehicle_db,
                        station_id=None,
                        area_id=area.id,
                        subloc_no=area.capacity,
                        trip_id=None,
                        time_start=timedelta(seconds=standby_start)
                        + simulation_start_time,
                        time_end=timedelta(seconds=standby_end) + simulation_start_time,
                        soc_start=soc,
                        soc_end=soc,
                        event_type=EventType.STANDBY_DEPARTURE,
                        description=f"DUMMY Standby event for {rotation_id}.",
                        timeseries=None,
                    )

                    session.add(standby_event)

        # New rotation assignment
        for schedule_id, vehicle_id in list_of_assigned_schedules:
            # Get corresponding old vehicle id
            session.query(Rotation).filter(Rotation.id == schedule_id).update(
                {"vehicle_id": vehicle_id}, synchronize_session="auto"
            )

        # Delete all non-depot events
        session.query(Event).filter(
            Event.scenario == scenario,
            Event.trip_id.isnot(None) | Event.station_id.isnot(None),
        ).delete(synchronize_session="auto")

        session.flush()

        # Delete all vehicles without rotations
        vehicle_assigned_sq = (
            session.query(Rotation.vehicle_id)
            .filter(Rotation.scenario == scenario)
            .distinct()
            .subquery()
        )

        session.query(Vehicle).filter(Vehicle.scenario == scenario).filter(
            Vehicle.id.not_in(select(vehicle_assigned_sq))
        ).delete()

        session.flush()
