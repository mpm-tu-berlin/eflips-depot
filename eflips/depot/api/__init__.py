"""
This package contains the public API for eFLIPS-Depot. It is to be used in conjunction with the
`eflips.model <https://github.com/mpm-tu-berlin/eflips-model>`_ package, where the Scenario is defined. The Scenario
is then passed to the :func:`eflips.depot.api.init_simulation` function, which returns a (black-box)
:class:`SimulationHost` object. This object is then passed to the :func:`eflips.depot.api.run_simulation` function,
which returns another (black-box) object, the :class:`DepotEvaluation` object. This object contains the results of
the simulation, which can be added to the database using the :func:`eflips.depot.api.add_evaluation_to_database`
function.
"""
import os
from contextlib import contextmanager
from datetime import timedelta
from math import ceil
from typing import Any, Dict, Optional, Union, Tuple

import sqlalchemy.orm
from eflips.model import (
    Event,
    EventType,
    Rotation,
    Scenario,
    Vehicle,
    Depot,
    Plan,
    Process,
    AssocPlanProcess,
    AssocAreaProcess,
    Area,
    AreaType,
)
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import eflips.depot
from eflips.depot import ProcessStatus
from eflips.depot import DepotEvaluation, SimulationHost
from eflips.depot.api.private import (
    depot_to_template,
    repeat_vehicle_schedules,
    start_and_end_times,
    vehicle_type_to_global_constants_dict,
    VehicleSchedule,
)


@contextmanager
def create_session(
    scenario: Union[Scenario, int, Any], database_url: Optional[str] = None
) -> Tuple[Session, Scenario]:
    """
    This method takes a scenario, which can be either a :class:`eflips.model.Scenario` object, an integer specifying
    the ID of a scenario in the database, or any other object that has an attribute `id` that is an integer. It then
    creates a SQLAlchemy session and returns it. If the scenario is a :class:`eflips.model.Scenario` object, the
    session is created and returned. If the scenario is an integer or an object with an `id` attribute, the session
    is created, returned and closed after the context manager is exited.
    :param scenario: Either a :class:`eflips.model.Scenario` object, an integer specifying the ID of a scenario in the
        database, or any other object that has an attribute `id` that is an integer.
    :return: Yield a Tuple of the session and the scenario.
    """
    managed_session = False
    engine = None
    session = None
    try:
        if isinstance(scenario, Scenario):
            session = inspect(scenario).session
        elif isinstance(scenario, int) or hasattr(scenario, "id"):
            if isinstance(scenario, int):
                scenario_id = scenario
            else:
                scenario_id = scenario.id

            if database_url is None:
                if "DATABASE_URL" in os.environ:
                    database_url = os.environ.get("DATABASE_URL")
                else:
                    raise ValueError("No database URL specified.")

            managed_session = True
            engine = create_engine(database_url)
            session = Session(engine)
            scenario = session.query(Scenario).filter(Scenario.id == scenario_id).one()
        else:
            raise ValueError(
                "The scenario parameter must be either a Scenario object, an integer or an object with an 'id' attribute."
            )
        yield session, scenario
    finally:
        if managed_session:
            if session is not None:
                session.commit()
                session.close()
            if engine is not None:
                engine.dispose()


def _delete_depot(scenario: Scenario, session: Session):
    """This function deletes all depot-related data from the database for a given scenario. Used before a new depot
    in this scenario is created.

    :param scenario: The scenario to be simulated
    :param session: The database session

    :return: None. The depot-related data will be deleted from the database.
    """

    # Delete assocs
    session.query(AssocPlanProcess).filter(
        AssocPlanProcess.scenario_id == scenario.id
    ).delete()
    list_of_area = session.query(Area).filter(Area.scenario_id == scenario.id).all()

    for area in list_of_area:
        session.query(AssocAreaProcess).filter(
            AssocAreaProcess.area_id == area.id
        ).delete()
        session.query(Event).filter(Event.area_id == area.id).delete()

    # delete processes
    session.query(Process).filter(Process.scenario_id == scenario.id).delete()

    # delete areas
    session.query(Area).filter(Area.scenario_id == scenario.id).delete()
    # delete depot
    session.query(Depot).filter(Depot.scenario_id == scenario.id).delete()
    # delete plan
    session.query(Plan).filter(Plan.scenario_id == scenario.id).delete()
    # delete assoc_plan_process

    session.commit()


