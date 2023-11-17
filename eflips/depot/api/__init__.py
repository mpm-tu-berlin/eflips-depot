"""
This package contains the public API for eFLIPS-Depot. It is split two ways:

by function
    The methods for setting up and starting a simulation are in the top-level :mod:`eflips.depot.api` module. The data
    structures for the simulation input are in the :mod:`eflips.depot.api.input` module. The output data structures are
    in the :mod:`eflips.depot.api.output` module.

by interface
    The top-level input and output modules contain only the definitions of the data structures. For interfacing with
    other simulation frameworks, a *separate* module should be created that implements the interface. For example, the
    :mod:`eflips.depot.api.django_simba` module implements the interface for the
    `django-simba <(https://github.com/rl-institut/django-simba)>`_ framework. It is recommended to create subclasses of
    the :class:`eflips.depot.api.input.VehicleType` and :class:`eflips.depot.api.input.VehicleSchedule` classes,
    overriding the :meth:`__init__()` method to read the data from the other simulation framework.

"""
import os
import warnings
from datetime import timedelta, datetime
from math import ceil
from typing import List, Optional, Dict, Hashable, Tuple

import eflips.depot
from eflips.depot import SimulationHost, DepotEvaluation
from eflips.depot.api.input import VehicleType, VehicleSchedule, Depot


def init_simulation(
    vehicle_types: List[VehicleType],
    vehicle_schedules: List[VehicleSchedule],
    vehicle_counts: Optional[Dict[Hashable, int]] = None,
    depot: Depot = None,
    repetition_period: Optional[timedelta] = None,
) -> SimulationHost:
    """
    This methods checks the input data for consistency, initializes a simulation host object and returns it. The
    simulation host object can then be passed to :func:`run_simulation()`.

    :param vehicle_types: A list of :class:`eflips.depot.api.input.VehicleType` objects. There should be at least one
        vehicle type for each `vehicle_class` referenced in the VehicleSchedule.
    :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    :param vehicle_counts: A dictionary mapping vehicle type IDs to the number of vehicles of that type. If `None`, a
        very high vehicle count will be used. It is expected that the simulation will be run twice, once to estimate the
        number of vehicles needed and once to run the simulation with the correct number of vehicles.
    :param depot: A :class:`eflips.depot.api.input.Depot` object. If `None`, a default depot will be created.
    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. This
        is needed because the *result* should be a steady-state result. THis can only be achieved by simulating a
        time period before and after our actual simulation, and then only using the "middle". eFLIPS tries to
        automatically detect whether the schedule should be repeated daily or weekly. If this fails, a ValueError is
        raised and repetition needs to be specified manually.

    :return: A :class:`eflips.depot.Simulation.SimulationHost` object. This object should be reagrded as a "black box"
        by the user. It should be passed to :func:`run_simulation()` to run the simulation and obtain the results.
    """

    # Clear the eflips settings
    eflips.settings.reset_settings()

    path_to_this_file = os.path.dirname(__file__)

    # Step 1: Set up the depot
    if depot is not None:
        depot.validate()

        # Create an eFlips depot
        depot_dict = depot._to_template()
        eflips_depot = eflips.depot.Depotinput(
            filename_template=depot_dict, show_gui=False
        )
    else:
        warnings.warn("Smart default depot not implemented yet")
        path_to_default_depot = os.path.join(
            path_to_this_file, "defaults", "default_depot"
        )
        eflips_depot = eflips.depot.Depotinput(
            filename_template=path_to_default_depot, show_gui=False
        )

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
    # Turn API VehicleSchedule objects into eFLIPS TimeTable object
    # Get correctly repeated vehicle schedules
    # if total duration time is 1 or 2 days, vehicle schedule will be repeated daily
    # if total duration time is 7 or 8 days, vehicle schedule will be repeated weekly
    # However, we need to override quite a lot of the settings
    # The ["general"]["SIMULATION_TIME"] entry is calculated from the difference between the first and last departure
    # time in the vehicle schedule

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

    vehicle_schedules = _repeat_vehicle_schedules(vehicle_schedules, repetition_period)

    sim_start_stime, total_duration_seconds = _start_and_end_times(vehicle_schedules)
    eflips.globalConstants["general"]["SIMULATION_TIME"] = int(total_duration_seconds)

    timetable = VehicleSchedule._to_timetable(
        vehicle_schedules, simulation_host.env, sim_start_stime
    )
    simulation_host.timetable = timetable

    # Step 4: Set up the vehicle types
    # We need to calculate roughly how many vehicles we need
    # We do that by taking the total trips for each vehicle class and creating 100 times the number of vehicles
    # for each vehicle type in the vehicle class
    all_vehicle_classes = set(
        [vehicle_schedule.vehicle_class for vehicle_schedule in vehicle_schedules]
    )
    vehicle_count = {}
    for vehicle_class in all_vehicle_classes:
        trip_count = sum(
            [
                1 if vehicle_schedule.vehicle_class == vehicle_class else 0
                for vehicle_schedule in vehicle_schedules
            ]
        )
        vehicle_types_with_vehicle_class = [
            vehicle_type
            for vehicle_type in vehicle_types
            if vehicle_type.vehicle_class == vehicle_class
        ]
        for vehicle_type in vehicle_types_with_vehicle_class:
            if vehicle_counts is not None and vehicle_type.id in vehicle_counts:
                vehicle_count[vehicle_type.id] = vehicle_counts[vehicle_type.id]
            else:
                vehicle_count[vehicle_type.id] = int(
                    ceil(trip_count * 100 * len(vehicle_types_with_vehicle_class))
                )
    # Now we put the vehicle count into the settings
    eflips.globalConstants["depot"]["vehicle_count"][depot_id] = {}
    for vehicle_type, count in vehicle_count.items():
        eflips.globalConstants["depot"]["vehicle_count"][depot_id][vehicle_type] = count

    # We  need to put the vehicle type objects into the GlobalConstants
    for vehicle_type in vehicle_types:
        eflips.globalConstants["depot"]["vehicle_types"][
            vehicle_type.id
        ] = vehicle_type._to_global_constants_dict()

    # We need to fill out the substitutable types, which is a list of lists of vehicle type IDs for a vehicle class
    for vehicle_class in all_vehicle_classes:
        vehicle_types_with_vehicle_class = [
            vehicle_type
            for vehicle_type in vehicle_types
            if vehicle_type.vehicle_class == vehicle_class
        ]
        eflips.globalConstants["depot"]["substitutable_types"].append(
            [vehicle_type.id for vehicle_type in vehicle_types_with_vehicle_class]
        )

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


