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
import datetime
import itertools
import logging
import os
import warnings
from collections import OrderedDict
from datetime import timedelta
from enum import Enum
from math import ceil
from typing import Any, Dict, Optional, Union, List

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
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import select

import eflips.depot
from eflips.depot import DepotEvaluation, ProcessStatus, SimulationHost, SimpleVehicle
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
    check_depot_validity,
)


class SmartChargingStrategy(Enum):
    """Enum class for different smart charging strategies."""

    NONE = 0
    """
    Do not use smart charging.

    Buses are charged with the maximum power available, from the time they arrive at the depot
    until they are full (or leave the depot).
    """
    EVEN = 1
    """
    Use smart charging with an even distribution of charging power over the time the bus is at the depot.

    This aims to
    minimize the peak power demand.
    """
    MIN_PRICE = 2
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
    logger = logging.getLogger(__name__)

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
            rotation: Rotation
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

                # If the vehicle is
                #  - Capable of opportunity charging
                #  - On a Rotation which allows opportunity charging
                #  - Currently at a station which allows opportunity charging
                #  - which is not the last trip of the rotation
                #  We add a charging event

                if (
                    rotation.vehicle_type.opportunity_charging_capable
                    and rotation.allow_opportunity_charging
                    and trip.route.arrival_station.is_electrified
                    and trip != rotation.trips[-1]
                ):
                    logger.debug(
                        f"Adding opportunity charging event for trip {trip.id}"
                    )
                    # Identify the break time between trips
                    trip_index = rotation.trips.index(trip)
                    next_trip = rotation.trips[trip_index + 1]
                    break_time = next_trip.departure_time - trip.arrival_time

                    # How much energy can be charged in this time?
                    energy_charged = (
                        max([v[1] for v in vehicle_type.charging_curve])
                        * break_time.total_seconds()
                        / 3600
                    )

                    if energy_charged > 0:
                        # Calculate the end SoC
                        post_charge_soc = min(
                            current_soc
                            + energy_charged / vehicle_type.battery_capacity,
                            1,
                        )

                        # Create the charging event
                        current_event = Event(
                            scenario_id=scenario.id,
                            vehicle_type_id=rotation.vehicle_type_id,
                            vehicle=vehicle,
                            station_id=trip.route.arrival_station_id,
                            time_start=trip.arrival_time,
                            time_end=next_trip.departure_time,
                            soc_start=current_soc,
                            soc_end=post_charge_soc,
                            event_type=EventType.CHARGING_OPPORTUNITY,
                            description=f"Opportunity charging event for trip {trip.id}.",
                            timeseries=None,
                        )
                        current_soc = post_charge_soc
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
                safety_margin=0.2,
            )


