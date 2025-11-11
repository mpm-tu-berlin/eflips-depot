import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from eflips.model import (
    AssocRouteStation,
    BatteryType,
    Line,
    Route,
    Scenario,
    StopTime,
    Trip,
    TripType,
    VehicleClass,
)
from eflips.model import (
    VehicleType,
    AreaType,
    Area,
    AssocAreaProcess,
    AssocPlanProcess,
    Plan,
    Depot,
    Station,
    Process,
    Vehicle,
    Event,
    Rotation,
)
from geoalchemy2.shape import from_shape
from shapely import Point

from api.test_api import TestHelpers
from eflips.depot.api import (
    simple_consumption_simulation,
    simulate_scenario,
    generate_depot_optimal_size,
    generate_optimal_depot_layout,
)

from eflips.depot.api.private.depot import DepotConfigurationWish, AreaInformation
from eflips.depot.api.private.depot import (
    area_needed_for_vehicle_parking,
    generate_depot,
    depot_smallest_possible_size,
    group_rotations_by_start_end_stop,
)


class TestAreaCalc(TestHelpers):
    def test_area_calc_line(self, session, full_scenario):
        vt = session.query(VehicleType).first()
        vt.length = 12
        vt.width = 2.35

        # The area should remain equal for +1…+6 vehicles, as one line is added for each 6 vehicles.
        area_size = area_needed_for_vehicle_parking(
            vt, area_type=AreaType.LINE, count=13
        )
        for i in range(14, 19):
            assert (
                area_needed_for_vehicle_parking(vt, area_type=AreaType.LINE, count=i)
                == area_size
            )

    def test_area_calc_compare_line_direct(self, session, full_scenario):
        vt = session.query(VehicleType).first()
        vt.length = 12
        vt.width = 2.35

        area_size_all_direct = area_needed_for_vehicle_parking(
            vt, area_type=AreaType.DIRECT_ONESIDE, count=120
        )
        area_size_all_line = area_needed_for_vehicle_parking(
            vt, area_type=AreaType.LINE, count=120
        )
        assert area_size_all_direct > area_size_all_line

    def test_are_calc_direct_one(self, session, full_scenario):
        # We test by comparing the results with danial's formula, which should give the same results for zero spacing.
        vt = session.query(VehicleType).first()
        vt.length = 12
        vt.width = 2.35

        counts = range(1, 10)
        spacing = 0
        angle = 45

        for count in counts:
            width_danial = (
                math.sin(math.radians(angle)) * vt.width
                + math.sin(math.radians(angle)) * vt.length
            )
            length_danial = (
                math.sin(math.radians(angle)) * vt.length
                + math.sin(math.radians(angle)) * vt.width
                + (count - 1) * (2 * math.cos(math.radians(angle)) * vt.width)
            )
            area_danial = width_danial * length_danial

            area = area_needed_for_vehicle_parking(
                vt,
                area_type=AreaType.DIRECT_ONESIDE,
                count=count,
                spacing=spacing,
                angle=angle,
            )
            assert np.isclose(area, area_danial, rtol=1e-5)


