"""
This package contains the public API for eFLIPS-Depot. It is split to ways:

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
from typing import List

from depot import SimulationHost, DepotEvaluation
from depot.api.input import VehicleType, VehicleSchedule, Depot


def init_simulation(
    vehicle_types: List[VehicleType],
    vehicle_schedules: List[VehicleSchedule],
    depot: Depot = None,
) -> SimulationHost:
    """
    This methods checks the input data for consistency, initializes a simulation host object and returns it. The
    simulation host object can then be passed to :func:`run_simulation()`.

    :param vehicle_types: A list of :class:`eflips.depot.api.input.VehicleType` objects. There should be at least one
        vehicle type for each `vehicle_class` referenced in the VehicleSchedule.
    :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    :param depot: A :class:`eflips.depot.api.input.Depot` object. If `None`, a default depot will be created.
    :return: A :class:`eflips.depot.Simulation.SimulationHost` object. This object should be reagrded as a "black box"
        by the user. It should be passed to :func:`run_simulation()` to run the simulation and obtain the results.
    """

    # Check input data

    # Create simulation host
    simulation_host = NotImplementedError

    # Turn API VehicleSchedule objects into eFLIPS TimeTable object
    timetable = NotImplementedError

    ### In g areeneral, after obtaining the simulation host we will do the things
    ### that done in `standard_setuo()` from the old input files here,
    ### but instead use the API objects.

    ### For example:
    # vehicle types by giving vehicle type objects (avoiding the unpacking of JSON files done in complete_gc())
    # vehicle count information (by creating a very very large number of vehicles for each type, e.g 10 times the number of vehicles in the schedule)

    # Add timetable to simulation host
    simulation_host.timetable = timetable

    raise NotImplementedError


def _validate_input_data(
    vehicle_types: List[VehicleType], vehicle_schedule: List[VehicleSchedule]
):
    """
    This method checks if the VehicleSchedule "matches" the VehicleType. For each VehicleClass suggested in the
    VehicleSchedule, there should be a VehicleType with the same VehicleClass.

    In this version of the API, there should be *exactly* one VehicleType for each VehicleClass. We will move to
    "at least one" in the future.

    :param vehicle_types: A list of :class:`eflips.depot.api.input.VehicleType`
    :param vehicle_schedule: A list of :class:`eflips.depot.api.input.VehicleSchedule´
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