def _repeat_vehicle_schedules(
    vehicle_schedules: List[VehicleSchedule], repetition_period: timedelta
) -> List[VehicleSchedule]:
    """
    This method repeats the vehicle schedules in the list `vehicle_schedules` by the timedelta `repetition_period`.

    It takes the given vehicle schedules and creates two copies, one `repetition_period` earlier, one `repetition_period`
    later. It then returns the concatenation of the three lists.

    :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    :param repetition_period: A timedelta object specifying the period of the vehicle schedules.
    :return: a list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    """
    # Add the repeated schedules to the forward and backward lists
    schedule_list_backward = []
    schedule_list_forward = []

    for vehicle_schedule in vehicle_schedules:
        schedule_list_backward.append(vehicle_schedule.repeat(-repetition_period))
        schedule_list_forward.append(vehicle_schedule.repeat(repetition_period))

    vehicle_schedules = (
        schedule_list_backward + vehicle_schedules + schedule_list_forward
    )

    return vehicle_schedules


def _start_and_end_times(vehicle_schedules) -> Tuple[datetime, int]:
    """
    This method is used to find the start time and duration for simulating a given list of vehicle schedules.
    It finds the times of midnight of the day of the first departure and midnight of the day after the last arrival.

    :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    :return: The datetime of midnight of the day of the first departure and the total duration of the simulation in
        seconds.
    """

    first_departure_time = min(
        [vehicle_schedule.departure for vehicle_schedule in vehicle_schedules]
    )
    midnight_of_first_departure_day = first_departure_time.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_arrival_time = max(
        [vehicle_schedule.arrival for vehicle_schedule in vehicle_schedules]
    )
    midnight_of_last_arrival_day = last_arrival_time.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    midnight_after_last_arrival_day = midnight_of_last_arrival_day + timedelta(days=1)
    total_duration_seconds = (
        midnight_after_last_arrival_day - midnight_of_first_departure_day
    ).total_seconds()

    return midnight_of_first_departure_day, total_duration_seconds


def _validate_input_data(
    vehicle_types: List[VehicleType], vehicle_schedule: List[VehicleSchedule]
):
    """
    This method checks if the VehicleSchedule "matches" the VehicleType. For each VehicleClass suggested in the
    VehicleSchedule, there should be a VehicleType with the same VehicleClass.

    In this version of the API, there should be *exactly* one VehicleType for each VehicleClass. We will move to
    "at least one" in the future.

    :param vehicle_types: A list of :class:`eflips.depot.api.input.VehicleType` objects.
    :param vehicle_schedule: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    :raises AssertionError: If there is a VehicleClass in the VehicleSchedule that does not have a corresponding
        VehicleType.
    """

    for vehicle_schedule in vehicle_schedule:
        if_vehicle_type_found = False

        # get vehicle class from vehicle schedule
        vehicle_class = vehicle_schedule.vehicle_class

        # search vehicle type with the same vehicle class
        for vehicle_type in vehicle_types:
            if vehicle_type.vehicle_class == vehicle_class:
                if_vehicle_type_found = True
                break

        if not if_vehicle_type_found:
            raise AssertionError(
                f"VehicleType with vehicle_class {vehicle_class} not found"
            )

        # FOr this API version, we also check that there is only one vehicle type per vehicle class
        # We will move to "at least one" in the future
        assert len(set([t.vehicle_class for t in vehicle_types])) == len(
            vehicle_types
        ), "There should be exactly one vehicle type per vehicle class"


def run_simulation(simulation_host: SimulationHost) -> DepotEvaluation:
    """Run simulation and return simulation results

    :param simulation_host: A "black box" object containing all input data for the simulation.
    :return: Object of :class:`eflips.depot.evaluation.DepotEvaluation` containing the simulation results.
    """
    simulation_host.run()
    ev = simulation_host.depot_hosts[0].evaluation

    return ev