class TestGenerateDepot(TestHelpers):
    @pytest.fixture
    def no_depot_scenario(self, session, full_scenario):
        session.query(AssocAreaProcess).delete()
        session.query(Area).delete()
        session.query(AssocPlanProcess).delete()
        session.query(Process).delete()
        session.query(Depot).delete()
        session.query(Plan).delete()

        session.query(Rotation).update({"vehicle_id": None})
        session.query(Event).delete()
        session.query(Vehicle).delete()

        session.flush()
        return full_scenario

    def test_generate_depot_depot_invalid_line_count(self, session, no_depot_scenario):
        # Here, we generate a depot with 11 line, 1 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 11

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        with pytest.raises(ValueError):
            generate_depot(
                capacity_of_areas=vt_capacity,
                station=station_to_create_at,
                scenario=no_depot_scenario,
                session=session,
            )

    def test_generate_depot_depot_invalid_direct_twoside_count(
        self, session, no_depot_scenario
    ):
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 3
                else:
                    vt_capacity[vt][area_type] = 0

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        with pytest.raises(ValueError):
            generate_depot(
                capacity_of_areas=vt_capacity,
                station=station_to_create_at,
                scenario=no_depot_scenario,
                session=session,
            )

    def test_generate_depot(self, session, no_depot_scenario):
        # Here, we generate a depot with 12 line, 12 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 12

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        generate_depot(
            capacity_of_areas=vt_capacity,
            station=station_to_create_at,
            scenario=no_depot_scenario,
            session=session,
        )

        # What should happen is that now, a depot exists in the database
        depot = session.query(Depot).one()
        # The depot should have six charging areas (one line area with 2 lines, one direct for each vehicle type), two shunting areas,
        # one cleaning area and one waiting area.
        assert len(depot.areas) == 8

        # The plan should have
        # 1) shunting
        # 2) cleaning
        # 3) shunting
        # 4) charging
        # 5) standby pre_departure
        assert len(depot.default_plan.processes) == 5

        # Simulate the depot
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=True)
        simulate_scenario(no_depot_scenario)
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=False)

    def test_generate_depot_no_shunting(self, session, no_depot_scenario):
        # Here, we generate a depot with 12 line, 12 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 12

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        generate_depot(
            capacity_of_areas=vt_capacity,
            station=station_to_create_at,
            scenario=no_depot_scenario,
            shunting_duration=None,
            session=session,
        )

        # What should happen is that now, a depot exists in the database
        depot = session.query(Depot).one()
        # The depot should have six charging areas (one line area with 2 lines, one direct for each vehicle type), ~~two shunting areas~~,
        # one cleaning area and one waiting area.
        assert len(depot.areas) == 6

        # The plan should have
        # 1) ~~shunting~~
        # 2) cleaning
        # 3) ~~shunting~~
        # 4) charging
        # 5) standby pre_departure
        assert len(depot.default_plan.processes) == 3

        # Simulate the depot
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=True)
        simulate_scenario(no_depot_scenario)
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=False)

    def test_generate_depot_no_cleaning(self, session, no_depot_scenario):
        # Here, we generate a depot with 12 line, 12 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 12

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        generate_depot(
            capacity_of_areas=vt_capacity,
            station=station_to_create_at,
            scenario=no_depot_scenario,
            cleaning_duration=None,
            session=session,
        )

        # What should happen is that now, a depot exists in the database
        depot = session.query(Depot).one()
        # The depot should have six charging areas (one line area with 2 lines, one direct for each vehicle type), two shunting areas,
        # ~~one cleaning area~~ and one waiting area.
        assert len(depot.areas) == 7

        # The plan should have
        # 1) shunting
        # 2) ~~cleaning~~
        # 3) shunting
        # 4) charging
        # 5) standby pre_departure
        assert len(depot.default_plan.processes) == 4

        # Simulate the depot
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=True)
        simulate_scenario(no_depot_scenario)
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=False)

    def test_generate_depot_no_shunting_no_cleaning(self, session, no_depot_scenario):
        # Here, we generate a depot with 12 line, 12 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 12

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        generate_depot(
            capacity_of_areas=vt_capacity,
            station=station_to_create_at,
            scenario=no_depot_scenario,
            shunting_duration=None,
            cleaning_duration=None,
            session=session,
        )

        # What should happen is that now, a depot exists in the database
        depot = session.query(Depot).one()
        # The depot should have six charging areas (one line area with two lines, one direct for each vehicle type), ~~two shunting areas~~,
        # ~~one cleaning area~~ and one waiting area.
        assert len(depot.areas) == 5

        # The plan should have
        # 1) ~~shunting~~
        # 2) ~~cleaning~~
        # 3) ~~shunting~~
        # 4) charging
        # 5) standby pre_departure
        assert len(depot.default_plan.processes) == 2

        # Simulate the depot
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=True)
        simulate_scenario(no_depot_scenario)
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=False)


