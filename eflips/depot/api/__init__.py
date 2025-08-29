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
import logging
import os
import warnings
from collections import OrderedDict
from datetime import timedelta, datetime
from enum import Enum
from math import ceil
from typing import Any, Dict, Optional, Union

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
    VehicleType,
    AreaType,
    ChargeType,
    Route,
    ConsistencyWarning,
    Station,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

import eflips.depot
from eflips.depot import (
    DepotEvaluation,
    SimulationHost,
    UnstableSimulationException,
    DelayedTripException,
)
from eflips.depot.api.private.consumption import ConsumptionResult
from eflips.depot.api.private.consumption import (
    initialize_vehicle,
    add_initial_standby_event,
    attempt_opportunity_charging_event,
    extract_trip_information,
)
from eflips.depot.api.private.depot import (
    delete_depots,
    depot_to_template,
    group_rotations_by_start_end_stop,
    generate_depot,
    depot_smallest_possible_size,
)
from eflips.depot.api.private.results_to_database import (
    get_finished_schedules_per_vehicle,
    generate_vehicle_events,
    complete_standby_departure_events,
    add_soc_to_events,
    add_events_into_database,
    update_vehicle_in_rotation,
    update_waiting_events,
)
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


def generate_consumption_result(scenario):
    """
    Generate consumption information for the scenario.

    This function retrieves the consumption LUT and vehicle classes from the database and returns a dictionary
    containing the consumption information for each vehicle type in the scenario. If a trip has no corresponding
    consumption LUT, it won't be included in the results.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.

    :return: A dictionary containing the consumption information for each vehicle type in the scenario.
    """

    with create_session(scenario) as (session, scenario):
        trips = session.query(Trip).filter(Trip.scenario_id == scenario.id).all()
        consumption_results = {}
        for trip in trips:
            try:
                consumption_info = extract_trip_information(
                    trip.id,
                    scenario,
                )
            except ValueError as e:
                # If the trip has no consumption information, skip it
                logging.warning(
                    f"Skipping trip {trip.id} due to missing consumption information: {e}"
                )
                continue

            battery_capacity_current_vt = trip.rotation.vehicle_type.battery_capacity
            consumption_result = consumption_info.generate_consumption_result(
                battery_capacity_current_vt
            )
            consumption_results[trip.id] = consumption_result

    return consumption_results


