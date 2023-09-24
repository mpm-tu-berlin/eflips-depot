import os
import random
from typing import List

import pandas as pd

import eflips

from depot import SimulationHost

import pytest

from depot.api import VehicleSchedule, VehicleType, _validate_input_data


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
            vehicle_schedules.append(VehicleSchedule(**row))
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

        vehicle_classes = set(df["vehicle_class"])
        vehicle_types = []
        for vehicle_class in vehicle_classes:
            vehicle_type = VehicleType(
                vehicle_class,
                vehicle_class,
                random.randrange(100, 300, 50),
                random.randrange(90, 240, 30),
                None,
            )
            vehicle_types.append(vehicle_type)
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
