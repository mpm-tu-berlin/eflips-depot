# -*- coding: utf-8 -*-
"""
Initialization and hosting of a depot simulation.

"""
import copy
from abc import ABC, abstractmethod
from collections import namedtuple

import simpy
from eflips.helperFunctions import load_json, save_json, set_by_path

import eflips


class DepotHost:
    """Host for one depot and its configurator in a simulation. Needs to be
    operated by a SimulationHost or a similar procedure.
    Attributes depot, configurator and evaluation are stable references.

    Parameters:
    simulation_host: [SimulationHost]

    Attributes:
    depot: related [Depot]
    configurator: [DepotConfigurator] of related depot
    evaluation: [DepotEvaluation] of related depot
    """

    def __init__(self, env, simulation_host):
        self.env = env
        self.simulation_host = simulation_host

        self.evaluation = eflips.depot.DepotEvaluation(self)
        self.configurator = eflips.depot.DepotConfigurator(self.env)
        self.depot = self.configurator.depot

    def load_and_complete_template(self, filename_template):
        """Load depot template, validate and complete it."""
        success, errormsg = self.configurator.load(filename_template)
        if not success:
            raise ValueError("Error while loading template: " + errormsg)

        success, errormsg = self.configurator.complete()
        if not success:
            raise ValueError(errormsg)


class SimulationHost:
    """Wrapper for one or more depots in one simulation run.
    Provides utilities to properly setup a simulation:
    - load eflips settings
    - Create simpy.env, a timetable and a vehicleGenerator
    - Create one or more DepotHost instances and related objects
    - Measure time
    - Run the simulation

    Makes it possible to start the simulation from an outside script.

    Parameters:
    to_simulate: [list] of Depotinput objects. Specifies the number of
        depots to simulate, their template and if a GUI should be shown. Based
        on this input, DepotHost instances are created.

        The parallel simulation of multiple depots has not been tested yet,
        therefore to_simulate must have only one entry.

    Attributes:
    tictoc: [eflips.helperFunctions.Tictoc] measures execution time
    env: [simpy.Environment]
    depot_hosts: [list] of DepotHost instances
    timetable: [eflips.depot.standalone.Timetable]
    vg: [eflips.depot.standalone.VehicleGenerator]
    """

    def __init__(
        self, to_simulate, run_progressbar=False, print_timestamps=True, tictocname=""
    ):
        self.to_simulate = to_simulate

        self.tictoc = eflips.helperFunctions.Tictoc(print_timestamps, tictocname)
        self.tictoc.tic()
        self.run_progressbar = run_progressbar

        self.env = simpy.Environment()

        self.vg = eflips.depot.VehicleGenerator(self.env)

        self.gc = eflips.globalConstants

        self.filename_timetable = None
        self.timetable = None

        # Instantiate depot host(s) with empty depots
        self.depot_hosts = [DepotHost(self.env, self) for _ in to_simulate]
        self.depots = [dh.depot for dh in self.depot_hosts]

    @property
    def filename_eflips_settings(self):
        return self.gc["FILENAME_SETTINGS"] if "FILENAME_SETTINGS" in self.gc else None

    def standard_setup(self, filename_eflips_settings, filename_timetable):
        """Standard simulation setup without gui."""
        self.load_eflips_settings(filename_eflips_settings)
        self.load_timetable(filename_timetable)
        for dh, di in zip(self.depot_hosts, self.to_simulate):
            dh.load_and_complete_template(di.filename_template)
        self.complete()

    @staticmethod
    def load_eflips_settings(filename):
        """Load eflips.globalConstants. Validate and complete it (required for
        the depot simulation).
        """
        eflips.load_settings(filename)
        eflips.depot.settings_config.check_gc_validity()
        eflips.depot.settings_config.complete_gc()

    def load_timetable(self, filename):
        """Load timetable data from excel and init a timetable.
        load_eflips_settings() must be called before this.
        """
        timetabledata = eflips.depot.standalone.timetabledata_from_excel(filename)
        self.init_timetable(timetabledata)

    def init_timetable(self, timetabledata):
        """Use timetabledata to init a timetable."""
        self.timetable = eflips.depot.standalone.timetable_from_timetabledata(
            self.env, timetabledata
        )
        self.filename_timetable = timetabledata.filename.replace(".xlsx", "")

    def complete(self):
        """Complete the depot configuration phase. Must be called after
        loading a depot template/ configurating it and before simulation start.
        """
        for depot in self.depots:
            depot.timetable = self.timetable

        for depot_host in self.depot_hosts:
            depot_host.evaluation.complete()

    def run(self):
        """Run the simulation. All depot configurations have to be complete."""
        self.vg.run(self.depots)
        self.env.process(self.timetable.run(self.depots))

        if self.run_progressbar:
            self.env.process(
                eflips.helperFunctions.progressbar(
                    self.env,
                    eflips.settings.globalConstants["general"]["SIMULATION_TIME"],
                    step=10,
                    step_unit="%",
                )
            )

        self.tictoc.toc("list")  # mark the end of the configuration phase

        # Run env
        self.env.run(
            until=eflips.settings.globalConstants["general"]["SIMULATION_TIME"]
        )

        self.tictoc.toc("list")  # mark the end of the simulation phase
        if self.tictoc.print_timestamps:
            self.tictoc.print_toclist("interval")
            self.tictoc.print_toclist("cumulative")


