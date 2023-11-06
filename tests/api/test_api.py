import os
import random
import uuid
from datetime import datetime, timedelta, time
from typing import List

import pytest

import eflips
from depot.api.enums import AreaType
from depot.api.input import Area, Process, Plan
from eflips.depot import SimulationHost
from eflips.depot.api import (
    VehicleSchedule,
    VehicleType,
    Depot,
    _validate_input_data,
    init_simulation,
    run_simulation,
)


class TestApi:
    @pytest.fixture
    def depot(self):
        # A depot with a representative set of areas
        # Create an arrival cleaning process
        # Generate random time stamps for availability for testing purposes

        random.seed()
        random.getstate()
        time_stamps = []
        for i in range(4):
            time_stamps.append(
                (
                    time(
                        hour=random.randint(0, 23),
                        minute=random.randint(0, 59),
                        second=random.randint(0, 59),
                    )
                )
            )

        # first try 4 stamps and then add the numbers

        # At first sorting the test list then add more possibilities

        time_stamps.sort()

        list_of_availability = [
            (time_stamps[0], time_stamps[1]),
            (time_stamps[2], time_stamps[3]),
        ]

        print(list_of_availability)

        arrival_cleaning = Process(
            id=1,
            name="Arrival Cleaning",
            dispatchable=False,
            areas=[],  # Connect the areas later
            duration=4800,
            electric_power=None,
            availability=list_of_availability,
        )

        arrival_area = Area(
            id=1,
            name="Arrival Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=None,  # we connect the depot later
            available_processes=[arrival_cleaning],
            vehicle_classes=None,
            capacity=500,
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
            electric_power=150.0,
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
            capacity=30,
            row_count=6,
        )

        # Create a direct charging area
        direct_charging_area = Area(
            id=3,
            name="Direct Charging Area",
            type=AreaType.DIRECT_TWOSIDE,
            depot=None,  # we connect the depot later
            available_processes=[charging, preconditioning, standby_pre_departure],
            vehicle_classes=None,
            capacity=100,
        )

        # Create another area that just does standby pre-departure
        standby_pre_departure_area = Area(
            id=4,
            name="Standby Pre-departure Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=None,  # we connect the depot later
            available_processes=[standby_pre_departure, preconditioning],
            vehicle_classes=None,
            capacity=10,
        )

        # Connect the areas and processes
        charging.areas = [line_charging_area, direct_charging_area]
        preconditioning.areas = [
            line_charging_area,
            direct_charging_area,
            standby_pre_departure_area,
        ]
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
    def vehicle_schedules(
        self, vehicle_types, schedules_per_day=50, days=1
    ) -> List[VehicleSchedule]:
        """
        This method creates a believable set of VehicleSchedule objects for testing purposes. It creates a number of
        "bus lines" with a randomly chosen interval (within 3 to 20 minutes) and a randomly chosen duration of the
        total vehicle schedule (5 to 25 hours). For each day, a random set of schedules is created.

        :param vehicle_types: A list of :class:`eflips.depot.api.input.VehicleType` objects. Schedule objects will be
        randomly assigned to one of these vehicle types.

        :param schedules_per_day: The number of schedules to create per day.

        :param days: The number of days to create schedules for.

        :return: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
        """

        start_date = datetime(2023, 1, 1, 0, 0, 0)
        schedules = []

        # Limit the number of trips in order to simplify testing
        state = random.getstate()
        random.seed(42)

        for day in range(days):
            schedules_created = 0
            while schedules_created < schedules_per_day:
                # Create a new bus line
                # Give it an interval between 3 and 20 minutes, a duration between 5 and 25 hours and a first time
                # between 5:00 and 8:00
                interval = timedelta(minutes=random.randint(3, 60))
                duration = timedelta(hours=random.randint(6, 30))
                first_time = start_date + timedelta(
                    minutes=random.randint(5 * 60, 8 * 60)
                )

                number_of_schedules = random.randint(5, 20)
                vehicle_type = random.choice(vehicle_types)

                departure_time = first_time
                for schedule_number in range(number_of_schedules):
                    if schedules_created >= schedules_per_day:
                        break

                    # Create a new schedule
                    soc = {vehicle_type.id: random.uniform(0.1, 0.2)}
                    schedule = VehicleSchedule(
                        id=str(
                            uuid.uuid5(uuid.NAMESPACE_DNS, str(random.randbytes(64)))
                        ),  # Repeatable randomness
                        vehicle_class=vehicle_type.vehicle_class,
                        departure=departure_time,
                        arrival=departure_time + duration,
                        departure_soc=1.0,
                        arrival_soc=soc,
                        minimal_soc=soc,
                        opportunity_charging=False,
                    )
                    schedules.append(schedule)
                    departure_time += interval
                    schedules_created += 1

            start_date += timedelta(days=1)

        random.setstate(state)
        VehicleSchedule.visualize(schedules)

        return schedules

    @pytest.fixture
    def vehicle_types(self) -> List[VehicleType]:
        """
        This method creates a believable set of VehicleType objects for testing purposes.

        :return: A list of :class:`eflips.depot.api.input.VehicleType`
        """

        # Create a 12 meter bus
        vehicle_type_12m = VehicleType(
            id="12",
            vehicle_class="12m",
            battery_capacity_total=300,
            charging_curve=150,
            v2g_curve=None,
        )

        # Create a 18 meter bus
        vehicle_type_18m = VehicleType(
            id="18",
            vehicle_class="18m",
            battery_capacity_total=120,
            charging_curve=450,
            v2g_curve=None,
        )

        # Create another 12 meter bus
        vehicle_type_12m_terminus_charge = VehicleType(
            id="121",
            vehicle_class="12m terminus_charge",
            battery_capacity_total=120,
            charging_curve=450,
            v2g_curve=None,
        )

        return [vehicle_type_12m, vehicle_type_18m, vehicle_type_12m_terminus_charge]

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
