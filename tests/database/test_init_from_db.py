import pytest
import os
import random
import uuid
from datetime import datetime, timedelta
from typing import List


import eflips
from eflips.depot.api.enums import AreaType
from eflips.depot.api.input import Area, Process, Plan
from eflips.depot.api.input import ApiVehicleType
from eflips.depot import SimulationHost
from eflips.depot.api import (
    VehicleSchedule,
    Depot,
    _validate_input_data,
    init_simulation,
    run_simulation,
)


from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from eflips.model import Base
from eflips.model.general import (
    Scenario,
    VehicleType,
    BatteryType,
    VehicleClass,
    Vehicle,
)


class TestGeneral:
    @pytest.fixture()
    def scenario(self, session):
        """
        Creates a scenario
        :param session: An SQLAlchemy Session with the eflips-db schema
        :return: A :class:`Scenario` object
        """
        scenario = Scenario(name="Test Scenario")
        session.add(scenario)
        session.commit()
        return scenario

    @pytest.fixture(autouse=True)
    def sample_content(self, session):
        """
        Creates a scenario that comes filled with sample content for each type
        :param session: An SQLAlchemy Session with the eflips-db schema
        :return: A :class:`Scenario` object
        """

        # Add a scenario
        scenario = Scenario(name="Test Scenario")
        session.add(scenario)

        # Create a 12 meter bus
        vehicle_type_12m = VehicleType(
            scenario=scenario,
            name="12",
            battery_capacity=300,
            charging_curve=[[0, 1], [150, 150]],
            opportunity_charging_capable=True,
        )
        session.add(vehicle_type_12m)
        battery_type = BatteryType(
            scenario=scenario, specific_mass_kg_per_kwh=100, chemistry={"test": "test"}
        )
        session.add(battery_type)
        vehicle_type_12m.battery_type = battery_type

        # Add a vehicle type without a battery type
        # vehicle_type_18m = VehicleType(
        #     scenario=scenario,
        #     name="18",
        #     battery_capacity=120,
        #     charging_curve=[[0, 1], [150, 150]],
        #     opportunity_charging_capable=True,
        # )
        #
        # session.add(vehicle_type_18m)

        vehicle_type_12m_terminus_charge = VehicleType(
            scenario=scenario,
            name="12_terminus_charge",
            battery_capacity=120,
            charging_curve=[[0, 1], [150, 150]],
            opportunity_charging_capable=True,
        )
        # Add a VehicleClass
        vehicle_class = VehicleClass(
            scenario=scenario,
            name="Test Vehicle Class",
            vehicle_types=[
                vehicle_type_12m,
                # vehicle_type_18m,
                vehicle_type_12m_terminus_charge,
            ],
        )
        session.add(vehicle_class)

        # Add a vehicle
        vehicle = Vehicle(
            scenario=scenario,
            vehicle_type=vehicle_type_12m,
            name="Test Vehicle",
            name_short="TV",
        )
        session.add(vehicle)

        session.commit()
        return scenario

    @pytest.fixture()
    def session(self):
        """
        Creates a session with the eflips-db schema
        NOTE: THIS DELETE ALL DATA IN THE DATABASE
        :return: an SQLAlchemy Session with the eflips-db schema
        """
        url = os.environ["DATABASE_URL"]
        engine = create_engine(
            url, echo=False
        )  # Change echo to True to see SQL queries
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        session = Session(bind=engine)
        yield session
        session.close()

    @pytest.fixture()
    def vehicle_types(self, session):
        list_of_vehicle_types = []
        stmt = select(VehicleType)
        for db_vt in session.execute(stmt).scalars():
            api_vt = ApiVehicleType(db_vt)
            list_of_vehicle_types.append(api_vt)

        return list_of_vehicle_types

    def test_vehicle_type_validity(self, vehicle_types):
        assert len(vehicle_types) == 3
        for db_vt in vehicle_types:
            assert isinstance(db_vt, ApiVehicleType)

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
            capacity=50,
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
            capacity=20,
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
                        vehicle_classes=vehicle_type.vehicle_classes,
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

        return schedules

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