Depotinput = namedtuple("Depotinput", ["filename_template", "show_gui"])
"""Container for parameters for SimulationHost"""


def create_alternatives(basefilename, keypath, values):
    """Create one or more json depot templates that vary by one attribute.
    Parameters:
    basefilename: [str] including path, excluding extension
    keypath: [list] of access keys
    values: [list] of value alternatives
    """
    basedata = load_json(basefilename)
    for i, value in enumerate(values):
        data = copy.deepcopy(basedata)
        set_by_path(data, keypath, value)
        save_json(data, basefilename + "_" + str(i))
        print("Saved as %s" % basefilename + "_" + str(i))


class BaseMultipleSimulationHost(ABC):
    """Base utilities for consecutively running multiple depot simulations.

    See usage demo in run_multiple_test.py

    history: [list] of tuples containing the filenames of the inputs of each
    simulation run.
    """

    def __init__(
        self,
        basefilename_eflips_settings,
        basefilename_timetable,
        basefilename_template,
        print_timestamps_total=True,
        tictocname_total="",
        print_timestamps_each=True,
        tictocname_each="",
    ):
        self.tictoc = eflips.Tictoc(print_timestamps_total, tictocname_total)
        self.tictoc.tic()
        self.print_timestamps_each = print_timestamps_each
        self.tictocname_each = tictocname_each

        self.basefilename_eflips_settings = basefilename_eflips_settings
        self.basefilename_timetable = basefilename_timetable
        self.basefilename_template = basefilename_template
        self.history = []

    def run(self):
        """Call to execute."""
        fn_es = self.basefilename_eflips_settings
        fn_ti = self.basefilename_timetable
        fn_te = self.basefilename_template
        done = False

        while not done:
            print()
            print()
            eflips.settings.reset_settings()

            simulation_host = self.simulate(fn_es, fn_ti, fn_te)

            self.evaluate(simulation_host.depot_hosts[0])

            done, fn_es, fn_ti, fn_te = self.next(
                simulation_host.depot_hosts[0], fn_es, fn_ti, fn_te
            )

        self.tictoc.toc("list")
        if self.tictoc.print_timestamps:
            self.tictoc.print_toclist("cumulative")

    def simulate(self, fn_es, fn_ti, fn_te):
        """Execute one simulation and return its SimulationHost instance."""
        simulation_host = eflips.depot.SimulationHost(
            [eflips.depot.Depotinput(filename_template=fn_te, show_gui=False)],
            run_progressbar=False,
            print_timestamps=self.print_timestamps_each,
            tictocname=self.tictocname_each,
        )
        simulation_host.standard_setup(fn_es, fn_ti)
        print(
            "Simulating template %s."
            % simulation_host.depot_hosts[0].configurator.templatename
        )
        simulation_host.run()
        print("Completed simulation.")
        self.history.append((fn_es, fn_ti, fn_te))
        return simulation_host

    def evaluate(self, depotsim):
        """Do evaluation after a simulation run."""

    @abstractmethod
    def stop_criterion(self, depotsim):
        """Return True if simulating should be stopped, else False."""
        return True

    @abstractmethod
    def next(self, depotsim, fn_es, fn_ti, fn_te):
        """
        Call self.stop_criterion to decide on further actions. For alternating,
        create a new scenario to simulate based on the results of the previous
        run.
        Return a tuple of
            - return value of self.stop_criterion,
            --- fn_es, fn_ti, fn_te that may each point to another file than
                passed as parameter, possibly newly created.
        """
        # Decide if continue or stop
        if not self.stop_criterion(depotsim):
            # Stop
            return True, None, None, None
        else:
            # Alternate
            # ...
            return False, fn_es, fn_ti, fn_te


class SimulationStop(simpy.Event):
    """Event for immediately terminating the simulation."""

    def __init__(self, env):
        super(SimulationStop, self).__init__(env)
        self.callbacks.append(simpy.core.StopSimulation.callback)

    def succeed(self, value=None):
        """Same as simpy.Event.succeed() but with URGENT priority.
        Use value to e.g. supply a reason.
        """
        if self._value is not simpy.events.PENDING:
            raise RuntimeError("%s has already been triggered" % self)

        self._ok = True
        self._value = value
        self.env.termination_reason = self
        self.env.schedule(self, simpy.events.URGENT)
        return self
