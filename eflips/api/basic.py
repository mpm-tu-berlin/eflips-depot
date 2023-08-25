# -*- coding: utf-8 -*-
"""A temporary file for eFLIPS-Depot API
"""

from dataclasses import dataclass
from eflips.depot.simulation import SimulationHost, Depotinput



def init_simulation(fsettings, fschedule, ftemplate):
    """Initialization of :class:`eflips.depot.simulation.SimulationHost`

    :param fsettings: Path of setting file (JSON)
    :type fsettings: str
    :param fschedule: Path of bus-schedule file (XML)
    :type fschedule: str
    :param ftemplate: Path of template file (JSON)
    :type ftemplate: str

    :return: A :class:`eflips.depot.simulation.SimulationHost`
    :rtype: :class:`eflips.depot.simulation.SimulationHost`
    """
    simulation_host = SimulationHost(
        [
            Depotinput(
                filename_template=ftemplate,
                show_gui=False)
        ],
        run_progressbar=True,
        print_timestamps=True,
        tictocname=''
    )
    simulation_host.standard_setup(fsettings,
                                   fschedule)

    return simulation_host



def run_simulation(simulation_host):
    """Run simulation and return simulation results

    :param simulation_host: Simulation Host of eflips-depot
    :type simulation_host: :class:`eflips.depot.Simulation.SimulationHost`
    :return: Object of :class:`eflips.depot.Simulation.SimulationHost` storing simulation results
    :rtype: :class:`eflips.depot.Simulation.SimulationHost`
    """
    simulation_host.run()
    ev = simulation_host.depot_hosts[0].evaluation

    return ev



def to_simba(ev):
    """Returns a list containing input data for simba

    :param ev: Object storing all simulation results
    :type ev: :class:`eflips.depot.evaluation.DepotEvaluation`
    :return: list of :class:`InputForSimba`
    :rtype: list"""
    inputs_for_simba = []
    for trip_i in ev.timetable.trips_issued:
        if '_r1' in trip_i.ID:  # _r1: repetition 1 of all rotations
            data_unit = InputForSimba(int(float(trip_i.ID_orig)),  # Slightly ugly, but we need to return an int
                                      trip_i.vehicle.ID,
                                      trip_i.start_soc)
            inputs_for_simba.append(data_unit)

    return inputs_for_simba


@dataclass
class InputForSimba:
    """Input Data for Simba

    :param rotation_id: ID of rotation
    :type rotation_id: int
    :param vehicle_id: ID of vehicle
    :type vehicle_id: str
    :param soc_departure: soc at departure of each vehicle
    :type soc_departure: float
    """
    rotation_id: int
    vehicle_id: str
    soc_departure: float
