import os
from datetime import datetime

import pytest
import pytz

import eflips
from eflips.depot import Timetable
from eflips.depot.api import VehicleType, VehicleSchedule
from eflips.depot.simple_vehicle import VehicleType as EflipsVehicleType


class TestVehicleType:
    @pytest.fixture
    def sample_vehicle_type(self):
        # Sample values for initialization
        id = "SB_DC"
        vehicle_class = "SB"
        battery_capacity_total = 100
        charging_curve = 15.0
        v2g_curve = None
        soc_max = 0.8
        soc_min = 0.2
        soh = 1.0

        return VehicleType(
            id,
            vehicle_class,
            battery_capacity_total,
            charging_curve,
            v2g_curve,
            soc_max,
            soc_min,
            soh,
        )

    def test_vehicle_type_init(self):
        """This method tests if a VehicleType object can be correctly successfully initialized and the curves
        are properly functioning"""

        # Sample values for initialization
        id = "SB_DC"
        vehicle_class = "SB"
        battery_capacity_total = 100
        charging_curve = 15.0
        v2g_curve = None
        soc_max = 0.8
        soc_min = 0.2
        soh = 1.0

        # Test if a sample vehicle type can be initialized
        vehicle_type = VehicleType(
            id,
            vehicle_class,
            battery_capacity_total,
            charging_curve,
            v2g_curve,
            soc_max,
            soc_min,
            soh,
        )

        # Test invalid soc_min and soc_max values
        with pytest.raises(AssertionError):
            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                charging_curve,
                v2g_curve,
                1.1,
                soc_min,
                soh,
            )
        with pytest.raises(AssertionError):
            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                charging_curve,
                v2g_curve,
                -0.1,
                soc_min,
                soh,
            )
        with pytest.raises(AssertionError):
            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                charging_curve,
                v2g_curve,
                soc_max,
                1.1,
                soh,
            )
        with pytest.raises(AssertionError):
            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                charging_curve,
                v2g_curve,
                soc_max,
                -0.1,
                soh,
            )
        # SocMin must be smaller than SocMax
        with pytest.raises(AssertionError):
            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                charging_curve,
                v2g_curve,
                0.8,
                0.9,
                soh,
            )

        # Test invalid SoH values
        with pytest.raises(AssertionError):
            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                charging_curve,
                v2g_curve,
                soc_max,
                soc_min,
                1.1,
            )
        with pytest.raises(AssertionError):
            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                charging_curve,
                v2g_curve,
                soc_max,
                soc_min,
                -0.1,
            )

        # Test different konds of inputs for charging curve and v2g curve
        possible_curves = [
            15.0,
            ([0, 0.8, 1], [0, 15, 0]),
            {0.0: 0.0, 0.8: 15.0, 1.0: 0.0},
        ]

        for curve in possible_curves:
            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                curve,
                v2g_curve,
                soc_max,
                soc_min,
                soh,
            )
            assert vehicle_type.charging_curve(0.8) == 15.0

            vehicle_type = VehicleType(
                id,
                vehicle_class,
                battery_capacity_total,
                charging_curve,
                curve,
                soc_max,
                soc_min,
                soh,
            )
            assert vehicle_type.v2g_curve(0.8) == 15.0

    def test_net_capacity(self, sample_vehicle_type):
        """This method tests if the net capacity is correctly calculated"""

        assert pytest.approx(sample_vehicle_type.net_battery_capacity) == 60.0

    def test_to_eflips_vehicle_type(self, sample_vehicle_type):
        eflips_vehicle_type = sample_vehicle_type._to_eflips_vehicle_type()
        assert isinstance(eflips_vehicle_type, EflipsVehicleType)

        assert eflips_vehicle_type.ID == sample_vehicle_type.id
        assert (
            eflips_vehicle_type.battery_capacity
            == sample_vehicle_type.battery_capacity_total
        )
        assert eflips_vehicle_type.soc_min == sample_vehicle_type.soc_min
        assert eflips_vehicle_type.soc_max == sample_vehicle_type.soc_max
        assert eflips_vehicle_type.soh == sample_vehicle_type.soh


class TestVehicleSchedule:
    @pytest.fixture
    def sample_vehicle_schedule(self):
        tz = pytz.UTC
        # Sample values for initialization
        id = "Honig"
        vehicle_class = "Pustekuchen"
        departure = datetime(2020, 1, 1, 0, 0, 0, tzinfo=tz)
        arrival = datetime(2020, 1, 1, 1, 0, 0, tzinfo=tz)
        departure_soc = 0.9
        arrival_soc = {
            "ebus": 0.5,
        }
        minimum_soc = {"ebus": 0.2}
        opportunity_charging = False

        return VehicleSchedule(
            id,
            vehicle_class,
            departure,
            arrival,
            departure_soc,
            arrival_soc,
            minimum_soc,
            opportunity_charging,
        )

    def test_conversion_to_eflips(self, sample_vehicle_schedule):
        # We need an environment to convert the vehicle schedule
        absolute_path = os.path.dirname(__file__)
        filename_eflips_settings = os.path.join(
            absolute_path, "..", "sample_simulation", "settings"
        )
        filename_schedule = os.path.join(
            absolute_path, "..", "sample_simulation", "schedule"
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
        env = simulation_host.env

        eflips_vehicle_schedule = sample_vehicle_schedule._to_timetable(
            [sample_vehicle_schedule],
            env,
            start_of_simulation=datetime(2020, 1, 1, 0, 0, 0, tzinfo=pytz.UTC),
        )
        assert isinstance(eflips_vehicle_schedule, Timetable)
