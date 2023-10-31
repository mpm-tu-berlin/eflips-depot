import os

import pandas as pd

import eflips

from eflips.depot import DepotEvaluation
from eflips.depot.api.django_simba.output import to_simba, InputForSimba
from test_eflips_whole_stack import TestDepotEvaluation


class TestApiDjangoSImbaOutput(TestDepotEvaluation):
    def test_output_format(self, depot_evaluation):
        output_for_simba = to_simba(depot_evaluation)
        assert isinstance(output_for_simba, list)
        for item in output_for_simba:
            assert isinstance(item, InputForSimba)

    def test_output_differs_from_input(self, tmp_path):
        """
        In order to validate our bugfix fpr #53, we make sure that the soc_departure values in the output differ from
        the soc_departure values in the input.
        :return: nothing
        """

        # Set up a depot evaluation object
        absolute_path = os.path.dirname(__file__)
        filename_eflips_settings = os.path.join(
            absolute_path, "..", "sample_simulation", "settings"
        )
        filename_schedule = os.path.join(
            absolute_path, "..", "sample_simulation", "schedule"
        )

        # Copy and manipulate the schedule file
        schedule_file_extension = ".xlsx"
        filename_schedule_copy = os.path.join(tmp_path, "schedule")
        schedule = pd.read_excel(filename_schedule + schedule_file_extension)

        # Set the start_soc column for all rows to 0.42
        schedule["start_soc"] = 0.42
        schedule["end_soc"] = 0.1
        schedule.to_excel(
            filename_schedule_copy + schedule_file_extension,
            sheet_name="Tripdata",
            index=False,
        )

        filename_template = os.path.join(
            absolute_path, "..", "sample_simulation", "sample_depot"
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
        simulation_host.standard_setup(filename_eflips_settings, filename_schedule_copy)
        simulation_host.run()

        ev = simulation_host.depot_hosts[0].evaluation

        output_for_simba = to_simba(ev)
        assert isinstance(output_for_simba, list)
        for item in output_for_simba:
            assert isinstance(item, InputForSimba)
            assert item.soc_departure != 0.42