def generate_depot_layout(
    scenario: Union[Scenario, int, Any],
    charging_power: float,
    database_url: Optional[str] = None,
    delete_existing_depot: bool = False,
    capacity: Optional[int] = None,
):
    """
    This function generates a simple depot layout according to the vehicle types and rotations in the scenario.

    For each vehicle type, it generates 3 areas: arrival_area, charging_area, and standby-departure_area, all with type
    DIRECT_ONESIDE. The capacity of each area can be specified by user, or generated according to number of
    rotations. Each area has a list of available processes. When specified by user, the capacity of all areas will be
    the same.

    A default plan will also be generated, which includes the following default processes: standby_arrival, cleaning,
    charging and standby_departure. Each vehicle will be processed with this exact order (stancby_arrival is optional
    because it only happens if a vehicle needs to wait for the next process).

    Using this function causes deleting the original depot in this scenario.


    :param scenario: The scenario to be simulated
    :param charging_power: the charging power of the charging area in kW
    :param delete_existing_depot: if there is already a depot existing in this scenario, set True to delete this
        existing depot. Set to False and a ValueError will be raised if there is a depot in this scenario.
    :param capacity: capacity of each area. If not specified, the capacity will be generated according to the rotation.

    :return: None. The depot layout will be added to the database.
    """

    with create_session(scenario, database_url) as (session, scenario):
        # Handles existing depot
        if session.query(Depot).filter(Depot.scenario_id == scenario.id).count() != 0:
            if delete_existing_depot is False:
                raise ValueError("Depot already exists.")
            else:
                _delete_depot(scenario, session)

        # Create a simple depot
        depot = Depot(scenario=scenario, name="Test Depot", name_short="TD")
        session.add(depot)

        # Create plan
        plan = Plan(scenario=scenario, name="Test Plan")
        session.add(plan)

        depot.default_plan = plan

        # Create processes
        standby_arrival = Process(
            name="Standby Arrival",
            scenario=scenario,
            dispatchable=False,
        )
        clean = Process(
            name="Arrival Cleaning",
            scenario=scenario,
            dispatchable=False,
            duration=timedelta(minutes=30),
        )
        charging = Process(
            name="Charging",
            scenario=scenario,
            dispatchable=False,
            electric_power=charging_power,
        )
        standby_departure = Process(
            name="Standby Pre-departure",
            scenario=scenario,
            dispatchable=True,
        )
        session.add(standby_arrival)
        session.add(clean)
        session.add(charging)
        session.add(standby_departure)

        for vehicle_type in scenario.vehicle_types:
            list_of_rotations = [
                rotation
                for rotation in scenario.rotations
                if rotation.vehicle_type_id == vehicle_type.id
            ]

            max_vehicle_num = (
                session.query(Rotation)
                .filter(Rotation.vehicle_type_id == vehicle_type.id)
                .filter(Rotation.scenario_id == scenario.id)
                .count()
            )

            # Create areas
            if max_vehicle_num != 0:
                if capacity is None:
                    # Assuming each vehicle only be assigned to one rotation
                    parking_capacity = max_vehicle_num

                else:
                    parking_capacity = capacity

                # Create stand by arrival area
                arrival_area = Area(
                    scenario=scenario,
                    name=f"Arrival for {vehicle_type.name_short}",
                    depot=depot,
                    area_type=AreaType.DIRECT_ONESIDE,
                    capacity=parking_capacity,
                )
                session.add(arrival_area)
                arrival_area.vehicle_type = vehicle_type

                # Create charging area
                charging_area = Area(
                    scenario=scenario,
                    name=f"Direct Charging Area for {vehicle_type.name_short}",
                    depot=depot,
                    area_type=AreaType.DIRECT_ONESIDE,
                    capacity=parking_capacity,
                )
                session.add(charging_area)
                charging_area.vehicle_type = vehicle_type

                # Create cleaning area

                list_of_rotations.sort(key=lambda x: x.trips[-1].arrival_time)

                if capacity is None:
                    clean_capacity = 1

                    # Maximum number of vehicles that can park in the cleaning area according to rotation
                    for rot_idx in range(0, len(list_of_rotations)):
                        cleaning_interval_start = (
                            list_of_rotations[rot_idx].trips[-1].arrival_time
                        )

                        # Potential improvement: the "edge" between copy and non-copy schedules might need higher cleaning
                        #  capacity than real, causing standby-arrival events. Considering adding repetition_period here
                        # This could be solved by implementing a sliging window that rolls over from e.g. sunday (last
                        # day to monday (first day) again, to calculate the load from both the beginning and end of the
                        # data.
                        for next_rot_idx in range(rot_idx + 1, len(list_of_rotations)):
                            arrival_time = (
                                list_of_rotations[next_rot_idx].trips[-1].arrival_time
                            )

                            if arrival_time > cleaning_interval_start + clean.duration:
                                clean_capacity = max(
                                    clean_capacity, next_rot_idx - rot_idx
                                )
                                break

                else:
                    clean_capacity = capacity

                cleaning_area = Area(
                    scenario=scenario,
                    name=f"Cleaning Area for {vehicle_type.name_short}",
                    depot=depot,
                    area_type=AreaType.DIRECT_ONESIDE,
                    capacity=clean_capacity,
                )

                session.add(cleaning_area)
                cleaning_area.vehicle_type = vehicle_type

                arrival_area.processes.append(standby_arrival)
                cleaning_area.processes.append(clean)
                charging_area.processes.append(charging)
                charging_area.processes.append(standby_departure)

        assocs = [
            AssocPlanProcess(
                scenario=scenario, process=standby_arrival, plan=plan, ordinal=0
            ),
            AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
            AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=2),
            AssocPlanProcess(
                scenario=scenario, process=standby_departure, plan=plan, ordinal=3
            ),
        ]
        session.add_all(assocs)


