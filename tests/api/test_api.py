import os

import eflips

from depot import SimulationHost

import pytest


class TestApi:
    @pytest.fixture
    def simulation_host(self) -> SimulationHost:
        """
        This method provides a SimulationHost object for testing purposes.
        :return: A :class:`eflips.depot.simulation.SimulationHost` object.
        """
        absolute_path = os.path.dirname(__file__)

        filename_template = os.path.join(
            absolute_path, "sample_simulation", "sample_depot"
        )

        simulation_host = eflips.depot.SimulationHost(
            [
                eflips.depot.Depotinput(
                    filename_template=filename_template, show_gui=False
                )
            ],
            run_progressbar=True,
            print_timestamps=True,
            tictocname="",
        )

        assert isinstance(simulation_host, SimulationHost)
        return simulation_host
