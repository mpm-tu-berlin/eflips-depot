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

    raise NotImplementedError


def run_simulation(simulation_host: SimulationHost) -> DepotEvaluation:
    """Run simulation and return simulation results

    :param simulation_host: A "black box" object containing all input data for the simulation.
    :return: Object of :class:`eflips.depot.evaluation.DepotEvaluation` containing the simulation results.
    """
    simulation_host.run()
    ev = simulation_host.depot_hosts[0].evaluation

    return ev