class TestGenerateOptimalDepot(TestHelpers):
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
            length=10,
            width=2.5,
            height=4,
            empty_mass=12000,
            allowed_mass=16760,
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
            length=10,
            width=2.5,
            height=4,
            empty_mass=12000,
            allowed_mass=16760,
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
            geom=from_shape(Point(0, 0), srid=4326),
            is_electrified=False,
        )
        session.add(stop_1)

        stop_2 = Station(
            scenario=scenario,
            name="Test Station 2",
            name_short="TS2",
            geom=from_shape(Point(1, 0), srid=4326),
            is_electrified=False,
        )
        session.add(stop_2)

        stop_3 = Station(
            scenario=scenario,
            name="Test Station 3",
            name_short="TS3",
            geom=from_shape(Point(2, 0), srid=4326),
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
        number_of_rotations = 20
        for vehicle_type in scenario.vehicle_types:
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

        # We need to set the consumption values for all vehicle types to 1
        for vehicle_type in scenario.vehicle_types:
            vehicle_type.consumption = 1
        session.flush()

        session.commit()
        return scenario

    def test_calculate_depot_optimal_size(self, session, full_scenario):
        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )

        # Run a consumption simulation
        simple_consumption_simulation(full_scenario, initialize_vehicles=True)

        depot_smallest_possible_size(
            station_to_create_at, full_scenario, session, standard_block_length=6
        )

    def test_generate_optimal_depot(self, session, full_scenario):
        generate_depot_optimal_size(full_scenario, delete_existing_depot=True)

        depot = session.query(Depot).filter(Depot.scenario_id == full_scenario.id).all()
        assert len(depot) != 0, "No depot was created in the scenario!"

        areas = session.query(Area).filter(Area.scenario_id == full_scenario.id).all()
        assert len(areas) != 0, "No areas were created in the depot!"

    def test_generate_optimal_depot_auto_generate(self, session, full_scenario):
        station_rotation_group = group_rotations_by_start_end_stop(
            full_scenario.id, session
        )

        depot_wishes = []
        for station, rotation_groups in station_rotation_group.items():
            depot_wish = DepotConfigurationWish(
                station_id=station[0].id,
                auto_generate=True,
                default_power=150,
                standard_block_length=6,
            )

            depot_wishes.append(depot_wish)
        generate_optimal_depot_layout(
            depot_wishes, full_scenario, delete_existing_depot=True
        )

        depots = (
            session.query(Depot).filter(Depot.scenario_id == full_scenario.id).all()
        )
        assert len(depots) == 1, "1 depot was expected to be created in the scenario!"

        areas = session.query(Area).filter(Area.scenario_id == full_scenario.id).all()
        assert len(areas) != 0, "No areas were created in the depot!"

    def test_generate_optimal_depot_from_wish(self, session, full_scenario):
        station_rotation_group = group_rotations_by_start_end_stop(
            full_scenario.id, session
        )

        depot_wishes = []

        area_infos = []
        vehicle_types = session.query(VehicleType).all()
        for vt in vehicle_types:
            area_info_direct = AreaInformation(
                vehicle_type_id=vt.id,
                area_type=AreaType.DIRECT_ONESIDE,
                capacity=20,
                power=150,
            )
            area_info_line = AreaInformation(
                vehicle_type_id=vt.id,
                area_type=AreaType.LINE,
                capacity=6,
                power=200,
                block_length=6,
            )
            area_infos.append(area_info_direct)
            area_infos.append(area_info_line)

            # area_infos.append(area_info)
        for station, rotation_groups in station_rotation_group.items():
            depot_wish = DepotConfigurationWish(
                station_id=station[0].id,
                auto_generate=False,
                default_power=150,
                standard_block_length=6,
                cleaning_slots=2,
                shunting_slots=2,
                shunting_duration=timedelta(minutes=5),
                cleaning_duration=timedelta(minutes=10),
                areas=area_infos,
            )

            depot_wishes.append(depot_wish)

        generate_optimal_depot_layout(
            depot_wishes, full_scenario, delete_existing_depot=True
        )

        depots = (
            session.query(Depot).filter(Depot.scenario_id == full_scenario.id).all()
        )
        assert len(depots) == 1, "1 depot was expected to be created in the scenario!"

        areas = session.query(Area).filter(Area.scenario_id == full_scenario.id).all()
        assert len(areas) != 0, "No areas were created in the depot!"

        charging_areas = (
            session.query(Area)
            .filter(
                Area.scenario_id == full_scenario.id,
                Area.vehicle_type_id.isnot(None),
            )
            .all()
        )
        assert len(charging_areas) == 2 * len(
            vehicle_types
        ), "Charging areas for each vehicle type were expected to be created in the depot!"
