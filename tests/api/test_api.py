import os
import random
from datetime import datetime
from typing import List

import pandas as pd

import eflips

from depot import SimulationHost

import pytest

from depot.api import (
    VehicleSchedule,
    VehicleType,
    _validate_input_data,
    init_simulation,
    run_simulation,
)


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

    @pytest.fixture
    def vehicle_schedules(self) -> List[VehicleSchedule]:
        """
        This method creates a believable set of VehicleSchedule objects for testing purposes. It loads the sample_input
        file and creates VehicleSchedule objects from the data in the file.

        :return: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
        """
        absolute_path = os.path.dirname(__file__)
        path_to_sample_input = os.path.join(absolute_path, "sample_input.csv")

        df = pd.read_csv(path_to_sample_input)
        vehicle_schedules = []
        for _, row in df.iterrows():
            # we need to turn the arrival_soc and minimal_soc into dictionaries
            row["arrival_soc"] = {row["vehicle_class"]: row["arrival_soc"]}
            row["minimal_soc"] = {row["vehicle_class"]: row["minimal_soc"]}
            row["departure"] = datetime.fromisoformat(row["departure"])
            row["arrival"] = datetime.fromisoformat(row["arrival"])
            vehicle_schedules.append(VehicleSchedule(**row))

        # Limit the number of trips in order to simplify testing
        state = random.getstate()
        random.seed(42)
        vehicle_schedules = random.sample(vehicle_schedules, 1000)
        random.setstate(state)

        return vehicle_schedules

    @pytest.fixture
    def vehicle_types(self) -> List[VehicleType]:
        """
        This method creates a believable set of VehicleType objects for testing purposes. It loads the sample_input
        file and creates a VehicleType for each vehicle class in the file.

        :return: A list of :class:`eflips.depot.api.input.VehicleType`
        """
        absolute_path = os.path.dirname(__file__)
        path_to_sample_input = os.path.join(absolute_path, "sample_input.csv")

        df = pd.read_csv(path_to_sample_input)

        state = random.getstate()
        random.seed(42)

        vehicle_classes = set(df["vehicle_class"])
        vehicle_types = []
        for vehicle_class in vehicle_classes:
            vehicle_type = VehicleType(
                vehicle_class,
                vehicle_class,
                random.randrange(100, 300, 50),
                random.randrange(10, 40, 10),
                None,
            )
            vehicle_types.append(vehicle_type)

        random.setstate(state)
        return vehicle_types

    def test_validate_input_data(self, vehicle_types, vehicle_schedules):
        """
        Test the _validate_input_data method. This method should raise an AssertionError if there is a vehicle class
        in the vehicle schedule that does not have a corresponding vehicle type.

        :param vehicle_types: A list of :class:`eflips.depot.api.input.VehicleType`
        :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule`
        :return: Nothing
        """
        _validate_input_data(vehicle_types, vehicle_schedules)

        # Invalidate the vehicle types by removing one of them
        vehicle_types.pop()
        with pytest.raises(AssertionError):
            _validate_input_data(vehicle_types, vehicle_schedules)

    def test_init_simulation(self, vehicle_types, vehicle_schedules):
        """
        Test the init_simulation() API endpoint.

        :param vehicle_types: THe vehicle types from the fixture
        :param vehicle_schedules: The vehicle schedules from the fixture
        :return: Nothing
        """
        simulation_host = init_simulation(vehicle_types, vehicle_schedules)

    def test_run_simulation(self, vehicle_types, vehicle_schedules, tmp_path):
        simulation_host = init_simulation(vehicle_types, vehicle_schedules)
        depot_evaluation = run_simulation(simulation_host)

        vehicle_counts = depot_evaluation.nvehicles_used_calculation()

        # Now run the simulation again, with the knowledge of the vehicle counts
        simulation_host = init_simulation(
            vehicle_types, vehicle_schedules, vehicle_counts
        )
        depot_evaluation = run_simulation(simulation_host)

        depot_evaluation.path_results = str(tmp_path)

        depot_evaluation.vehicle_periods(
            periods={
                "depot general": "darkgray",
                "park": "lightgray",
                "serve_supply_clean_daily": "steelblue",
                "serve_clean_ext": "darkblue",
                "charge_dc": "forestgreen",
                "charge_oc": "forestgreen",
                "precondition": "black",
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