def simulate_scenario(
    scenario: Union[Scenario, int, Any],
    simple_consumption_simulation: bool = False,
    repetition_period: Optional[timedelta] = None,
    calculate_exact_vehicle_count: bool = True,
    database_url: Optional[str] = None,
) -> None:
    """
    This method simulates a scenario and adds the results to the database. It fills in the "Charging Events" in the
    :class:`eflips.model.Event` table and associates :class:`eflips.model.Vehicle` objects with all the existing
    "Driving Events" in the :class:`eflips.model.Event` table.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
           an integer specifying the ID of a scenario in the database. Or any other object that has an attribute
           `id` that is an integer. If no :class:`eflips.model.Scenario` object is passed, the `database_url`
           parameter must be set to a valid database URL ot the environment variable `DATABASE_URL` must be set to a
           valid database URL.

    :param simple_consumption_simulation: A boolean flag indicating whether the simulation should be run in
        "simple consumption" mode. In this mode, the vehicle consumption is calculated using a simple formula and
        existing driving events are ignored. This is useful for testing purposes.

    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. This
        is needed because the *result* should be a steady-state result. THis can only be achieved by simulating a
        time period before and after our actual simulation, and then only using the "middle". eFLIPS tries to
        automatically detect whether the schedule should be repeated daily or weekly. If this fails, a ValueError is
        raised and repetition needs to be specified manually.

    :param calculate_exact_vehicle_count: A boolean flag indicating whether the exact number of vehicles should be
        calculated. If this is set to True, the simulation will be run twice. The first time, the number of vehicles
        will be calculated using the number of trips for each vehicle type. The second time, the number of vehicles
        will be set to the calculated number of vehicles.

    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
        :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a valid database
        URL.

    :return: Nothing. The results are added to the database.
    """

    # Step 0: Load the scenario
    with create_session(scenario, database_url) as (session, scenario):
        simulation_host = _init_simulation(
            scenario=scenario,
            session=session,
            simple_consumption_simulation=simple_consumption_simulation,
            repetition_period=repetition_period,
        )

        ev = _run_simulation(simulation_host)
        # TODO Only commit second simulation results into the database

        if calculate_exact_vehicle_count:
            vehicle_counts = ev.nvehicles_used_calculation()
            simulation_host = _init_simulation(
                scenario=scenario,
                session=session,
                simple_consumption_simulation=simple_consumption_simulation,
                repetition_period=repetition_period,
                vehicle_count_dict=vehicle_counts,
            )
            ev = _run_simulation(simulation_host)

        _add_evaluation_to_database(scenario.id, ev, session)


