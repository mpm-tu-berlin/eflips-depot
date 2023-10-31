import os
import random
import uuid
from datetime import datetime, timedelta
from typing import List

import pandas as pd

import eflips

from eflips.depot import SimulationHost

import pytest

from eflips.depot.api import (
    VehicleSchedule,
    VehicleType,
    Depot,
    _validate_input_data,
    init_simulation,
    run_simulation,
)

from depot.api.input import Area, AreaType, Process, Plan


class TestApi:
    @pytest.fixture
    def depot(self):
        # A depot with a representative set of areas
        # Create an arrival cleaning process
        arrival_cleaning = Process(
            id=1,
            name="Arrival Cleaning",
            dispatchable=False,
            areas=[],  # Connect the areas later
            duration=4800,
            electric_power=None,
        )

        arrival_area = Area(
            id=1,
            name="Arrival Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=None,  # we connect the depot later
            available_processes=[arrival_cleaning],
            vehicle_classes=None,
            capacity=6,
        )

        # Connect the areas and processes
        arrival_cleaning.areas = [arrival_area]

        # Create a charging process
        charging = Process(
            id=2,
            name="Charging",
            dispatchable=False,
            areas=[],  # Connect the areas later
            duration=None,
            electric_power=20.0,
        )

        # And a pre-conditioning process
        preconditioning = Process(
            id=3,
            # TODO fix the name problem of preconditioning
            name="Pre-conditioning",
            # name="precondition",
            dispatchable=False,
            areas=[],  # Connect the areas later
            duration=30 * 60,
            electric_power=20.0,
        )

        # And a standby pre-departure process
        standby_pre_departure = Process(
            id=4,
            name="Standby Pre-departure",
            dispatchable=True,
            areas=[],  # Connect the areas later
            duration=None,
            electric_power=None,
        )

        # Create a line charging area
        line_charging_area = Area(
            id=2,
            name="Line Charging Area",
            type=AreaType.LINE,
            depot=None,  # we connect the depot later
            available_processes=[charging, preconditioning, standby_pre_departure],
            vehicle_classes=None,
            capacity=24,
            row_count=4,
        )

        # Create a direct charging area
        direct_charging_area = Area(
            id=3,
            name="Direct Charging Area",
            type=AreaType.DIRECT_TWOSIDE,
            depot=None,  # we connect the depot later
            available_processes=[charging, preconditioning, standby_pre_departure],
            vehicle_classes=None,
            capacity=6,
        )

        # Create another area that just does standby pre-departure
        standby_pre_departure_area = Area(
            id=4,
            name="Standby Pre-departure Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=None,  # we connect the depot later
            available_processes=[standby_pre_departure],
            vehicle_classes=None,
            capacity=6,
        )

        # Connect the areas and processes
        charging.areas = [line_charging_area, direct_charging_area]
        preconditioning.areas = [line_charging_area, direct_charging_area]
        standby_pre_departure.areas = [
            line_charging_area,
            direct_charging_area,
            standby_pre_departure_area,
        ]

        # Create a plan
        plan = Plan(
            id=1,
            processes=[
                arrival_cleaning,
                charging,
                preconditioning,
                standby_pre_departure,
            ],
        )

        # Create a depot
        depot = Depot(
            id=1,
            name="Test Depot",
            areas=[
                arrival_area,
                line_charging_area,
                direct_charging_area,
                standby_pre_departure_area,
            ],
            plan=plan,
        )

        # Connect the areas and depot
        arrival_area.depot = depot
        line_charging_area.depot = depot
        direct_charging_area.depot = depot
        standby_pre_departure_area.depot = depot

        return depot

    @pytest.fixture
    def simulation_host(self, depot) -> SimulationHost:
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
        # path_to_sample_input = os.path.join(absolute_path, "sample_input.csv")

        # TODO use a small capacity of schedules and will be fixed later
        path_to_sample_input = os.path.join(
            absolute_path, "sample_input_small_capacity.csv"
        )

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
        # vehicle_schedules = random.sample(vehicle_schedules, 1000)

        # TODO use a small capacity of schedules and will be fixed later
        vehicle_schedules = random.sample(vehicle_schedules, 5)

        # Instead, I'll do it manually
        # Let's have a bus depart every four hours for an eight hour period
        vehicle_schedules = []
        for i in range(0, 24, 4):
            departure = datetime(2020, 1, 1, i)
            arrival = departure + timedelta(hours=8)
            vehicle_schedules.append(
                VehicleSchedule(
                    vehicle_class="articulated",
                    departure=departure,
                    arrival=arrival,
                    arrival_soc={"articulated": 0.8},
                    departure_soc=1.0,
                    minimal_soc={"articulated": 0.2},
                    opportunity_charging=False,
                    id=str(i),
                )
            )

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
        # path_to_sample_input = os.path.join(absolute_path, "sample_input.csv")

        # TODO use a small capacity of schedules and will be fixed later
        path_to_sample_input = os.path.join(
            absolute_path, "sample_input_small_capacity.csv"
        )

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

        # Also add a vehicle type for the articulated buses
        vehicle_type = VehicleType("articulated", "articulated", 600, 90)
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

    def test_init_simulation(self, vehicle_types, vehicle_schedules, depot):
        """
        Test the init_simulation() API endpoint.

        :param vehicle_types: THe vehicle types from the fixture
        :param vehicle_schedules: The vehicle schedules from the fixture
        :return: Nothing
        """
        simulation_host = init_simulation(vehicle_types, vehicle_schedules, None, depot)

    def test_run_simulation(self, vehicle_types, vehicle_schedules, depot, tmp_path):
        simulation_host = init_simulation(vehicle_types, vehicle_schedules, None, depot)
        depot_evaluation = run_simulation(simulation_host)

        vehicle_counts = depot_evaluation.nvehicles_used_calculation()

        # Now run the simulation again, with the knowledge of the vehicle counts
        simulation_host = init_simulation(
            vehicle_types, vehicle_schedules, vehicle_counts, depot
        )
        depot_evaluation = run_simulation(simulation_host)

        depot_evaluation.path_results = str(tmp_path)

        depot_evaluation.vehicle_periods(
            # periods={
            #     "depot general": "darkgray",
            #     "park": "lightgray",
            #     "serve_supply_clean_daily": "steelblue",
            #     "serve_clean_ext": "darkblue",
            #     "charge_dc": "forestgreen",
            #     "charge_oc": "forestgreen",
            #     "precondition": "black",
            # },
            # TODO re-write process names to plot
            periods={
                "depot general": "darkgray",
                "park": "lightgray",
                "Arrival Cleaning": "steelblue",
                "Charging": "darkblue",
                "Standby Pre-departure": "forestgreen",
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
