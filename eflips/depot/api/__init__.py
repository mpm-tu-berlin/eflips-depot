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
import warnings
from datetime import timedelta
from math import ceil
from typing import Optional, Union, Any, Dict

import sqlalchemy.orm
from eflips.model import (
    Scenario,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import eflips.depot
from eflips.depot import SimulationHost, DepotEvaluation
from eflips.depot.api.private import (
    VehicleSchedule,
    depot_to_template,
    vehicle_type_to_global_constants_dict,
    repeat_vehicle_schedules,
    start_and_end_times,
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
        session = None
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

    _add_evaluation_to_database(ev, session)

    if session is not None:
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
        depot_dict = depot_to_template(scenario.depots[0])
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
    depot_evaluation: DepotEvaluation,
    session: sqlalchemy.orm.Session,
) -> None:
    warnings.warn(
        "NOT adding any events to the database, since this is not implemented yet."
    )
