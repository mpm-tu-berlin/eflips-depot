import os
from datetime import datetime, timezone, timedelta

import pytest
from eflips.model import (
    Scenario,
    VehicleType,
    BatteryType,
    VehicleClass,
    Vehicle,
    Line,
    Station,
    Route,
    AssocRouteStation,
    Rotation,
    Trip,
    TripType,
    StopTime,
    Depot,
    Plan,
    Area,
    AreaType,
    Process,
    AssocPlanProcess,
    Base,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from depot.api import init_simulation, run_simulation


class TestHelpers:
    @pytest.fixture()
    def scenario(self, session):
        """
        Creates a scenario
        :param session: An SQLAlchemy Session with the eflips-model schema
        :return: A :class:`Scenario` object
        """
        scenario = Scenario(name="Test Scenario")
        session.add(scenario)
        session.commit()
        return scenario

    @pytest.fixture()
    def full_scenario(self, session):
        """
        Creates a scenario that comes filled with sample content for each type
        :param session: An SQLAlchemy Session with the eflips-model schema
        :return: A :class:`Scenario` object
        """

        # Add a scenario
        scenario = Scenario(name="Test Scenario")
        session.add(scenario)

        # Add a vehicle type with a battery type
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            opportunity_charging_capable=True,
        )
        session.add(vehicle_type)
        battery_type = BatteryType(
            scenario=scenario, specific_mass=100, chemistry={"test": "test"}
        )
        session.add(battery_type)
        vehicle_type.battery_type = battery_type

        # Add a vehicle type without a battery type
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type 2",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            opportunity_charging_capable=True,
        )
        session.add(vehicle_type)

        # Add a VehicleClass
        vehicle_class = VehicleClass(
            scenario=scenario,
            name="Test Vehicle Class",
            vehicle_types=[vehicle_type],
        )
        session.add(vehicle_class)

        # Add a vehicle
        vehicle = Vehicle(
            scenario=scenario,
            vehicle_type=vehicle_type,
            name="Test Vehicle",
            name_short="TV",
        )
        session.add(vehicle)

        line = Line(
            scenario=scenario,
            name="Test Line",
            name_short="TL",
        )
        session.add(line)

        stop_1 = Station(
            scenario=scenario,
            name="Test Station 1",
            name_short="TS1",
            geom="POINT(0 0 0)",
            is_electrified=False,
        )
        session.add(stop_1)

        stop_2 = Station(
            scenario=scenario,
            name="Test Station 2",
            name_short="TS2",
            geom="POINT(1 0 0)",
            is_electrified=False,
        )
        session.add(stop_2)

        stop_3 = Station(
            scenario=scenario,
            name="Test Station 3",
            name_short="TS3",
            geom="POINT(2 0 0)",
            is_electrified=False,
        )

        route_1 = Route(
            scenario=scenario,
            name="Test Route 1",
            name_short="TR1",
            departure_station=stop_1,
            arrival_station=stop_3,
            line=line,
            distance=1000,
        )
        assocs = [
            AssocRouteStation(
                scenario=scenario, station=stop_1, route=route_1, elapsed_distance=0
            ),
            AssocRouteStation(
                scenario=scenario, station=stop_2, route=route_1, elapsed_distance=500
            ),
            AssocRouteStation(
                scenario=scenario, station=stop_3, route=route_1, elapsed_distance=1000
            ),
        ]
        route_1.assoc_route_stations = assocs
        session.add(route_1)

        route_2 = Route(
            scenario=scenario,
            name="Test Route 2",
            name_short="TR2",
            departure_station=stop_3,
            arrival_station=stop_1,
            line=line,
            distance=1000,
        )
        assocs = [
            AssocRouteStation(
                scenario=scenario, station=stop_3, route=route_2, elapsed_distance=0
            ),
            AssocRouteStation(
                scenario=scenario, station=stop_2, route=route_2, elapsed_distance=500
            ),
            AssocRouteStation(
                scenario=scenario, station=stop_1, route=route_2, elapsed_distance=1000
            ),
        ]
        route_2.assoc_route_stations = assocs
        session.add(route_2)

        # Add the schedule objects
        first_departure = datetime(
            year=2020, month=1, day=1, hour=12, minute=0, second=0, tzinfo=timezone.utc
        )
        interval = timedelta(minutes=30)
        duration = timedelta(minutes=20)
        trips = []

        rotation = Rotation(
            scenario=scenario,
            trips=trips,
            vehicle_type=vehicle_type,
            allow_opportunity_charging=False,
        )
        session.add(rotation)

        for i in range(15):
            # forward
            trips.append(
                Trip(
                    scenario=scenario,
                    route=route_1,
                    trip_type=TripType.PASSENGER,
                    departure_time=first_departure + 2 * i * interval,
                    arrival_time=first_departure + 2 * i * interval + duration,
                    rotation=rotation,
                )
            )
            stop_times = [
                StopTime(
                    scenario=scenario,
                    station=stop_1,
                    arrival_time=first_departure + 2 * i * interval,
                ),
                StopTime(
                    scenario=scenario,
                    station=stop_2,
                    arrival_time=first_departure
                    + 2 * i * interval
                    + timedelta(minutes=5),
                ),
                StopTime(
                    scenario=scenario,
                    station=stop_3,
                    arrival_time=first_departure + 2 * i * interval + duration,
                ),
            ]
            trips[-1].stop_times = stop_times

            # backward
            trips.append(
                Trip(
                    scenario=scenario,
                    route=route_2,
                    trip_type=TripType.PASSENGER,
                    departure_time=first_departure + (2 * i + 1) * interval,
                    arrival_time=first_departure + (2 * i + 1) * interval + duration,
                    rotation=rotation,
                )
            )
            stop_times = [
                StopTime(
                    scenario=scenario,
                    station=stop_3,
                    arrival_time=first_departure + (2 * i + 1) * interval,
                ),
                StopTime(
                    scenario=scenario,
                    station=stop_2,
                    arrival_time=first_departure
                    + (2 * i + 1) * interval
                    + timedelta(minutes=5),
                ),
                StopTime(
                    scenario=scenario,
                    station=stop_1,
                    arrival_time=first_departure + (2 * i + 1) * interval + duration,
                ),
            ]
            trips[-1].stop_times = stop_times
        session.add_all(trips)

        # Create a simple depot

        depot = Depot(scenario=scenario, name="Test Depot", name_short="TD")
        session.add(depot)

        # Create plan

        plan = Plan(scenario=scenario, name="Test Plan")
        session.add(plan)

        depot.default_plan = plan

        # Create areas
        cleaning_area = Area(
            scenario=scenario,
            name="Test Area",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=2,
        )
        session.add(cleaning_area)
        cleaning_area.vehicle_type = vehicle_type

        charging_area = Area(
            scenario=scenario,
            name="Test Area",
            depot=depot,
            area_type=AreaType.LINE,
            row_count=2,
            capacity=6,
        )
        session.add(charging_area)
        charging_area.vehicle_type = vehicle_type

        parking_area = Area(
            scenario=scenario,
            name="Test Area",
            depot=depot,
            area_type=AreaType.DIRECT_TWOSIDE,
            capacity=4,
        )
        session.add(parking_area)
        parking_area.vehicle_type = vehicle_type

        # Create processes
        standby_arrival = Process(
            name="Standby Arrival",
            scenario=scenario,
            dispatchable=False,
        )

        clean = Process(
            name="Clean",
            scenario=scenario,
            dispatchable=False,
            duration=timedelta(minutes=30),
        )

        charging = Process(
            name="Charging",
            scenario=scenario,
            dispatchable=False,
            electric_power=150,
        )

        standby_departure = Process(
            name="Standby Departure",
            scenario=scenario,
            dispatchable=True,
        )

        session.add(standby_arrival)
        session.add(clean)
        session.add(charging)
        session.add(standby_departure)

        cleaning_area.processes.append(clean)
        cleaning_area.processes.append(standby_arrival)
        charging_area.processes.append(charging)
        parking_area.processes.append(standby_departure)

        assocs = [
            AssocPlanProcess(
                scenario=scenario, process=standby_arrival, plan=plan, ordinal=0
            ),
            AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
            AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=2),
            AssocPlanProcess(
                scenario=scenario, process=standby_departure, plan=plan, ordinal=3
            ),
        ]
        session.add_all(assocs)

        session.commit()
        return scenario

    @pytest.fixture()
    def session(self):
        """
        Creates a session with the eflips-model schema
        NOTE: THIS DELETE ALL DATA IN THE DATABASE
        :return: an SQLAlchemy Session with the eflips-model schema
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


class TestApi(TestHelpers):
    def test_init_simulation(self, session, full_scenario):
        # We need to set the consumption values for all vehicle types to 1
        for vehicle_type in full_scenario.vehicle_types:
            vehicle_type.consumption = 1
        session.commit()

        simulation_host = init_simulation(
            full_scenario, simple_consumption_simulation=True
        )

    def test_run_simulation(self, session, full_scenario, tmp_path):
        # We need to set the consumption values for all vehicle types to 1
        for vehicle_type in full_scenario.vehicle_types:
            vehicle_type.consumption = 1
        session.commit()

        simulation_host = init_simulation(
            full_scenario, simple_consumption_simulation=True
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
