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
from datetime import timedelta
from math import ceil
from typing import Any, Dict, Optional, Union

import sqlalchemy.orm
from eflips.model import Event, EventType, Rotation, Scenario, Vehicle
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import eflips.depot
from eflips.depot import DepotEvaluation, SimulationHost
from eflips.depot.api.private import (
    depot_to_template,
    repeat_vehicle_schedules,
    start_and_end_times,
    vehicle_type_to_global_constants_dict,
    VehicleSchedule,
)


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
    if isinstance(scenario, Scenario):
        session = inspect(scenario).session
        do_close_session = False
    elif isinstance(scenario, int) or hasattr(scenario, "id"):
        do_close_session = True
        if isinstance(scenario, int):
            scenario_id = scenario
        else:
            scenario_id = scenario.id

        if database_url is None:
            if "DATABASE_URL" in os.environ:
                database_url = os.environ.get("DATABASE_URL")
            else:
                raise ValueError("No database URL specified.")

        engine = create_engine(database_url)
        session = Session(engine)
        scenario = session.query(Scenario).filter(Scenario.id == scenario_id).one()
    else:
        raise ValueError(
            "The scenario parameter must be either a Scenario object, an integer or an object with an 'id' attribute."
        )

    simulation_host = _init_simulation(
        scenario=scenario,
        simple_consumption_simulation=simple_consumption_simulation,
        repetition_period=repetition_period,
    )

    ev = _run_simulation(simulation_host)

    if calculate_exact_vehicle_count:
        vehicle_counts = ev.nvehicles_used_calculation()
        simulation_host = _init_simulation(
            scenario=scenario,
            simple_consumption_simulation=simple_consumption_simulation,
            repetition_period=repetition_period,
            vehicle_count_dict=vehicle_counts,
        )
        ev = _run_simulation(simulation_host)

    _add_evaluation_to_database(scenario.id, ev, session)

    if do_close_session:
        session.close()