def _init_simulation(
    scenario: Scenario,
    session: Session,
    simple_consumption_simulation: bool = False,
    repetition_period: Optional[timedelta] = None,
    vehicle_count_dict: Optional[Dict[str, int]] = None,
) -> SimulationHost:
    """
    This methods checks the input data for consistency, initializes a simulation host object and returns it. The
    simulation host object can then be passed to :func:`run_simulation()`.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.
    :param session: A SQLAlchemy session object. This is used to add all the simulation results to the database.

    :param simple_consumption_simulation: A boolean flag indicating whether the simulation should be run in
        "simple consumption" mode. In this mode, the vehicle consumption is calculated using a simple formula and
        existing driving events are ignored. This is useful for testing purposes.

    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. This
        is needed because the *result* should be a steady-state result. THis can only be achieved by simulating a
        time period before and after our actual simulation, and then only using the "middle". eFLIPS tries to
        automatically detect whether the schedule should be repeated daily or weekly. If this fails, a ValueError is
        raised and repetition needs to be specified manually.

    :param vehicle_count_dict: An optional dictionary specifying the number of vehicles for each vehicle type. If this
        is not specified, the number of vehicles is assumed to be 10 times the number of trips for each vehicle type.

    :return: A :class:`eflips.depot.Simulation.SimulationHost` object. This object should be reagrded as a "black box"
        by the user. It should be passed to :func:`run_simulation()` to run the simulation and obtain the results.
    """
    # Clear the eflips settings
    eflips.settings.reset_settings()

    path_to_this_file = os.path.dirname(__file__)

    # Step 1: Set up the depot
    if len(scenario.depots) == 1:
        # Create an eFlips depot
        depot_dict = depot_to_template(scenario.depots[0])  # type: ignore
        eflips_depot = eflips.depot.Depotinput(
            filename_template=depot_dict, show_gui=False
        )
    elif len(scenario.depots) > 1:
        raise ValueError("Only one depot is supported at the moment.")
    else:
        raise ValueError("No depot found in scenario.")
    depot_id = "DEFAULT"

    # Step 2: eFLIPS initialization
    simulation_host = SimulationHost([eflips_depot], print_timestamps=False)
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
            use_builtin_consumption_model=simple_consumption_simulation,
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
    # We need to calculate roughly how many vehicles we need
    # We do that by taking the total trips for each vehicle class and creating 10 times the number of vehicles
    # for each vehicle type in the vehicle class
    all_vehicle_types = set([rotation.vehicle_type for rotation in scenario.rotations])

    if vehicle_count_dict is not None:
        if set(vehicle_count_dict.keys()) != set(
            [str(vehicle_type.id) for vehicle_type in all_vehicle_types]
        ):
            raise ValueError(
                "The vehicle count dictionary does not contain all vehicle types."
            )
        vehicle_count = vehicle_count_dict
    else:
        vehicle_count = {}
        for vehicle_type in all_vehicle_types:
            trip_count = sum(
                [
                    1 if rotation.vehicle_type == vehicle_type else 0
                    for rotation in scenario.rotations
                ]
            )
            vehicle_count[str(vehicle_type.id)] = 10 * trip_count

    # Now we put the vehicle count into the settings
    eflips.globalConstants["depot"]["vehicle_count"][depot_id] = {}
    for vehicle_type, count in vehicle_count.items():
        eflips.globalConstants["depot"]["vehicle_count"][depot_id][vehicle_type] = count

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
    """Run simulation and return simulation results

    :param simulation_host: A "black box" object containing all input data for the simulation.

    :return: Object of :class:`eflips.depot.evaluation.DepotEvaluation` containing the simulation results.
    """
    simulation_host.run()
    ev = simulation_host.depot_hosts[0].evaluation

    return ev


