import os
from datetime import datetime, timedelta, timezone

import eflips.model
import pytest
from eflips.model import (
    Area,
    AreaType,
    AssocPlanProcess,
    AssocRouteStation,
    Base,
    BatteryType,
    Depot,
    Event,
    EventType,
    Line,
    Plan,
    Process,
    Rotation,
    Route,
    Scenario,
    Station,
    StopTime,
    Trip,
    TripType,
    Vehicle,
    VehicleClass,
    VehicleType,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from eflips.depot.api import (
    generate_depot_layout,
    init_simulation,
    run_simulation,
    simple_consumption_simulation,
    simulate_scenario,
    add_evaluation_to_database,
)


class TestHelpers:
    @pytest.fixture()
    def scenario(self, session):
        """
        Creates a scenario.

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
        Creates a scenario that comes filled with sample content for each type.

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
                scenario=scenario, station=stop_2, route=route_2, elapsed_distance=100
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

        # Create a number of rotations
        number_of_rotations = 3
        for rotation_id in range(number_of_rotations):
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
                        dwell_duration=timedelta(minutes=1),
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

            first_departure += timedelta(minutes=20)

        # Create a simple depot

        depot = Depot(
            scenario=scenario, name="Test Depot", name_short="TD", station=stop_1
        )
        session.add(depot)

        # Create plan

        plan = Plan(scenario=scenario, name="Test Plan")
        session.add(plan)

        depot.default_plan = plan

        # Create areas
        arrival_area = Area(
            scenario=scenario,
            name="Arrival",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=number_of_rotations + 2,
        )
        session.add(arrival_area)

        cleaning_area = Area(
            scenario=scenario,
            name="Cleaning Area",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=1,
        )
        session.add(cleaning_area)
        cleaning_area.vehicle_type = vehicle_type

        charging_area = Area(
            scenario=scenario,
            name="Line Charging Area",
            depot=depot,
            area_type=AreaType.LINE,
            row_count=4,
            capacity=24,
        )
        session.add(charging_area)
        charging_area.vehicle_type = vehicle_type

        # Create processes
        clean = Process(
            name="Arrival Cleaning",
            scenario=scenario,
            dispatchable=False,
            duration=timedelta(minutes=30),
        )

        charging = Process(
            name="Charging",
            scenario=scenario,
            dispatchable=False,
            electric_power=15,
        )

        standby_departure = Process(
            name="Standby Pre-departure",
            scenario=scenario,
            dispatchable=True,
        )

        session.add(clean)
        session.add(charging)
        session.add(standby_departure)

        cleaning_area.processes.append(clean)
        charging_area.processes.append(charging)
        charging_area.processes.append(standby_departure)

        assocs = [
            AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=0),
            AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=1),
            AssocPlanProcess(
                scenario=scenario, process=standby_departure, plan=plan, ordinal=2
            ),
        ]
        session.add_all(assocs)

        # We need to set the consumption values for all vehicle types to 1
        for vehicle_type in scenario.vehicle_types:
            vehicle_type.consumption = 1
        session.flush()

        simple_consumption_simulation(scenario, initialize_vehicles=True)

        session.commit()
        return scenario

    @pytest.fixture()
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
        eflips.model.setup_database(engine)
        session = Session(bind=engine)
        yield session
        session.close()


class TestApi(TestHelpers):
    def test_run_simulation_by_id(self, session, full_scenario):
        # Make sure the DATABASE_URL is set
        assert "DATABASE_URL" in os.environ

        simulate_scenario(
            scenario=full_scenario.id,
        )

    def test_run_simulation_by_id_and_url(self, session, full_scenario):
        # Make sure the DATABASE_URL is set
        assert "DATABASE_URL" in os.environ

        simulate_scenario(
            scenario=full_scenario.id,
            database_url=os.environ["DATABASE_URL"],
        )

    def test_run_simulation_by_object_with_id_and_url(self, session, full_scenario):
        # Make sure the DATABASE_URL is set
        assert "DATABASE_URL" in os.environ

        # Create a simple object with an id attribute
        class ScenarioId:
            def __init__(self, id):
                self.id = id

        scen = ScenarioId(full_scenario.id)

        simulate_scenario(
            scenario=scen.id,
            database_url=os.environ["DATABASE_URL"],
        )

    def test_simulate_scenario(self, session, full_scenario):
        # Make sure the DATABASE_URL is set
        assert "DATABASE_URL" in os.environ

        simulate_scenario(
            scenario=full_scenario,
        )

    def test_init_simulation(self, session, full_scenario):
        simulation_host = init_simulation(
            full_scenario,
            session,
        )

    def test_run_simulation(self, session, full_scenario, tmp_path):
        simulation_host = init_simulation(
            full_scenario,
            session,
        )

        depot_evaluations = run_simulation(simulation_host)
        assert len(depot_evaluations) == 1

        add_evaluation_to_database(full_scenario, depot_evaluations, session)
        events = (
            session.query(Event).filter(Event.scenario_id == full_scenario.id).all()
        )
        assert len(events) > 0

    def test_run_simulation_too_small_battery(self, session, full_scenario, tmp_path):
        """
        This tests the consumption simulation with a batter that is way too small.

        The expected result is that the simulation finishes, with no extra vehicles being added.

        The old result was that the simulation creates a high number of vehicles, which is not correct.

        :param session:
        :param full_scenario:
        :param tmp_path:
        :return:
        """
        session.query(VehicleType).filter(
            VehicleType.scenario_id == full_scenario.id
        ).update({"battery_capacity": 1})

        # Delete the existing simulation results
        rotation_q = session.query(Rotation).filter(
            Rotation.scenario_id == full_scenario.id
        )
        rotation_q.update({"vehicle_id": None})
        session.query(Event).filter(Event.scenario_id == full_scenario.id).delete()
        session.query(Vehicle).filter(Vehicle.scenario_id == full_scenario.id).delete()

        simple_consumption_simulation(full_scenario, initialize_vehicles=True)

        session.commit()

        simulation_host = init_simulation(
            full_scenario,
            session,
        )

        with pytest.warns(UserWarning):
            depot_evaluations = run_simulation(simulation_host)

        assert len(depot_evaluations) == 1

        for depot_id, depot_evaluation in depot_evaluations.items():
            assert depot_evaluation.nvehicles_used_calculation()["2"] == 3

    def test_create_depot(self, session, full_scenario):
        generate_depot_layout(
            scenario=full_scenario, charging_power=90, delete_existing_depot=True
        )

        # Check that the depot was created
        assert (
            session.query(Depot).filter(Depot.scenario_id == full_scenario.id).count()
            == 1
        )

        areas = session.query(Area).filter(Area.scenario_id == full_scenario.id).all()
        assert isinstance(areas, list) and len(areas) != 0

        processes = (
            session.query(Process).filter(Area.scenario_id == full_scenario.id).all()
        )
        assert isinstance(processes, list) and len(processes) != 0

        plans = session.query(Plan).filter(Area.scenario_id == full_scenario.id).all()
        assert isinstance(plans, list) and len(plans) != 0

    def test_simulate_scenario_with_depot_generation(self, session, full_scenario):
        generate_depot_layout(
            scenario=full_scenario, charging_power=90, delete_existing_depot=True
        )

        simulate_scenario(
            scenario=full_scenario,
        )
        session.commit()

    def test_reassign_vehicle_id(self, session, full_scenario):
        generate_depot_layout(
            scenario=full_scenario, charging_power=90, delete_existing_depot=True
        )

        simulate_scenario(
            scenario=full_scenario,
        )
        session.commit()

        # check if there are any gaps bewteen vehicle driving and depot processes. Only suitable for simple
        # consumption simulation for now

        vehicle_list = session.query(Vehicle).all()

        for vehicle in vehicle_list:
            list_of_events = (
                session.query(Event)
                .filter(Event.vehicle_id == vehicle.id)
                .order_by(Event.time_start)
                .all()
            )
            for i in range(len(list_of_events) - 1):
                if (
                    list_of_events[i].event_type == EventType.DRIVING
                    and list_of_events[i + 1].event_type != EventType.DRIVING
                ):
                    assert (
                        list_of_events[i].time_end == list_of_events[i + 1].time_start
                    )

    def test_interruptable_charging_process(self, session, full_scenario, tmp_path):
        # Run simulation once with not interruptable charging process

        # Update the charging process to be interruptable with very low power

        session.query(Process).filter(Process.name == "Charging").update(
            {"electric_power": 1, "dispatchable": True}
        )

        session.flush()

        charging_process = (
            session.query(Process).filter(Process.name == "Charging").first()
        )
        assert charging_process.dispatchable is True

        # Run simulation

        simulation_host = simulate_scenario(full_scenario)

        # Query all charging events and see if there is an increase in Soc
        all_charging_events = (
            session.query(Event)
            .filter(Event.event_type == EventType.CHARGING_DEPOT)
            .all()
        )

        # If end socs of depot charging events are less than 1, then the charging process is interruptable
        for event in all_charging_events:
            assert event.soc_end > event.soc_start
            assert event.soc_end < 1


class TestSimpleConsumptionSimulation(TestHelpers):
    def test_consumption_simulation_initial(self, session, full_scenario):
        # Delete old events, rotation-id-assignments and vehicle-id-assignments
        session.query(Event).filter(Event.scenario_id == full_scenario.id).delete()
        session.query(Rotation).filter(Rotation.scenario_id == full_scenario.id).update(
            {"vehicle_id": None}
        )
        session.query(Vehicle).filter(Vehicle.scenario_id == full_scenario.id).delete()

        assert (
            session.query(Event).filter(Event.scenario_id == full_scenario.id).count()
            == 0
        )
        assert (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .count()
            > 0
        )
        assert (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .filter(Rotation.vehicle_id != None)
            .count()
            == 0
        )

        simple_consumption_simulation(
            scenario=full_scenario, initialize_vehicles=True, calculate_timeseries=True
        )

        assert (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .count()
            == session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .filter(Rotation.vehicle_id != None)
            .count()
        )
        assert (
            session.query(Event).filter(Event.scenario_id == full_scenario.id).count()
            > 0
        )

        for rotation in (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .all()
        ):
            assert rotation.vehicle_id is not None
            for trip in rotation.trips:
                assert len(trip.events) == 1
                if trip == rotation.trips[0]:
                    assert trip.events[0].soc_start == 1
                else:
                    assert trip.events[0].soc_start < 1
                assert trip.events[0].soc_end < 1

    def test_consumption_simulation_initial_no_timeseries(self, session, full_scenario):
        # Delete old events, rotation-id-assignments and vehicle-id-assignments
        session.query(Event).filter(Event.scenario_id == full_scenario.id).delete()
        session.query(Rotation).filter(Rotation.scenario_id == full_scenario.id).update(
            {"vehicle_id": None}
        )
        session.query(Vehicle).filter(Vehicle.scenario_id == full_scenario.id).delete()

        assert (
            session.query(Event).filter(Event.scenario_id == full_scenario.id).count()
            == 0
        )
        assert (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .count()
            > 0
        )
        assert (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .filter(Rotation.vehicle_id != None)
            .count()
            == 0
        )

        simple_consumption_simulation(
            scenario=full_scenario, initialize_vehicles=True, calculate_timeseries=False
        )

        assert (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .count()
            == session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .filter(Rotation.vehicle_id != None)
            .count()
        )
        assert (
            session.query(Event).filter(Event.scenario_id == full_scenario.id).count()
            > 0
        )

        for rotation in (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .all()
        ):
            assert rotation.vehicle_id is not None
            for trip in rotation.trips:
                assert len(trip.events) == 1
                if trip == rotation.trips[0]:
                    assert trip.events[0].soc_start == 1
                else:
                    assert trip.events[0].soc_start < 1
                assert trip.events[0].soc_end < 1

    def test_consumption_simulation_initial_no_consumption(
        self, session, full_scenario
    ):
        # Delete old events, rotation-id-assignments and vehicle-id-assignments
        session.query(Event).filter(Event.scenario_id == full_scenario.id).delete()
        session.query(Rotation).filter(Rotation.scenario_id == full_scenario.id).update(
            {"vehicle_id": None}
        )
        session.query(Vehicle).filter(Vehicle.scenario_id == full_scenario.id).delete()

        # Unset consumption for all vehicle types
        session.query(VehicleType).update({"consumption": None})

        with pytest.raises(ValueError):
            simple_consumption_simulation(
                scenario=full_scenario,
                initialize_vehicles=True,
                calculate_timeseries=True,
            )

    def test_consumption_simulation_subsequent(self, session, full_scenario):
        # Delete old events, rotation-id-assignments and vehicle-id-assignments
        session.query(Event).filter(Event.scenario_id == full_scenario.id).delete()
        session.query(Rotation).filter(Rotation.scenario_id == full_scenario.id).update(
            {"vehicle_id": None}
        )
        session.query(Vehicle).filter(Vehicle.scenario_id == full_scenario.id).delete()

        # Run the "initial" step manually to validate the "subsequent" step
        rotations = (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .all()
        )
        for rotation in rotations:
            vehicle = Vehicle(
                vehicle_type_id=rotation.vehicle_type_id,
                scenario_id=full_scenario.id,
                name=f"Vehicle for rotation {rotation.id}",
            )
            session.add(vehicle)
            rotation.vehicle = vehicle

        simple_consumption_simulation(
            scenario=full_scenario, initialize_vehicles=False, calculate_timeseries=True
        )  # This is the "subsequent" step

        assert (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .count()
            == session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .filter(Rotation.vehicle_id != None)
            .count()
        )
        assert (
            session.query(Event).filter(Event.scenario_id == full_scenario.id).count()
            > 0
        )

        for rotation in (
            session.query(Rotation)
            .filter(Rotation.scenario_id == full_scenario.id)
            .all()
        ):
            assert rotation.vehicle_id is not None
            for trip in rotation.trips:
                assert len(trip.events) == 1
                if trip == rotation.trips[0]:
                    assert trip.events[0].soc_start == 1
                else:
                    assert trip.events[0].soc_start < 1
                assert trip.events[0].soc_end < 1