def _init_simulation(
    scenario: Scenario,
    simple_consumption_simulation: bool = False,
    repetition_period: Optional[timedelta] = None,
    vehicle_count_dict: Optional[Dict[str, int]] = None,
) -> SimulationHost:
    """
    This methods checks the input data for consistency, initializes a simulation host object and returns it. The
    simulation host object can then be passed to :func:`run_simulation()`.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.

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
            rotation, use_builtin_consumption_model=simple_consumption_simulation
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

    # If the database already contains non-driving events for this scenario, then we cannot add driving events
    non_driving_event_q = (
        session.query(Event)
        .filter(Event.scenario_id == scenario_id)
        .filter(Event.event_type != EventType.DRIVING)
    )
    if non_driving_event_q.count() > 0:
        raise ValueError(
            "The database already contains non-driving events for this scenario. Please delete them first."
        )

    # If the database contains no driving events for this scenario, then we need to add them
    driving_event_q = (
        session.query(Event)
        .filter(Event.scenario_id == scenario_id)
        .filter(Event.event_type == EventType.DRIVING)
    )
    if driving_event_q.count() == 0:
        for rot in (
            session.query(Rotation).filter(Rotation.scenario_id == scenario_id).all()
        ):
            current_soc = 1.0
            for trip in rot.trips:
                energy = (trip.route.distance / 1000) * rot.vehicle_type.consumption
                soc_start = current_soc
                soc_end = current_soc - (energy / rot.vehicle_type.battery_capacity)
                current_soc = soc_end

                # Create a driving event
                current_event = Event(
                    scenario_id=scenario_id,
                    vehicle_type_id=rot.vehicle_type_id,
                    trip_id=trip.id,
                    time_start=trip.departure_time,
                    time_end=trip.arrival_time,
                    soc_start=soc_start,
                    soc_end=soc_end,
                    event_type=EventType.DRIVING,
                    description=f"`VehicleType.consumption`-based driving event for trip {trip.id}.",
                    timeseries=None,
                )
                session.add(current_event)

    # Read results from depot_evaluation categorized by vehicle
    for current_vehicle in depot_evaluation.vehicle_generator.items:
        list_of_events_per_vehicle = []

        vehicle_type_id = int(current_vehicle.vehicle_type.ID)

        vehicle_id = depot_evaluation.vehicle_generator.items.index(current_vehicle) + 1

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

        for finished_trip in current_vehicle.finished_trips:
            if finished_trip.is_copy is False:
                assigned_schedule_id = int(finished_trip.ID)
                list_of_assigned_schedules.append(
                    (assigned_schedule_id, current_vehicle_db.id)
                )

        # Read processes of this vehicle
        list_of_timekeys = list(
            current_vehicle.logger.loggedData["dwd.active_processes_copy"].keys()
        )
        list_of_timekeys.sort()

        battery_logs = current_vehicle.battery_logs

        for time_key in list_of_timekeys:
            process_list = current_vehicle.logger.loggedData[
                "dwd.active_processes_copy"
            ][time_key]
            current_area = current_vehicle.logger.loggedData["dwd.current_area"][
                time_key
            ]
            # Only concerning processes not being cancelled

            if process_list is not None and current_area is not None:
                for current_process in process_list:
                    if (
                        len(current_process.starts) is not 1
                        or len(current_process.ends) is not 1
                    ):
                        raise NotImplementedError(
                            f"Restart of process {current_process.ID} is not implemented."
                        )

                    # Get start time

                    # if it is a standby event starting at the same time with other processes
                    if (
                        len(process_list) is not 1
                        and type(current_process).__name__ == "Standby"
                    ):
                        # Start time of this event is the end time of the previous event
                        event_start_after_simulation = timedelta(
                            seconds=process_list[0].ends[0]
                        )

                    else:
                        event_start_after_simulation = timedelta(
                            seconds=current_process.starts[0]
                        )

                    time_start = simulation_start_time + event_start_after_simulation

                    # Get end time
                    if current_process.dur > 0:
                        # If this process has a valid duration then directly use end time
                        event_end_after_simulation = timedelta(
                            seconds=current_process.ends[0]
                        )
                        time_end = simulation_start_time + event_end_after_simulation

                    else:
                        # If this process has a duration of 0, then end time is the start time of the next process

                        start_time_index = list_of_timekeys.index(
                            current_process.starts[0]
                        )
                        if start_time_index == len(list_of_timekeys) - 1:
                            # This is the last process in the list. Use end of simulation time as event end time
                            end_time_sec = depot_evaluation.SIM_TIME

                        else:
                            end_time_sec = list_of_timekeys[start_time_index + 1]

                        event_end_after_simulation = timedelta(seconds=end_time_sec)
                        time_end = simulation_start_time + event_end_after_simulation

                    # Get EventType
                    match type(current_process).__name__:
                        case "Serve":
                            event_type = EventType.SERVICE
                        case "Charge":
                            event_type = EventType.CHARGING_DEPOT
                        case "Standby":
                            if current_area.issink is True:
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

                    # Get proper area and subloc id

                    area_id = int(current_area.ID)

                    slot_id = current_vehicle.logger.loggedData["dwd.current_slot"][
                        time_key
                    ]
                    subloc_id = slot_id if slot_id is not None else None

                    # Read battery logs for timeseries

                    if len(battery_logs) == 0:
                        raise ValueError("No battery logs found.")

                    for log in battery_logs:
                        # Charging starts
                        if (
                            log.t == current_process.starts[0]
                            and log.event_name == "charge_start"
                            and type(current_process).__name__ == "Charge"
                        ):
                            soc_start = log.energy / log.energy_real

                        if (
                            log.t == current_process.starts[0]
                            and log.event_name == "charge_step"
                            and type(current_process).__name__ == "Charge"
                        ):
                            raise NotImplementedError("Charge step is not implemented.")

                        # Charging ends
                        if (
                            log.t == current_process.ends[0]
                            and log.event_name == "charge_end"
                            and type(current_process).__name__ == "Charge"
                        ):
                            soc_end = log.energy / log.energy_real

                        if (
                            # Vehicle stands by for departure
                            log.t == current_process.starts[0]
                            and log.event_name == "charge_start"
                            and type(current_process).__name__ == "Standby"
                        ):
                            # get the charge_end event
                            idx = battery_logs.index(log)
                            charge_end_log = battery_logs[idx + 1]
                            soc_start = charge_end_log.energy / log.energy_real
                            soc_end = soc_start

                        # Vehicle on a trip
                        if log.t == time_key and log.event_name == "consume_start":
                            pass

                        if (
                            # Vehicle arrives
                            log.t == current_process.starts[0]
                            and log.event_name == "consume_end"
                        ) or (
                            # Preconditioning
                            log.t == current_process.ends[0]
                            and log.event_name == "consume_start"
                        ):
                            # No battery consumption
                            soc_start = log.energy / log.energy_real

                            soc_end = soc_start

                    current_event = Event(
                        scenario_id=scenario_id,
                        vehicle_type_id=vehicle_type_id,
                        vehicle=current_vehicle_db,
                        station_id=None,
                        area_id=area_id,
                        subloc_no=subloc_id,
                        trip_id=None,
                        time_start=time_start,
                        time_end=time_end
                        - timedelta(seconds=1),  # Avoiding overlapping
                        soc_start=soc_start,
                        soc_end=soc_end,
                        event_type=event_type,
                        description=None,
                        timeseries=None,
                    )

                    list_of_events_per_vehicle.append(current_event)

        list_of_events.extend(list_of_events_per_vehicle)

    # Update rotation table with vehicle ids
    for schedule_id, vehicle_id in list_of_assigned_schedules:
        rotation_q = session.query(Rotation).filter(
            Rotation.id == schedule_id, Rotation.scenario_id == scenario_id
        )
        if rotation_q.count() == 0:
            raise ValueError(
                f"Could not find Rotation {schedule_id} in scenario {scenario_id}."
            )
        else:
            rotation_q.update({"vehicle_id": vehicle_id})
            for trip in rotation_q.one().trips:
                for event in trip.events:
                    assert event.vehicle_id is None
                    assert event.event_type == EventType.DRIVING
                    event.vehicle_id = vehicle_id
                    event.time_end = event.time_end - timedelta(seconds=1)

    # Write Events
    session.add_all(list_of_events)