def apply_even_smart_charging(
    scenario: Union[Scenario, int, Any],
    database_url: Optional[str] = None,
    standby_departure_duration: timedelta = timedelta(minutes=5),
) -> None:
    """
    Takes a scenario where depot simulation has been run and applies smart charging to the depot.

    This modifies the time and power of the charging events in the database. The arrival and departure times and SoCs at
    these times are not modified.

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

                if (
                    next_event is None
                    or next_event.event_type != EventType.STANDBY_DEPARTURE
                ):
                    warnings.warn(
                        f"Event {charging_event.id} has no STANDBY_DEPARTURE event after a CHARGING_DEPOT "
                        f"event. No room for smart charging."
                    )
                    continue

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
    smart_charging_strategy: SmartChargingStrategy = SmartChargingStrategy.EVEN,
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
            case SmartChargingStrategy.NONE:
                pass
            case SmartChargingStrategy.EVEN:
                apply_even_smart_charging(scenario, database_url)
            case SmartChargingStrategy.MIN_PRICE:
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
        # Step 1.5: Check validity of a depot
        check_depot_validity(depot)

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
        if "None" in vehicle_types_for_depot:
            vehicle_types_for_depot.remove("None")

        # If we have a vehicle count dictionary, we validate and use ir
        if vehicle_count_dict is not None and depot_id in vehicle_count_dict.keys():
            if set(vehicle_count_dict[depot_id].keys()) < vehicle_types_for_depot:
                raise ValueError(
                    "The vehicle count dictionary does not contain all vehicle types for depot {depot_id}."
                )
            eflips.globalConstants["depot"]["vehicle_count"][
                depot_id
            ] = vehicle_count_dict[depot_id]
        else:
            # Calculate it from the size of the charging area with a 2x margin

            for vehicle_type in vehicle_types_for_depot:
                vehicle_count = 0
                for area in depot.areas:
                    if area.vehicle_type_id == int(vehicle_type):
                        # TODO potential edit if we make vehicle type of an area a list
                        for p in area.processes:
                            if p.electric_power is not None and p.duration is None:
                                vehicle_count = area.capacity

                assert (
                    vehicle_count > 0
                ), f"The charging area capacity for vehicle type {vehicle_type} should not be 0."

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

    for depot_id, depot_evaluation in depot_evaluations.items():
        simulation_start_time = depot_evaluation.sim_start_datetime

        # Depot-layer operations

        list_of_assigned_schedules = []

        waiting_area_id = None

        total_areas = scenario.areas
        for area in total_areas:
            if area.depot_id == int(depot_id) and len(area.processes) == 0:
                waiting_area_id = area.id

        assert isinstance(waiting_area_id, int) and waiting_area_id > 0, (
            f"Waiting area id should be an integer greater than 0. For every depot there must be at least "
            f"one waiting area."
        )

        for current_vehicle in depot_evaluation.vehicle_generator.items:
            # Vehicle-layer operations

            vehicle_type_id = int(current_vehicle.vehicle_type.ID)

            current_vehicle_db = Vehicle(
                vehicle_type_id=vehicle_type_id,
                scenario=scenario,
                name=current_vehicle.ID,
                name_short=None,
            )

            # Flush the vehicle object to get the vehicle id
            session.add(current_vehicle_db)
            session.flush()

            dict_of_events = OrderedDict()

            (
                schedule_current_vehicle,
                earliest_time,
                latest_time,
                # Earliest and latest time defines a time window, only the events within this time window will be
                # handled. It is usually the departure time of the last copy trip in the "early-shifted" copy
                # schedules and the departure time of the first copy trip in the "late-shifted" copy schedules.
            ) = _get_finished_schedules_per_vehicle(
                dict_of_events, current_vehicle.finished_trips, current_vehicle_db.id
            )

            try:
                assert earliest_time is not None and latest_time is not None

            except AssertionError as e:
                warnings.warn(
                    f"Vehicle {current_vehicle_db.id} has only copied trips. The profiles of this vehicle "
                    f"will not be written into database."
                )
                continue

            assert (
                earliest_time < latest_time
            ), f"Earliest time {earliest_time} is not less than latest time {latest_time}."

            list_of_assigned_schedules.extend(schedule_current_vehicle)

            _generate_vehicle_events(
                dict_of_events,
                current_vehicle,
                waiting_area_id,
                earliest_time,
                latest_time,
            )

            # Python passes dictionaries by reference

            _complete_standby_departure_events(dict_of_events, latest_time)

            _add_soc_to_events(dict_of_events, current_vehicle.battery_logs)

            try:
                assert (not dict_of_events) is False
            except AssertionError as e:
                warnings.warn(
                    f"Vehicle {current_vehicle_db.id} has no valid events. The vehicle will not be written "
                    f"into database."
                )

                continue

            _add_events_into_database(
                current_vehicle_db,
                dict_of_events,
                session,
                scenario,
                simulation_start_time,
            )

        # Postprocessing of events
        _update_vehicle_in_rotation(session, scenario, list_of_assigned_schedules)
        _update_waiting_events(session, scenario, waiting_area_id)


def _get_finished_schedules_per_vehicle(
    dict_of_events, list_of_finished_trips: List, db_vehicle_id: int
):
    """
    This function completes the following tasks:

    1. It gets the finished non-copy schedules of the current vehicle,
    which will be used in :func:`_update_vehicle_in_rotation()`.

    2. It fills the dictionary of events with the trip_ids of the current vehicle.

    3. It returns an earliest and a latest time according to this vehicle's schedules. Only processes happening within
    this time window will be handled later.

    Usually the earliest time is the departure time of the last copy trip in the "early-shifted" copy schedules
    and the lastest time is the departure time of the first copy trip in the "late-shifted" copy schedules.

    # If the vehicle's first trip is a non-copy trip, the earliest time is the departure time of the first trip. If the
    # vehicle's last trip is a non-copy trip, the latest time is the departure time of the last trip.

    :param dict_of_events: An ordered dictionary storing the data related to an event. The keys are the start times of
        the events.
    :param list_of_finished_trips: A list of finished trips of a vehicle directly from
        :class:`eflips.depot.simple_vehicle.SimpleVehicle` object.

    :param db_vehicle_id: The vehicle id in the database.

    :return: A tuple of three elements. The first element is a list of finished schedules of the vehicle. The second and
        third elements are the earliest and latest time of the vehicle's schedules.
    """
    finished_schedules = []

    list_of_finished_trips.sort(key=lambda x: x.atd)
    earliest_time = None
    latest_time = None

    for i in range(len(list_of_finished_trips)):
        assert list_of_finished_trips[i].atd == list_of_finished_trips[i].std, (
            "The trip {current_trip.ID} is delayed. The simulation doesn't "
            "support delayed trips for now."
        )

        if list_of_finished_trips[i].is_copy is False:
            current_trip = list_of_finished_trips[i]

            finished_schedules.append((int(current_trip.ID), db_vehicle_id))
            dict_of_events[current_trip.atd] = {
                "type": "Trip",
                "id": int(current_trip.ID),
            }

            if i == 0:
                earliest_time = current_trip.atd

            if i == len(list_of_finished_trips) - 1:
                latest_time = current_trip.atd

            if i != 0 and list_of_finished_trips[i - 1].is_copy is True:
                earliest_time = list_of_finished_trips[i - 1].atd

            if (
                i != len(list_of_finished_trips) - 1
                or list_of_finished_trips[i + 1].is_copy is True
            ):
                latest_time = list_of_finished_trips[i + 1].atd

    return finished_schedules, earliest_time, latest_time


def _generate_vehicle_events(
    dict_of_events,
    current_vehicle: SimpleVehicle,
    virtual_waiting_area_id: int,
    earliest_time: datetime.datetime,
    latest_time: datetime.datetime,
) -> None:
    """
    This function generates and ordered dictionary storing the data related to an event.

    It returns a dictionary. The keys are the start times of the
    events. The values are also dictionaries containing:
    - type: The type of the event.
    - end: The end time of the event.
    - area: The area id of the event.
    - slot: The slot id of the event.
    - id: The id of the event-related process.

    For trips, only the type is stored.

    For waiting events, the slot is not stored for now.

    :param current_vehicle: a :class:`eflips.depot.simple_vehicle.SimpleVehicle` object.

    :param virtual_waiting_area_id: the id of the virtual waiting area. Vehicles waiting for the first process will park here.

    :param earliest_time: the earliest relevant time of the current vehicle. Any events earlier than this will not be
        handled.

    :param latest_time: the latest relevant time of the current vehicle. Any events later than this will not be handled.

    :return: None. The results are added to the dictionary.
    """

    # For convenience
    area_log = current_vehicle.logger.loggedData["dwd.current_area"]
    slot_log = current_vehicle.logger.loggedData["dwd.current_slot"]
    waiting_log = current_vehicle.logger.loggedData["area_waiting_time"]

    # Handling waiting events
    waiting_log_timekeys = sorted(waiting_log.keys())

    for idx in range(len(waiting_log_timekeys)):
        waiting_end_time = waiting_log_timekeys[idx]

        # Only extract events if the time is within the upper mentioned range

        if earliest_time <= waiting_end_time <= latest_time:
            waiting_info = waiting_log[waiting_end_time]

            if waiting_info["waiting_time"] == 0:
                continue

            warnings.warn(
                f"Vehicle {current_vehicle.ID} has been waiting for {waiting_info['waiting_time']} seconds. "
            )

            start_time = waiting_end_time - waiting_info["waiting_time"]

            if waiting_info["area"] == waiting_log[waiting_log_timekeys[0]]["area"]:
                # if the vehicle is waiting for the first process, put it in the virtual waiting area
                waiting_area_id = virtual_waiting_area_id
            else:
                # If the vehicle is waiting for other processes,
                # put it in the area of the prodecessor process of the waited process.
                waiting_area_id = waiting_log[waiting_log_timekeys[idx - 1]]["area"]

            dict_of_events[start_time] = {
                "type": "Standby",
                "end": waiting_end_time,
                "area": waiting_area_id,
                "is_waiting": True,
            }

    # Create a list of battery log in order of time asc. Convenient for looking up corresponding soc

    for time_stamp, process_log in current_vehicle.logger.loggedData[
        "dwd.active_processes_copy"
    ].items():
        if earliest_time <= time_stamp <= latest_time:
            num_process = len(process_log)
            if num_process == 0:
                # A departure happens and this trip should already be stored in the dictionary
                pass
            else:
                for process in process_log:
                    current_area = area_log[time_stamp]
                    current_slot = slot_log[time_stamp]

                    if current_area is None or current_slot is None:
                        raise ValueError(
                            f"For process {process.ID} Area and slot should not be None."
                        )

                    match process.status:
                        case ProcessStatus.COMPLETED | ProcessStatus.CANCELLED:
                            assert (
                                len(process.starts) == 1 and len(process.ends) == 1
                            ), (
                                f"Current process {process.ID} is completed and should only contain one start and "
                                f"one end time."
                            )

                            if process.dur > 0:
                                # Valid duration
                                dict_of_events[time_stamp] = {
                                    "type": type(process).__name__,
                                    "end": process.ends[0],
                                    "area": current_area.ID,
                                    "slot": current_slot,
                                    "id": process.ID,
                                }
                            else:
                                # Duration is 0
                                assert current_area.issink is True, (
                                    f"A process with no duration could only "
                                    f"happen in the last area before dispatched"
                                )
                                if (
                                    time_stamp in dict_of_events.keys()
                                    and "end" in dict_of_events[time_stamp].keys()
                                ):
                                    start_this_event = dict_of_events[time_stamp]["end"]
                                    dict_of_events[start_this_event] = {
                                        "type": type(process).__name__,
                                        "area": current_area.ID,
                                        "slot": current_slot,
                                        "id": process.ID,
                                    }

                        case ProcessStatus.IN_PROGRESS:
                            assert (
                                len(process.starts) == 1 and len(process.ends) == 0
                            ), f"Current process {process.ID} is marked IN_PROGRESS, but has an end."

                            if current_area is None or current_slot is None:
                                raise ValueError(
                                    f"For process {process.ID} Area and slot should not be None."
                                )

                            if process.dur > 0:
                                # Valid duration
                                dict_of_events[time_stamp] = {
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

                        # The following ProcessStatus possibly only happen while the simulation is running,
                        # not in the results
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


def _complete_standby_departure_events(
    dict_of_events: Dict, latest_time: datetime.datetime
) -> None:
    """
    This function completes the standby departure events by adding an end time to each standby departure event.

    :param dict_of_events: a dictionary containing the events of a vehicle. The keys are the start times of the events.

    :param latest_time: the latest relevant time of the current vehicle. Any events later than this will not be handled.

    :return: None. The results are added to the dictionary.
    """
    for i in range(len(dict_of_events.keys())):
        time_keys = sorted(dict_of_events.keys())

        process_dict = dict_of_events[time_keys[i]]
        if "end" not in process_dict and process_dict["type"] != "Trip":
            # End time of a standby_departure will be the start of the following trip
            if i == len(time_keys) - 1:
                # The event reaches simulation end
                end_time = latest_time
            else:
                end_time = time_keys[i + 1]

            process_dict["end"] = end_time


def _add_soc_to_events(dict_of_events, battery_log) -> None:
    """
    This function completes the soc of each event by looking up the battery log.

    :param dict_of_events: a dictionary containing the events of a vehicle. The keys are the start times of the events.

    :param battery_log: a list of battery logs of a vehicle.

    :return: None. The results are added to the dictionary.
    """
    battery_log_list = []
    for log in battery_log:
        battery_log_list.append((log.t, log.energy / log.energy_real))

    time_keys = sorted(dict_of_events.keys())
    for i in range(len(time_keys)):
        # Get soc
        soc_start = None
        soc_end = None
        start_time = time_keys[i]
        process_dict = dict_of_events[time_keys[i]]
        for j in range(len(battery_log_list)):
            # Access the correct battery log according to time since there is only one battery log for each time
            log = battery_log_list[j]

            if process_dict["type"] != "Trip":
                if log[0] == start_time:
                    soc_start = log[1]
                if log[0] == process_dict["end"]:
                    soc_end = log[1]
                if log[0] < start_time < battery_log_list[j + 1][0]:
                    soc_start = log[1]
                if log[0] < process_dict["end"] < battery_log_list[j + 1][0]:
                    soc_end = log[1]

                if soc_start is not None:
                    soc_start = min(soc_start, 1)  # so
                process_dict["soc_start"] = soc_start
                if soc_end is not None:
                    soc_end = min(soc_end, 1)  # soc should not exceed 1
                process_dict["soc_end"] = soc_end

            else:
                continue


def _add_events_into_database(
    db_vehicle, dict_of_events, session, scenario, simulation_start_time
) -> None:
    """
    This function generates :class:`eflips.model.Event` objects from the dictionary of events and adds them into the.

    database.

    :param db_vehicle: vehicle object in the database

    :param dict_of_events: dictionary containing the events of a vehicle. The keys are the start times of the events.

    :param session: a :class:`sqlalchemy.orm.Session` object for database connection.

    :param scenario: the current simulated scenario

    :param simulation_start_time: simulation start time in :class:`datetime.datetime` format

    :return: None. The results are added to the database.
    """
    for start_time, process_dict in dict_of_events.items():
        # Generate EventType
        match process_dict["type"]:
            case "Serve":
                event_type = EventType.SERVICE
            case "Charge":
                event_type = EventType.CHARGING_DEPOT
            case "Standby":
                if (
                    "is_waiting" in process_dict.keys()
                    and process_dict["is_waiting"] is True
                ):
                    event_type = EventType.STANDBY
                else:
                    event_type = EventType.STANDBY_DEPARTURE
            case "Precondition":
                event_type = EventType.PRECONDITIONING
            case "Trip":
                continue
            case _:
                raise ValueError(
                    'Invalid process type %s. Valid process types are "Serve", "Charge", '
                    '"Standby", "Precondition"'
                )

        current_event = Event(
            scenario=scenario,
            vehicle_type_id=db_vehicle.vehicle_type_id,
            vehicle=db_vehicle,
            station_id=None,
            area_id=int(process_dict["area"]),
            subloc_no=int(process_dict["slot"])
            if "slot" in process_dict.keys()
            else 00,
            trip_id=None,
            time_start=timedelta(seconds=start_time) + simulation_start_time,
            time_end=timedelta(seconds=process_dict["end"]) + simulation_start_time,
            soc_start=process_dict["soc_start"]
            if process_dict["soc_start"] is not None
            else process_dict["soc_end"],
            soc_end=process_dict["soc_end"]
            if process_dict["soc_end"] is not None
            else process_dict["soc_start"],  # if only one battery log is found,
            # then this is not an event with soc change
            event_type=event_type,
            description=process_dict["id"] if "id" in process_dict.keys() else None,
            timeseries=None,
        )

        session.add(current_event)

        # For non-copy schedules with no predecessor events, adding a dummy standby-departure

    time_keys = sorted(dict_of_events.keys())
    if (
        dict_of_events[time_keys[0]]["type"]
        == "Trip"
        # and dict_of_events[time_keys[0]]["is_copy"] is False
    ):
        standby_start = time_keys[0] - 1
        standby_end = time_keys[0]
        rotation_id = int(dict_of_events[time_keys[0]]["id"])
        area = (
            session.query(Area)
            .filter(Area.vehicle_type_id == db_vehicle.vehicle_type_id)
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
            vehicle_type_id=db_vehicle.vehicle_type_id,
            vehicle=db_vehicle,
            station_id=None,
            area_id=area.id,
            subloc_no=area.capacity,
            trip_id=None,
            time_start=timedelta(seconds=standby_start) + simulation_start_time,
            time_end=timedelta(seconds=standby_end) + simulation_start_time,
            soc_start=soc,
            soc_end=soc,
            event_type=EventType.STANDBY_DEPARTURE,
            description=f"DUMMY Standby event for {rotation_id}.",
            timeseries=None,
        )

        session.add(standby_event)

    session.flush()


def _update_vehicle_in_rotation(session, scenario, list_of_assigned_schedules) -> None:
    """
    This function updates the vehicle id assigned to the rotations and deletes the events that are not depot events.

    :param session: a :class:`sqlalchemy.orm.Session` object for database connection.
    :param scenario: the current simulated scenario
    :param list_of_assigned_schedules: a list of tuples containing the rotation id and the vehicle id.
    :return: None. The results are added to the database.
    """
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


def _update_waiting_events(session, scenario, waiting_area_id) -> None:
    """
    This function evaluates the capacity of waiting area and assigns the waiting events to corresponding slots in the.

    waiting area.

    :param session: a :class:`sqlalchemy.orm.Session` object for database connection.

    :param scenario: the current simulated scenario.

    :param waiting_area_id: id of the waiting area.

    :raise ValueError: if the waiting area capacity is less than the peak waiting occupancy.

    :return: None. The results are added to the database.
    """
    # Process all the STANDBY (waiting) events #
    all_waiting_starts = (
        session.query(Event)
        .filter(
            Event.scenario_id == scenario.id,
            Event.event_type == EventType.STANDBY,
            Event.area_id == waiting_area_id,
        )
        .all()
    )

    all_waiting_ends = (
        session.query(Event)
        .filter(
            Event.scenario_id == scenario.id,
            Event.event_type == EventType.STANDBY,
            Event.area_id == waiting_area_id,
        )
        .all()
    )

    assert len(all_waiting_starts) == len(
        all_waiting_ends
    ), f"Number of waiting events starts {len(all_waiting_starts)} is not equal to the number of waiting event ends"

    if len(all_waiting_starts) == 0:
        print(
            "No waiting events found. The depot has enough capacity for waiting. Change the waiting area capacity to 10 as buffer."
        )

        session.query(Area).filter(Area.id == waiting_area_id).update(
            {"capacity": 10}, synchronize_session="auto"
        )

        return

    list_waiting_timestamps = []
    for waiting_start in all_waiting_starts:
        list_waiting_timestamps.append(
            {"timestamp": waiting_start.time_start, "event": (waiting_start.id, 1)}
        )

    for waiting_end in all_waiting_ends:
        list_waiting_timestamps.append(
            {"timestamp": waiting_end.time_end, "event": (waiting_end.id, -1)}
        )

    list_waiting_timestamps.sort(key=lambda x: x["timestamp"])
    start_and_end_records = [wt["event"][1] for wt in list_waiting_timestamps]

    peak_waiting_occupancy = max(list(itertools.accumulate(start_and_end_records)))

    # Assuming that there is only one waiting area in each depot

    waiting_area_id = all_waiting_starts[0].area_id
    waiting_area = session.query(Area).filter(Area.id == waiting_area_id).first()
    if waiting_area.capacity > peak_waiting_occupancy:
        warnings.warn(
            f"Current waiting area capacity {waiting_area.capacity} "
            f"is greater than the peak waiting occupancy. Updating the capacity to {peak_waiting_occupancy}."
        )
        session.query(Area).filter(Area.id == waiting_area_id).update(
            {"capacity": peak_waiting_occupancy}, synchronize_session="auto"
        )
        session.flush()
    elif waiting_area.capacity < peak_waiting_occupancy:
        raise ValueError(
            f"Waiting area capacity is less than the peak waiting occupancy. "
            f"Waiting area capacity: {waiting_area.capacity}, peak waiting occupancy: {peak_waiting_occupancy}."
        )
    else:
        pass

    session.flush()

    # Update waiting slots
    virtual_waiting_area = [None] * peak_waiting_occupancy
    for wt in list_waiting_timestamps:
        # check in
        if wt["event"][1] == 1:
            for i in range(len(virtual_waiting_area)):
                if virtual_waiting_area[i] is None:
                    virtual_waiting_area[i] = wt["event"][0]
                    session.query(Event).filter(Event.id == wt["event"][0]).update(
                        {"subloc_no": i}, synchronize_session="auto"
                    )
                    break
        # check out
        else:
            for i in range(len(virtual_waiting_area)):
                if virtual_waiting_area[i] == wt["event"][0]:
                    current_waiting_event = (
                        session.query(Event).filter(Event.id == wt["event"][0]).first()
                    )
                    assert current_waiting_event.subloc_no == i, (
                        f"Subloc number of the event {current_waiting_event.id} is not equal to the index of the "
                        f"event in the virtual waiting area."
                    )
                    virtual_waiting_area[i] = None
                    break

    session.flush()
