import os

import pytest
from eflips.model import (
    Base,
    setup_database,
    Scenario,
    VehicleType,
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
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from eflips.depot.api import (
    simple_consumption_simulation,
    simulate_scenario,
    SmartChargingStrategy,
)


class BaseTest:
    @pytest.fixture(scope="module")
    def session(self):
        """
        Creates a session with the eflips-model schema.

        NOTE: THIS DELETE ALL DATA IN THE DATABASE
        :return: an SQLAlchemy Session with the eflips-model schema
        """
        url = os.environ["DATABASE_URL"]
        engine = create_engine(
            url, echo=False
        )  # Change echo to True to see SQL queries
        Base.metadata.drop_all(engine)
        setup_database(engine)
        session = Session(bind=engine)
        yield session
        session.close()

    @pytest.fixture(scope="module")
    def scenario(self, session) -> Scenario:
        """
        Creates and simulates a sample scenario.

        :param session: An SQLAlchemy session (from the ``session`` fixture)
        :return: A Scenario object
        """
        scenario = Scenario(name="Entenhausen 01_2024")
        session.add(scenario)

        # Add a vehicle type without a battery type
        # Doppeldecker mit mittlerer Batterie
        vehicle_type_1 = VehicleType(
            scenario=scenario,
            name="Bus Typ Dagobert",
            battery_capacity=200,
            charging_curve=[[0, 200], [1, 150]],
            opportunity_charging_capable=True,
            consumption=1,
        )
        session.add(vehicle_type_1)

        # Add second vehicle type without a battery type
        # Kleiner Bus mit kleiner Batterie
        vehicle_type_2 = VehicleType(
            scenario=scenario,
            name="Bus Typ Düsentrieb",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            opportunity_charging_capable=True,
            consumption=1,
        )
        session.add(vehicle_type_2)

        # Add third vehicle type without a battery type
        # Langer Bus mit großer Batterie
        vehicle_type_3 = VehicleType(
            scenario=scenario,
            name="Bus Typ Panzerknacker",
            battery_capacity=300,
            charging_curve=[[0, 450], [1, 350]],
            opportunity_charging_capable=True,
            consumption=1,
        )
        session.add(vehicle_type_3)

        # -----------------------------------------
        # Add a VehicleClass
        vehicle_class = VehicleClass(
            scenario=scenario,
            name="Test Vehicle Class",
            vehicle_types=[vehicle_type_1],
        )
        session.add(vehicle_class)

        # Add a vehicle
        vehicle = Vehicle(
            scenario=scenario,
            vehicle_type=vehicle_type_1,
            name="Test Vehicle",
            name_short="TV",
        )
        session.add(vehicle)

        # -----------------------------------------
        # Anlegen von drei Buslinien
        line1 = Line(
            scenario=scenario,
            name="Oberstadt",
            name_short="OS",
        )
        session.add(line1)

        line2 = Line(scenario=scenario, name="Unterstadt", name_short="US")
        session.add(line2)

        line3 = Line(scenario=scenario, name="Holländisches Viertel", name_short="HV")
        session.add(line3)

        # -----------------------------------------
        # Anlegen von Stopps entlang der Linie Oberstadt
        stop_1 = Station(
            scenario=scenario,
            name="Industriepark",
            name_short="OS1",
            geom="POINT(0 0 0)",
            is_electrified=False,
        )
        session.add(stop_1)

        stop_2 = Station(
            scenario=scenario,
            name="Duckstraße",
            name_short="OS2",
            geom="POINT(1 0 0)",
            is_electrified=False,
        )
        stop_3 = Station(
            scenario=scenario,
            name="Alte Kirche",
            name_short="OS3",
            geom="POINT(2 0 0)",
            is_electrified=False,
        )

        stop_4 = Station(
            scenario=scenario,
            name="Düsentrieb Werkstatt",
            name_short="US1",
            geom="POINT(0 1 0)",
            is_electrified=False,
        )
        stop_5 = Station(
            scenario=scenario,
            name="Geldspeicher",
            name_short="US2",
            geom="POINT(0 4 0)",
            is_electrified=False,
        )

        stop_6 = Station(
            scenario=scenario,
            name="Milliardärsclub",
            name_short="HV1",
            geom="POINT(0 0 0)",
            is_electrified=False,
        )
        stop_7 = Station(
            scenario=scenario,
            name="Emil-Erpel-Statue",
            name_short="HV2",
            geom="POINT(0 0 2)",
            is_electrified=False,
        )
        stop_8 = Station(
            scenario=scenario,
            name="Rathaus",
            name_short="HV3",
            geom="POINT(0 -2 4)",
            is_electrified=False,
        )

        session.add_all([stop_2, stop_3, stop_4, stop_5, stop_6, stop_7, stop_8])

        # -----------------------------------------
        # Anlegen der Route für die Hinfahrt auf Linie Oberstadt
        route_100_hin = Route(
            scenario=scenario,
            name="Route Oberstadt Hin",
            name_short="ROS_Hin",
            departure_station=stop_1,
            arrival_station=stop_3,
            line=line1,
            distance=5000,
        )
        assocs = [
            AssocRouteStation(
                scenario=scenario,
                station=stop_1,
                route=route_100_hin,
                elapsed_distance=0,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_2,
                route=route_100_hin,
                elapsed_distance=2500,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_3,
                route=route_100_hin,
                elapsed_distance=5000,
            ),
        ]
        route_100_hin.assoc_route_stations = assocs
        session.add(route_100_hin)

        # -----------------------------------------
        # Anlegen der Route für Rückfahrt auf Linie Oberstadt
        route_100_rueck = Route(
            scenario=scenario,
            name="Route Oberstadt Rück",
            name_short="ROS_Rueck",
            departure_station=stop_3,
            arrival_station=stop_1,
            line=line1,
            distance=5000,
        )
        assocs = [
            AssocRouteStation(
                scenario=scenario,
                station=stop_3,
                route=route_100_rueck,
                elapsed_distance=0,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_2,
                route=route_100_rueck,
                elapsed_distance=2500,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_1,
                route=route_100_rueck,
                elapsed_distance=5000,
            ),
        ]
        route_100_rueck.assoc_route_stations = assocs
        session.add(route_100_rueck)

        # -----------------------------------------
        # Anlegen der Hin- und Rückrouten für Linie Unterstadt
        route_M40_hin = Route(
            scenario=scenario,
            name="Route Unterstadt Hin",
            name_short="RUS_Hin",
            departure_station=stop_4,
            arrival_station=stop_5,
            line=line2,
            distance=1200,
        )

        assocs = [
            AssocRouteStation(
                scenario=scenario,
                station=stop_4,
                route=route_M40_hin,
                elapsed_distance=0,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_5,
                route=route_M40_hin,
                elapsed_distance=1200,
            ),
        ]
        route_M40_hin.assoc_route_stations = assocs
        session.add(route_M40_hin)

        # -----------------------------------------
        route_M40_rueck = Route(
            scenario=scenario,
            name="Route Unterstadt Rück",
            name_short="RUS_Rück",
            departure_station=stop_5,
            arrival_station=stop_4,
            line=line2,
            distance=1200,
        )

        assocs = [
            AssocRouteStation(
                scenario=scenario,
                station=stop_5,
                route=route_M40_rueck,
                elapsed_distance=0,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_4,
                route=route_M40_rueck,
                elapsed_distance=1200,
            ),
        ]
        route_M40_rueck.assoc_route_stations = assocs
        session.add(route_M40_rueck)

        # -----------------------------------------
        # Anlegen der Hin- und Rückrouten für Linie Holländisches Viertel
        route_150_hin = Route(
            scenario=scenario,
            name="Route Holländisches Viertel Hin",
            name_short="RHV_Hin",
            departure_station=stop_6,
            arrival_station=stop_8,
            line=line3,
            distance=3000,
        )

        assocs = [
            AssocRouteStation(
                scenario=scenario,
                station=stop_6,
                route=route_150_hin,
                elapsed_distance=0,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_7,
                route=route_150_hin,
                elapsed_distance=1350,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_8,
                route=route_150_hin,
                elapsed_distance=3000,
            ),
        ]
        route_150_hin.assoc_route_stations = assocs
        session.add(route_150_hin)

        # -----------------------------------------
        # Anlegen der Route 6 auf Linie Holländisches Viertel
        route_150_rueck = Route(
            scenario=scenario,
            name="Route Holländisches Viertel Rück",
            name_short="RHV_Rueck",
            departure_station=stop_8,
            arrival_station=stop_6,
            line=line3,
            distance=3000,
        )

        assocs = [
            AssocRouteStation(
                scenario=scenario,
                station=stop_8,
                route=route_150_rueck,
                elapsed_distance=0,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_7,
                route=route_150_rueck,
                elapsed_distance=1650,
            ),
            AssocRouteStation(
                scenario=scenario,
                station=stop_6,
                route=route_150_rueck,
                elapsed_distance=3000,
            ),
        ]
        route_150_rueck.assoc_route_stations = assocs
        session.add(route_150_rueck)

        # -----------------------------------------

        from datetime import datetime, timedelta, timezone

        # Schedule
        first_rotation_departure = datetime(
            year=2024, month=2, day=1, hour=12, minute=0, second=0, tzinfo=timezone.utc
        )
        interval = timedelta(minutes=30)
        duration = timedelta(minutes=20)

        for h in range(4):
            rotation = Rotation(
                name=f"Umlauf {h}",
                scenario=scenario,
                trips=[],  # Zunächst leer lassen, "relationships" werden automatisch synchroniseirt
                vehicle_type=vehicle_type_1,
                allow_opportunity_charging=False,
            )
            session.add(rotation)

            first_departure = first_rotation_departure + timedelta(minutes=10 * h)

            trips = []
            for i in range(15):
                # ALLES AB HIER NOCHMAL PRÜFEN & ERWEITERN (Trips und Stopzeiten) !!!!!
                # Oberstadt Hin (Route 100_hin)
                trips.append(
                    Trip(
                        scenario=scenario,
                        route=route_100_hin,
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
                # Oberstadt Rück (Route 2)
                trips.append(
                    Trip(
                        scenario=scenario,
                        route=route_100_rueck,
                        trip_type=TripType.PASSENGER,
                        departure_time=first_departure + (2 * i + 1) * interval,
                        arrival_time=first_departure
                        + (2 * i + 1) * interval
                        + duration,
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
                        arrival_time=first_departure
                        + (2 * i + 1) * interval
                        + duration,
                    ),
                ]
                trips[-1].stop_times = stop_times
            session.add_all(trips)

        # Create a simple depot

        depot = Depot(
            scenario=scenario, name="Entenhausen Depot", name_short="ED", station=stop_1
        )
        session.add(depot)

        # Create plan

        plan = Plan(scenario=scenario, name="Entenhausen Plan")
        session.add(plan)

        depot.default_plan = plan

        # Create areas
        arrival_area = Area(
            scenario=scenario,
            name="Entenhausen Depot Arrival Area",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=6,
            vehicle_type=vehicle_type_1,
        )
        session.add(arrival_area)

        cleaning_area = Area(
            scenario=scenario,
            name="Entenhausen Depot Cleaning Area",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=6,
            vehicle_type=vehicle_type_1,
        )
        session.add(cleaning_area)

        charging_area = Area(
            scenario=scenario,
            name="Entenhausen Depot Area",
            depot=depot,
            area_type=AreaType.LINE,
            row_count=2,
            capacity=6,
            vehicle_type=vehicle_type_1,
        )
        session.add(charging_area)

        # Create processes
        standby_arrival = Process(
            name="Standby Arrival",
            scenario=scenario,
            dispatchable=False,
        )
        session.add(standby_arrival)

        clean = Process(
            name="Clean",
            scenario=scenario,
            dispatchable=False,
            duration=timedelta(minutes=30),
        )
        session.add(clean)

        charging = Process(
            name="Charging",
            scenario=scenario,
            dispatchable=False,
            electric_power=150,
        )
        session.add(charging)

        standby_departure = Process(
            name="Standby Departure",
            scenario=scenario,
            dispatchable=True,
        )
        session.add(standby_departure)

        # Connect the areas and processes. *The final area needs to have both a charging and standby_departure process*

        arrival_area.processes.append(standby_arrival)
        cleaning_area.processes.append(clean)
        charging_area.processes.append(charging)
        charging_area.processes.append(standby_departure)

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

        # We are currently in the test_input.py file so we do not run the simulation
        # eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=True)
        # eflips.depot.api.simulate_scenario(scenario)
        # eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=False)
        # session.commit()

        return scenario

    @pytest.fixture(scope="module")
    def post_simulation_scenario_no_smart_charging(self, session, scenario):
        # Run the simulation
        simple_consumption_simulation(scenario, initialize_vehicles=True)
        simulate_scenario(scenario, smart_charging_strategy=SmartChargingStrategy.NONE)
        session.commit()
        simple_consumption_simulation(scenario, initialize_vehicles=False)

        return scenario
