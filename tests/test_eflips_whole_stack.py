import os

import pytest

import eflips
import eflips.depot
from eflips.depot import DepotEvaluation


class TestDepotEvaluation:
    @pytest.fixture(autouse=True)
    def clear_settings(self):
        eflips.settings.reset_settings()

    @pytest.fixture
    def depot_evaluation(self):
        """This method creates a sample depot evaluation object containing some sample data. Since the depot evaluation
        is created at the end of the simulation, we need to create a simulation host object first.
        """

        absolute_path = os.path.dirname(__file__)
        filename_eflips_settings = os.path.join(
            absolute_path, "sample_simulation", "settings"
        )
        filename_schedule = os.path.join(absolute_path, "sample_simulation", "schedule")
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
        simulation_host.standard_setup(filename_eflips_settings, filename_schedule)
        simulation_host.run()

        ev = simulation_host.depot_hosts[0].evaluation

        return ev

    def test_plot_vehicle_periods(self, depot_evaluation: DepotEvaluation, tmp_path):
        # Override the path_results variable in the config's ['depot']['path_results'] with a temporary directory
        depot_evaluation.path_results = str(tmp_path)

        depot_evaluation.vehicle_periods(
            periods={
                "depot general": "darkgray",
                "park": "lightgray",
                "Arrival Cleaning": "steelblue",
                "Charging": "forestgreen",
                "Standby Pre-departure": "darkblue",
                "precondition": "black",
                "trip": "wheat",
            },
            save=True,
            show=False,
            formats=(
                "pdf",
                "png",
            ),
            show_total_power=True,
            show_annotates=True,
        )
        # Check if the files were created and are not empty
        assert os.path.isfile(os.path.join(tmp_path, "vehicle_periods.pdf"))
        assert os.stat(os.path.join(tmp_path, "vehicle_periods.pdf")).st_size > 0

        assert os.path.isfile(os.path.join(tmp_path, "vehicle_periods.png"))
        assert os.stat(os.path.join(tmp_path, "vehicle_periods.png")).st_size > 0