def simple_consumption_simulation(
    scenario: Union[Scenario, int, Any],
    initialize_vehicles: bool,
    database_url: Optional[str] = None,
    calculate_timeseries: bool = False,
    terminus_deadtime: timedelta = timedelta(minutes=1),
    consumption_result: Dict[int, ConsumptionResult] | None = None,
) -> None:
    """
    Run a simple consumption simulation and optionally initialize vehicles in the database.

    This function calculates energy consumption by multiplying each vehicle's total traveled
    distance by a constant ``VehicleType.consumption`` (kWh per km), then updates the database
    with the resulting SoC (State of Charge) data. The function can also use precomputed results
    for specific trips via the ``consumption_result`` parameter.

    If ``initialize_vehicles`` is True, vehicles and an initial STANDBY event (with 100% SoC)
    are created for each rotation that does not already have a vehicle. If it is False, existing
    vehicles in the database are assumed, and a check is performed to ensure each rotation has a
    vehicle.

    Opportunity charging can optionally be applied at the end of each trip, if the vehicle and
    station both allow it, and if the rotation is flagged to allow it. This charging event is
    constrained by a configurable terminus deadtime.

    **SoC Constraints**

    - When no precomputed results are provided, SoC is computed by subtracting energy used
      (`consumption * distance / battery_capacity`) from the previous event’s SoC.
    - When precomputed ``ConsumptionResult`` objects are provided in ``consumption_result``,
      they must have a non-positive total change in SoC (``delta_soc_total <= 0``).
      If the function detects a positive ``delta_soc_total``, it raises a ``ValueError``.

    **Timeseries Calculation**

    - If ``calculate_timeseries`` is True, the function builds a more granular SoC timeseries
      at each stop in the trip and stores it in the ``Event.timeseries`` column.
    - If False, the event’s ``timeseries`` is set to ``None``, which may speed up the simulation
      if you do not need intermediate SoC data.

    :param scenario:
        One of:
          - A :class:`eflips.model.Scenario` instance containing the input data for the simulation.
          - An integer specifying the ID of a scenario in the database.
          - Any other object with an integer ``id`` attribute.

        If not passing a :class:`eflips.model.Scenario` directly, the `database_url` parameter
        or the environment variable ``DATABASE_URL`` must point to a valid database.

    :param initialize_vehicles:
        A boolean flag indicating whether new vehicles should be created and assigned
        to rotations in the database. Set this to True the first time you run the simulation
        so that vehicles are initialized. In subsequent runs, set to False if vehicles
        are already present.

    :param database_url:
        A database connection string (e.g., ``postgresql://user:pass@host/db``).
        If you do not provide this and ``scenario`` is not a
        :class:`eflips.model.Scenario` instance, the environment variable
        ``DATABASE_URL`` must be set.

    :param calculate_timeseries:
        If True, each trip’s detailed SoC timeseries is computed and stored in the
        ``timeseries`` column of the corresponding driving and charging events.
        If False, only the start/end SoC is recorded, and ``timeseries`` is set to None.

    :param terminus_deadtime:
        The total time overhead (attach + detach) for charging at the terminus.
        If this deadtime exceeds the available layover time, no charging is performed.

    :param consumption_result:
        A dictionary mapping trip IDs to :class:`ConsumptionResult` instances for
        precomputed SoC changes. If an entry exists for a trip, this function uses
        those precomputed SoC changes instead of calculating them from distance
        and consumption. Each ``ConsumptionResult`` must have:

        - A non-positive ``delta_soc_total`` (<= 0).
        - Optionally, matching lists of timestamps and delta SoC values that are
          decreasing (i.e., the vehicle only loses or maintains SoC).

    :returns:
        ``None``. All simulation results are written directly to the database as
        :class:`eflips.model.Event` entries.

    :raises ValueError:
        - If a rotation in the scenario does not have a vehicle when
          ``initialize_vehicles=False``.
        - If the vehicle type has no ``consumption`` value.
        - If a provided ``ConsumptionResult`` has inconsistent list lengths,
          or if its ``delta_soc_total`` is positive.
        - If SoC timeseries are not decreasing when provided
          via ``consumption_result``.
    """
    logger = logging.getLogger(__name__)

    with create_session(scenario, database_url) as (session, scenario):
        rotations = (
            session.query(Rotation)
            .filter(Rotation.scenario_id == scenario.id)
            .order_by(Rotation.id)
            .options(
                sqlalchemy.orm.joinedload(Rotation.trips)
                .joinedload(Trip.route)
                .joinedload(Route.arrival_station)
            )
            .options(sqlalchemy.orm.joinedload(Rotation.vehicle_type))
            .options(sqlalchemy.orm.joinedload(Rotation.vehicle))
        )
        if initialize_vehicles:
            for rotation in rotations:
                initialize_vehicle(rotation, session)

        for rotation in rotations:
            if rotation.vehicle is None:
                raise ValueError("The rotation does not have a vehicle assigned to it.")

        vehicles = (
            session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).all()
        )

        # Get the event count for each vbehicle in a single query using a groub_py clause
        vehicle_event_count_q = (
            session.query(Event.vehicle_id, sqlalchemy.func.count(Event.id))
            .join(Vehicle)
            .filter(Vehicle.scenario_id == scenario.id)
            .group_by(Event.vehicle_id)
        )
        vehicle_event_count = dict(vehicle_event_count_q.all())

        for vehicle in vehicles:
            if vehicle.id not in vehicle_event_count.keys():
                add_initial_standby_event(vehicle, session)

        # Since we are doing no_autoflush blocks later, we need to flush the session once here so that unflushed stuff
        # From preceding functions is visible in the database
        session.flush()

        for rotation in rotations:
            rotation: Rotation
            with session.no_autoflush:
                vehicle_type = (
                    session.query(VehicleType)
                    .join(Rotation)
                    .filter(Rotation.id == rotation.id)
                    .one()
                )
                vehicle = (
                    session.query(Vehicle)
                    .join(Rotation)
                    .filter(Rotation.id == rotation.id)
                    .one()
                )
            if vehicle_type.consumption is None:
                # If the vehicle type has no consumption value, all trips must have a precomputed consumption result
                all_trip_ids = [trip.id for trip in rotation.trips]
                if not (
                    consumption_result is not None
                    and all(trip_id in consumption_result for trip_id in all_trip_ids)
                ):
                    raise ValueError(
                        "The vehicle type does not have a consumption value set and no consumption results are provided."
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
                if consumption_result is None or trip.id not in consumption_result:
                    logger.debug("Calculating consumption for trip %s", trip.id)
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
                            elapsed_energy = consumption * (
                                elapsed_distance / 1000
                            )  # kWh
                            soc = (
                                current_soc
                                - elapsed_energy / vehicle_type.battery_capacity
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
                    current_soc = (
                        soc_start - energy_used / vehicle_type.battery_capacity
                    )
                else:
                    logger.debug(f"Using pre-calculated timeseries for trip {trip.id}")
                    if (
                        calculate_timeseries
                        and consumption_result[trip.id].timestamps is not None
                    ):
                        assert consumption_result[trip.id].delta_soc is not None
                        timestamps = consumption_result[trip.id].timestamps

                        # Make sure the delta_soc is a monotonic decreasing function, with the same length as timestamps
                        if len(consumption_result[trip.id].delta_soc) != len(
                            timestamps
                        ):
                            raise ValueError(
                                "The length of the delta_soc and timestamps lists must be the same."
                            )
                        delta_socs = consumption_result[trip.id].delta_soc
                        if delta_socs[-1] > 0:
                            raise ValueError(
                                "The delta_soc must be a decreasing function."
                            )

                        socs = [current_soc + d for d in delta_socs]
                        timeseries = {
                            "time": [t.isoformat() for t in timestamps],
                            "soc": socs,
                        }
                    else:
                        timeseries = None

                    if consumption_result[trip.id].delta_soc_total > 0:
                        raise ValueError(
                            "The current SoC must be <= 0 when using a consumption result."
                        )
                    soc_start = current_soc
                    current_soc += consumption_result[trip.id].delta_soc_total

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
                    and trip.route.arrival_station.charge_type == ChargeType.OPPORTUNITY
                    and trip != rotation.trips[-1]
                ):
                    trip_index = rotation.trips.index(trip)
                    next_trip = rotation.trips[trip_index + 1]

                    current_soc = attempt_opportunity_charging_event(
                        previous_trip=trip,
                        next_trip=next_trip,
                        vehicle=vehicle,
                        charge_start_soc=current_soc,
                        terminus_deadtime=terminus_deadtime,
                        session=session,
                    )


def generate_depot_layout(
    scenario: Union[Scenario, int, Any],
    charging_power: float = 90,
    database_url: Optional[str] = None,
    delete_existing_depot: bool = False,
) -> None:
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

            # Create one direct slot for each rotation (it's way too much, but should work)
            vt_capacity_dict: Dict[VehicleType, Dict[AreaType, None | int]] = {}
            rotation_count_depot = 0
            for vehicle_type, rotations in vehicle_type_dict.items():
                vt_capacity_dict[vehicle_type] = {
                    AreaType.LINE: None,
                    AreaType.DIRECT_ONESIDE: len(rotations),
                    AreaType.DIRECT_TWOSIDE: None,
                }
                rotation_count_depot += len(rotations)

            generate_depot(
                vt_capacity_dict,
                first_stop,
                scenario,
                session,
                charging_power=charging_power,
                num_shunting_slots=max(rotation_count_depot // 10, 1),
                num_cleaning_slots=max(rotation_count_depot // 10, 1),
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
    logger = logging.getLogger(__name__)

    try:
        from eflips.opt.smart_charging import (
            optimize_charging_events_even,
            add_slack_time_to_events_of_depot,
        )
    except ImportError:
        logger.error(
            "The eFLIPS smart charging module is not installed. Please install eflips-opt >= 0.2.0."
        )
        raise

    with create_session(scenario, database_url) as (session, scenario):
        depots = session.query(Depot).filter(Depot.scenario_id == scenario.id).all()
        for depot in depots:
            add_slack_time_to_events_of_depot(
                depot, session, standby_departure_duration
            )

            events_for_depot = (
                session.query(Event)
                .join(Area)
                .filter(Area.depot_id == depot.id)
                .filter(Event.event_type == EventType.CHARGING_DEPOT)
                .all()
            )

            optimize_charging_events_even(events_for_depot)
            for event in events_for_depot:
                session.add(event)


def simulate_scenario(
    scenario: Union[Scenario, int, Any],
    repetition_period: Optional[timedelta] = None,
    database_url: Optional[str] = None,
    smart_charging_strategy: SmartChargingStrategy = SmartChargingStrategy.NONE,
    ignore_unstable_simulation: bool = False,
    ignore_delayed_trips: bool = False,
) -> None:
    """
    This method simulates a scenario and adds the results to the database.

    It fills in the "Charging Events" in the :class:`eflips.model.Event` table and associates
    :class:`eflips.model.Vehicle` objects with all the existing "Driving Events" in the :class:`eflips.model.Event`
    table. If the simulation becomes unstable, an :class:`UnstableSimulationException` is raised.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
        an integer specifying the ID of a scenario in the database. Or any other object that has an attribute
        ``id`` that is an integer. If no :class:`eflips.model.Scenario` object is passed, the ``database_url``
        parameter must be set to a valid database URL or the environment variable ``DATABASE_URL`` must be set to a
        valid database URL.

    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. This
        is needed because the result should be a steady-state result. This can only be achieved by simulating a
        time period before and after our actual simulation, and then only using the "middle". eFLIPS tries to
        automatically detect whether the schedule should be repeated daily or weekly. If this fails, a ValueError is
        raised and repetition needs to be specified manually.

    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
        :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a valid database
        URL.

    :param smart_charging_strategy: An optional parameter specifying the smart charging strategy to be used. The
        default is SmartChargingStrategy.NONE. The following strategies are available:
        - SmartChargingStrategy.NONE: Do not use smart charging. Buses are charged with the maximum power available,
        from the time they arrive at the depot until they are full (or leave the depot).
        - SmartChargingStrategy.EVEN: Use smart charging with an even distribution of charging power over the time the
        bus is at the depot. This aims to minimize the peak power demand.
        - SmartChargingStrategy.MIN_PRICE: Not implemented yet.

    :param ignore_unstable_simulation: If True, the simulation will not raise an exception if it becomes unstable.
    :param ignore_delayed_trips: If True, the simulation will not raise an exception if there are delayed trips.

    :return: Nothing. The results are added to the database.

    :raises UnstableSimulationException: If the simulation becomes numerically unstable or if
        the parameters cause the solver to diverge.
    :raises DelayedTripException: If there are delayed trips in the simulation.
    """
    logger = logging.getLogger(__name__)

    with create_session(scenario, database_url) as (session, scenario):
        simulation_host = init_simulation(
            scenario=scenario,
            session=session,
            repetition_period=repetition_period,
        )
        ev = run_simulation(simulation_host)
        try:
            add_evaluation_to_database(scenario, ev, session)

        except* DelayedTripException as delay_exp:
            if not ignore_delayed_trips:
                logger.error(
                    "There are delayed trips in the simulation. "
                    "Please check the input data and try again."
                )
                raise delay_exp
        except* UnstableSimulationException as unstable_exp:
            if not ignore_unstable_simulation:
                logger.error(
                    "The simulation became unstable. "
                    "Please check the input data and try again."
                )
                raise unstable_exp

        match smart_charging_strategy:
            case SmartChargingStrategy.NONE:
                pass
            case SmartChargingStrategy.EVEN:
                apply_even_smart_charging(scenario, database_url)
            case SmartChargingStrategy.MIN_PRICE:
                raise NotImplementedError("MIN_PRICE strategy is not implemented yet.")
            case _:
                raise NotImplementedError()


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
    for depot in session.query(Depot).filter(Depot.scenario_id == scenario.id).all():
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
        for rotation in session.query(Rotation)
        .filter(Rotation.scenario_id == scenario.id)
        .all()
    ]

    if repetition_period is None:
        repetition_period = schedule_duration_days(scenario)
        if repetition_period not in (timedelta(days=1), timedelta(days=7)):
            warnings.warn(
                f"Non-standard schedule duration of {repetition_period}. Please make sure this is intended.",
                UserWarning,
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
    # Clear old vehicle counts, if they exist
    eflips.globalConstants["depot"]["vehicle_count"] = {}

    grouped_rotations = group_rotations_by_start_end_stop(scenario.id, session)

    # We need to calculate roughly how many vehicles we need for each depot
    for depot in session.query(Depot).filter(Depot.scenario_id == scenario.id).all():
        depot_id = str(depot.id)
        eflips.globalConstants["depot"]["vehicle_count"][depot_id] = {}
        vehicle_types_for_depot = set(str(area.vehicle_type_id) for area in depot.areas)
        if "None" in vehicle_types_for_depot:
            vehicle_types_for_depot.remove("None")

        # In this case, all types are allowed
        if len(vehicle_types_for_depot) == 0:
            vehicle_types_for_depot = set([str(vt.id) for vt in scenario.vehicle_types])

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
            # Calculate it from the amount of rotations with a 4x margin because 4 times of repetition
            # in repeat_vehicle_schedules()
            rotations = grouped_rotations[(depot.station, depot.station)]

            for vehicle_type in vehicle_types_for_depot:
                vehicle_type_object = (
                    session.query(VehicleType)
                    .filter(
                        VehicleType.id == vehicle_type,
                        VehicleType.scenario_id == scenario.id,
                    )
                    .one()
                )
                vehicle_count = len(rotations.get(vehicle_type_object, []))

                if vehicle_count > 0:
                    eflips.globalConstants["depot"]["vehicle_count"][depot_id][
                        vehicle_type
                    ] = (
                        vehicle_count
                        * 4  # We multiply by 4 because we repeat the vehicle schedules 4 times
                    )
                else:
                    warnings.warn(
                        f"There are no rotations assigned to type {vehicle_type_object} in depot {depot_id}"
                    )

    # We  need to put the vehicle type objects into the GlobalConstants
    for vehicle_type in (
        session.query(VehicleType).filter(VehicleType.scenario_id == scenario.id).all()
    ):
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


def insert_dummy_standby_departure_events(
    depot_id: int, session: Session, sim_time_end: Optional[datetime] = None
) -> None:
    """
    Workaround for the missing STANDBY_DEPARTURE events in the database.

    :param session: The database session
    :param scenario: A scenario object
    :param sim_time_end: The end time of the simulation. If None, final events might not be properly handled.
    :return:
    """
    logger = logging.getLogger(__name__)

    # Look for charging events at areas belonging to the depot
    charging_events = (
        session.query(Event)
        .join(Area)
        .filter(Area.depot_id == depot_id)
        .filter(Event.event_type == EventType.CHARGING_DEPOT)
        .all()
    )

    for charging_event in charging_events:
        # See if the next event is a DRIVING event, but there is time between the two events
        next_event = (
            session.query(Event)
            .filter(Event.time_start >= charging_event.time_end)
            .filter(Event.vehicle_id == charging_event.vehicle_id)
            .order_by(Event.time_start)
            .first()
        )
        if (
            next_event is not None
            and next_event.event_type == EventType.DRIVING
            and (next_event.time_start - charging_event.time_end) > timedelta(seconds=1)
        ):
            logger.warning("Inserting dummy STANDBY_DEPARTURE event")
            # Insert a dummy STANDBY_DEPARTURE event
            dummy_event = Event(
                vehicle_id=charging_event.vehicle_id,
                vehicle_type_id=charging_event.vehicle.vehicle_type_id,
                time_start=charging_event.time_end,
                time_end=(next_event.time_start - timedelta(seconds=1)),
                event_type=EventType.STANDBY_DEPARTURE,
                area_id=charging_event.area_id,
                subloc_no=charging_event.subloc_no,
                scenario_id=charging_event.scenario_id,
                soc_start=charging_event.soc_end,
                soc_end=charging_event.soc_end,
                description="Dummy STANDBY_DEPARTURE event",
            )
            session.add(dummy_event)
        elif next_event is None and sim_time_end is not None:
            # If the event's end is before the simulation end, insert a dummy STANDBY_DEPARTURE event
            # From the end of the charging event to the end of the simulation
            logger.warning("Inserting dummy STANDBY_DEPARTURE event")
            if charging_event.time_end < sim_time_end:
                dummy_event = Event(
                    vehicle_id=charging_event.vehicle_id,
                    vehicle_type_id=charging_event.vehicle.vehicle_type_id,
                    time_start=charging_event.time_end,
                    time_end=sim_time_end,
                    event_type=EventType.STANDBY_DEPARTURE,
                    area_id=charging_event.area_id,
                    subloc_no=charging_event.subloc_no,
                    scenario_id=charging_event.scenario_id,
                    soc_start=charging_event.soc_end,
                    soc_end=charging_event.soc_end,
                    description="Dummy STANDBY_DEPARTURE event",
                )
                session.add(dummy_event)


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

    :raises UnstableSimulationException: If the simulation becomes numerically unstable or if
        the parameters cause the solver to diverge.
    :raises DelayedTripException: If there are delayed trips in the simulation.
    """

    # Read simulation start time

    for depot_id, depot_evaluation in depot_evaluations.items():
        simulation_start_time = depot_evaluation.sim_start_datetime

        # Depot-layer operations

        list_of_assigned_schedules = []

        waiting_area_id = None

        total_areas = session.query(Area).filter(Area.scenario_id == scenario.id).all()
        for area in total_areas:
            if area.depot_id == int(depot_id) and len(area.processes) == 0:
                waiting_area_id = area.id

        assert isinstance(waiting_area_id, int) and waiting_area_id > 0, (
            f"Waiting area id should be an integer greater than 0. For every depot there must be at least "
            f"one waiting area."
        )

        unstable_exp = UnstableSimulationException()
        delay_exp = DelayedTripException()

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
            ) = get_finished_schedules_per_vehicle(
                dict_of_events,
                current_vehicle.finished_trips,
                current_vehicle_db.id,
                unstable_exp,
                delay_exp,
            )

            if schedule_current_vehicle is None:
                continue

            list_of_assigned_schedules.extend(schedule_current_vehicle)

            generate_vehicle_events(
                dict_of_events,
                current_vehicle,
                waiting_area_id,
                earliest_time,
                latest_time,
            )

            # Python passes dictionaries by reference

            complete_standby_departure_events(dict_of_events, latest_time)

            add_soc_to_events(dict_of_events, current_vehicle.battery_logs)

            add_events_into_database(
                current_vehicle_db,
                dict_of_events,
                session,
                scenario,
                simulation_start_time,
            )

        # Postprocessing of events
        update_vehicle_in_rotation(session, scenario, list_of_assigned_schedules)
        update_waiting_events(session, scenario, waiting_area_id)

        errors = []

        if delay_exp.has_errors:
            errors.append(delay_exp)
        if unstable_exp.has_errors:
            errors.append(unstable_exp)

        if len(errors) > 0:
            raise ExceptionGroup(
                "Simulation is either unstable or including delayed blocks", errors
            )


def generate_depot_optimal_size(
    scenario: Union[Scenario, int, Any],
    standard_block_length: int = 6,
    charging_power: float = 90,
    database_url: Optional[str] = None,
    delete_existing_depot: bool = False,
    use_consumption_lut: bool = False,
    repetition_period: Optional[timedelta] = None,
) -> None:
    """
    Generates an optimal depot layout with the smallest possible size for each depot in the scenario.

    Line charging areas
     with given block length area preferred. The existing depot will be deleted if `delete_existing_depot` is set to True.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
        an integer specifying the ID of a scenario in the database. Or any other object that has an attribute "id" containing
        an integer pointing to a unique scenario id.
    :param standard_block_length: The standard block length for the depot layout in meters. Default is 6.
    :param charging_power: The charging power of the charging area in kW. Default is 90.
    :param database_url: An optional database URL. Used if no database url is given by the environment variable.
    :param delete_existing_depot: If there is already a depot existing in this scenario, set True to delete this
        existing depot. Set to False and a ValueError will be raised if there is a depot in this scenario.
    :param use_consumption_lut: If True, the depot layout will be generated based on the consumption lookup table.
        If False, constant consumption stored in VehicleType table will be used.
    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. If not
        specified, a default repetition period will be generated in simulate_scenario(). If the depot layout generated
        in this function will be used for further simulations, make sure that the repetition period is set to the same
        value as in the simulation.

    :return: None. The depot layout will be added to the database.
    """

    logger = logging.getLogger(__name__)

    with create_session(scenario, database_url) as (session, scenario):
        # Delete all depot events
        session.query(Event).filter(
            Event.scenario_id == scenario.id, Event.area_id.isnot(None)
        ).delete()

        if session.query(Depot).filter(Depot.scenario_id == scenario.id).count() != 0:
            if delete_existing_depot is False:
                raise ValueError(
                    "Depot already exists. Set delete_existing_depot to True to delete it."
                )

            delete_depots(scenario, session)

        outer_savepoint = session.begin_nested()
        # Delete all vehicles and events, also disconnect the vehicles from the rotations
        rotation_q = session.query(Rotation).filter(Rotation.scenario_id == scenario.id)
        rotation_q.update({"vehicle_id": None})
        session.query(Event).filter(Event.scenario_id == scenario.id).delete()
        session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).delete()

        # Handles existing depot

        ##### Step 0: Consumption Simulation #####
        # Run the consumption simulation for all depots

        if use_consumption_lut:
            # If using the consumption lookup table, we need to calculate the consumption results
            consumption_results = generate_consumption_result(scenario)
            simple_consumption_simulation(
                scenario,
                initialize_vehicles=True,
                consumption_result=consumption_results,
            )
        else:
            # If not using the consumption lookup table, we need to initialize the vehicles with the constant consumption
            simple_consumption_simulation(scenario, initialize_vehicles=True)

        ##### Step 1: Find all potential depots #####
        # These are all the spots where a rotation starts and end
        warnings.simplefilter("ignore", category=ConsistencyWarning)
        warnings.simplefilter("ignore", category=UserWarning)

        depot_capacities_for_scenario: Dict[
            Station, Dict[VehicleType, Dict[AreaType, int]]
        ] = {}

        num_rotations_for_scenario: Dict[Station, int] = {}

        grouped_rotations = group_rotations_by_start_end_stop(scenario.id, session)

        for (
            first_last_stop_tup,
            vehicle_type_dict,
        ) in grouped_rotations.items():
            first_stop, last_stop = first_last_stop_tup
            if first_stop != last_stop:
                raise ValueError("First and last stop of a rotation are not the same.")

            station = first_stop
            rotation_count_depot = sum(
                len(rotations) for vehicle_type, rotations in vehicle_type_dict.items()
            )

            inner_savepoint = session.begin_nested()
            try:
                # (Temporarily) Delete all rotations not starting or ending at the station
                logger.debug(
                    f"Deleting all rotations not starting or ending at {station.name}"
                )
                all_rot_for_scenario = (
                    session.query(Rotation)
                    .filter(Rotation.scenario_id == scenario.id)
                    .all()
                )
                to_delete = []
                for rot in all_rot_for_scenario:
                    first_stop = rot.trips[0].route.departure_station
                    if first_stop != station:
                        for trip in rot.trips:
                            for stop_time in trip.stop_times:
                                to_delete.append(stop_time)
                            for event in trip.events:
                                to_delete.append(event)
                            to_delete.append(trip)
                        to_delete.append(rot)
                for obj in to_delete:
                    session.flush()
                    session.delete(obj)
                    session.flush()

                logger.info(f"Generating depot layout for station {station.name}")
                vt_capacities_for_station = depot_smallest_possible_size(
                    station,
                    scenario,
                    session,
                    standard_block_length,
                    charging_power,
                    repetition_period,
                )

                depot_capacities_for_scenario[station] = vt_capacities_for_station
                num_rotations_for_scenario[station] = rotation_count_depot
            finally:
                inner_savepoint.rollback()

        outer_savepoint.rollback()

        # Estimation of the number of shunting and cleaning slots

        # Create depot using the calculated capacities
        for depot_station, capacities in depot_capacities_for_scenario.items():
            vehicle_type_rot_dict = grouped_rotations[depot_station, depot_station]

            all_rotations_this_depot = []

            for vehicle_type, rotations in vehicle_type_rot_dict.items():
                all_rotations_this_depot.extend(rotations)

            # sort the rotations by their start time
            all_rotations_this_depot.sort(key=lambda r: r.trips[0].departure_time)

            start_time = all_rotations_this_depot[0].trips[0].departure_time
            end_time = all_rotations_this_depot[-1].trips[-1].arrival_time

            elapsed_time = (end_time - start_time).total_seconds()
            # make them into a numpy with 30 min resolution
            import numpy as np

            TIME_RESOLUTION = 30 * 60  # 30 minutes in seconds

            time_range = np.zeros(int(elapsed_time / TIME_RESOLUTION) + 1)
            # calculate the number of rotations per time slot
            for rot in all_rotations_this_depot:
                start_time_index = int(
                    (rot.trips[0].departure_time - start_time).total_seconds()
                    // TIME_RESOLUTION
                )
                end_time_index = int(
                    (rot.trips[-1].arrival_time - start_time).total_seconds()
                    // TIME_RESOLUTION
                )
                # interpolate the start and end time to the time range

                time_range[start_time_index : end_time_index + 1] += 1

            generate_depot(
                capacities,
                depot_station,
                scenario,
                session,
                standard_block_length=standard_block_length,
                charging_power=charging_power,
                num_shunting_slots=int(max(time_range)),
                num_cleaning_slots=int(max(time_range)),
            )


def schedule_duration_days(
    scenario: Union[Scenario, int, Any], database_url: Optional[str] = None
) -> timedelta:
    """
    This method calculates the duration of a given scenario in days.

    This is the duration
    between the first departure of the first day, and the last departure on the last day, rounded up to full days.

    Most of the time, this is the "natural" repetition period of the scenario. We are simulating one full period, and
    this period – continuously repeated – is what happens in reality.

    This method can be used to show the user what the detected repetition period is and to auto-set the repetition
    period if none is provided.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
        an integer specifying the ID of a scenario in the database. Or any other object that has an attribute "id"
        containing an integer pointing to a unique scenario id.
    :param database_url: An optional database URL. Used if no database url is given by the environment variable.
    :return: a timedelta object representing the duration in days.
    """
    with create_session(scenario, database_url) as (session, scenario):
        first_departure = (
            session.query(func.min(Trip.departure_time))
            .filter(Trip.scenario_id == scenario.id)
            .scalar()
        )
        if first_departure is None:
            raise ValueError("No trips found in the scenario.")

        last_departure = (
            session.query(func.max(Trip.departure_time))
            .filter(Trip.scenario_id == scenario.id)
            .scalar()
        )
        if last_departure is None:
            raise ValueError("No trips found in the scenario.")

        duration = last_departure - first_departure
        duration_days = ceil(duration.total_seconds() / (24 * 60 * 60))

        return timedelta(days=duration_days)