def _add_evaluation_to_database(
    scenario_id: int,
    depot_evaluation: DepotEvaluation,
    session: sqlalchemy.orm.Session,
) -> None:
    """
    This method reads the simulation results from the :class:`eflips.depot.evaluation.DepotEvaluation` object and
    adds them into the database. Tables of Event, Rotation and Vehicle will be updated.

    :param scenario_id: the unique identifier of this simulated scenario. Needed for creating
           :class:`eflips.model.Event` objects.

    :param depot_evaluation: the :class:`eflips.depot.evaluation.DepotEvaluation` object containing the simulation
           results.

    :param session: a SQLAlchemy session object. This is used to add all the simulation results to the
           database.

    :return: Nothing. The results are added to the database.
    """

    # Read simulation start time
    simulation_start_time = depot_evaluation.sim_start_datetime

    # Initialization of empty lists

    list_of_vehicles = []

    list_of_events = []

    list_of_assigned_schedules = []

    # Read results from depot_evaluation categorized by vehicle
    for current_vehicle in depot_evaluation.vehicle_generator.items:
        list_of_events_per_vehicle = []

        vehicle_type_id = int(current_vehicle.vehicle_type.ID)

        # Create a Vehicle object for database
        current_vehicle_db = Vehicle(
            vehicle_type_id=vehicle_type_id,
            scenario_id=scenario_id,
            name=current_vehicle.ID,
            name_short=None,
        )
        # Flush the vehicle object to get the vehicle id
        session.add(current_vehicle_db)
        session.flush()
        list_of_vehicles.append(current_vehicle_db)

        dict_of_events = {}

        for finished_trip in current_vehicle.finished_trips:
            dict_of_events[finished_trip.atd] = {
                "type": "trip",
                "is_copy": finished_trip.is_copy,
                "id": finished_trip.ID,
            }

            if finished_trip.is_copy is False:
                assigned_schedule_id = int(finished_trip.ID)
                list_of_assigned_schedules.append(
                    (assigned_schedule_id, current_vehicle_db.id)
                )

        # Generate a dictionary of data logs from DepotEvaluation with time as keys. Logs for repeated schedules
        # and their depot processes are included but will not be written into database.

        last_standby_departure_start = 0

        # For convenience
        area_log = current_vehicle.logger.loggedData["dwd.current_area"]
        slot_log = current_vehicle.logger.loggedData["dwd.current_slot"]

        # For future uses
        waiting_log = current_vehicle.logger.loggedData["area_waiting_time"]
        battery_log = current_vehicle.battery_logs

        for start_time, process_log in current_vehicle.logger.loggedData[
            "dwd.active_processes_copy"
        ].items():
            if len(process_log) == 0:
                # A departure happens
                if last_standby_departure_start != 0:
                    # Update the last standby-departure end time
                    dict_of_events[last_standby_departure_start]["end"] = start_time
                else:
                    continue

            else:
                for process in process_log:
                    match process.status:
                        case ProcessStatus.CANCELLED:
                            raise NotImplementedError(
                                f"Cancelled processes {process.ID} are not implemented."
                            )
                        case ProcessStatus.COMPLETED:
                            assert (
                                len(process.starts) == 1 and len(process.ends) == 1
                            ), (
                                f"Current process {process.ID} is completed and should only contain one start and one "
                                f"end time."
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

                                if start_time in dict_of_events:
                                    assert current_area.issink is True
                                    # Standby departure
                                    actual_start_time = dict_of_events[start_time][
                                        "end"
                                    ]
                                    dict_of_events[actual_start_time] = {
                                        "type": type(process).__name__,
                                        "area": current_area.ID,
                                        "is_area_sink": current_area.issink,
                                        "slot": current_slot,
                                        "id": process.ID,
                                    }

                                    last_standby_departure_start = actual_start_time

                                else:
                                    # Standby arrival
                                    assert current_area.issink is False
                                    dict_of_events[start_time] = {
                                        "type": type(process).__name__,
                                        "area": current_area.ID,
                                        "is_area_sink": current_area.issink,
                                        "slot": current_slot,
                                        "id": process.ID,
                                    }
                        case ProcessStatus.IN_PROGRESS:
                            raise NotImplementedError(
                                f"Current process {process.ID} is in progress. Not implemented yet."
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

        dict_of_events = dict(sorted(dict_of_events.items()))

        # Generating valid event-list
        is_copy = True

        for start_time, process_dict in dict_of_events.items():
            if process_dict["type"] == "trip":
                is_copy = process_dict["is_copy"]
                finished_trip_id = process_dict["id"]
                # Get trip_id
                # Here or elsewhere get db v id
            else:
                if is_copy is False:
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
                        case _:
                            raise ValueError(
                                """Invalid process type %s. Valid process types are "Serve", "Charge", "Standby", 
                                "Precondition"""
                            )

                    # End time of 0-duration processes are start time of the next process

                    if "end" not in process_dict:
                        # TODO might optimise performance
                        end_time = list(dict_of_events.keys())[
                            list(dict_of_events.keys()).index(start_time) + 1
                        ]
                        process_dict["end"] = end_time

                    # Get soc
                    soc_start = None
                    soc_end = None
                    for log in battery_log:
                        if log.t == start_time:
                            soc_start = log.energy / log.energy_real
                        if log.t == process_dict["end"]:
                            soc_end = log.energy / log.energy_real

                    current_event = Event(
                        scenario_id=scenario_id,
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

                    list_of_events_per_vehicle.append(current_event)

        list_of_events.extend(list_of_events_per_vehicle)

    # Update rotation table with vehicle ids
    old_new_vehicle = []
    matched_vehicle_id = 0
    for schedule_id, vehicle_id in list_of_assigned_schedules:
        if vehicle_id != matched_vehicle_id:
            matched_vehicle_id = vehicle_id
            # Get rotation from db with id
            rotation_q = (
                session.query(Rotation)
                .filter(Rotation.scenario_id == scenario_id, Rotation.id == schedule_id)
                .one()
            )
            old_vehicle_id = rotation_q.vehicle_id
            old_new_vehicle.append((old_vehicle_id, vehicle_id))

    # Update depot events with old vehicle id

    # Write Events
    session.add_all(list_of_events)

    for old_vehicle_id, new_vehicle_id in old_new_vehicle:
        session.query(Event).filter(
            Event.scenario_id == scenario_id,
            Event.vehicle_id == new_vehicle_id,
        ).update({"vehicle_id": old_vehicle_id}, synchronize_session=False)
