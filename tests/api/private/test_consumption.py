import warnings
from datetime import datetime, timedelta, timezone

import pytest
from eflips.model import (
    AssocRouteStation,
    ConsistencyWarning,
    ConsumptionLut,
    Line,
    Rotation,
    Route,
    Station,
    StopTime,
    Temperatures,
    Trip,
    TripType,
    VehicleClass,
    VehicleType,
)
from geoalchemy2.shape import from_shape
from shapely.geometry import LineString, Point

from eflips.depot.api.private.consumption import (
    ConsumptionInformation,
    ConsumptionResult,
    TripSegment,
    clear_interpolator_cache,
    extract_trip_information,
)
from tests.api.test_api import TestHelpers


def _segment(
    *,
    distance_m=10_000.0,
    duration_s=1800.0,
    incline=0.0,
    level_of_loading=0.5,
    t_amb=10.0,
    end_time=None,
):
    """Build a TripSegment for tests with sensible defaults."""
    if end_time is None:
        end_time = datetime(2020, 1, 1, 12, 30, 0, tzinfo=timezone.utc)
    mean_speed_kmh = (distance_m / duration_s) * 3.6 if duration_s > 0 else 0.0
    return TripSegment(
        distance_m=distance_m,
        duration_s=duration_s,
        mean_speed_kmh=mean_speed_kmh,
        incline=incline,
        level_of_loading=level_of_loading,
        t_amb=t_amb,
        end_time=end_time,
    )


class TestConsumptionInformation(TestHelpers):
    @pytest.fixture
    def consumption_lut(self, session, scenario):
        """Creates a simple consumption LUT for testing."""
        # Create a simple vehicle type for the LUT
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type for LUT",
            name_short="TVTL",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            allowed_mass=18000,
            empty_mass=12000,
            opportunity_charging_capable=False,
        )
        session.add(vehicle_type)
        session.flush()

        # Create a vehicle class for the vehicle type
        vehicle_class = VehicleClass(
            scenario_id=vehicle_type.scenario_id,
            name=f"Consumption LUT for {vehicle_type.name_short}",
            vehicle_types=[vehicle_type],
        )
        session.add(vehicle_class)
        session.flush()  # To get assign the IDs

        # Create a LUT for the vehicle class using from_vehicle_type
        consumption_lut = ConsumptionLut.from_vehicle_type(vehicle_type, vehicle_class)

        # Now customize it with 4D data: incline, temperature, level_of_loading, and speed
        data_points = []
        values = []

        for incline in [0.0, 0.05]:
            for temp in [-10.0, 0.0, 10.0, 20.0]:
                for loading in [0.0, 0.5, 1.0]:
                    for speed in [10.0, 20.0, 30.0, 40.0]:
                        # Simple consumption formula: base + temp_factor + loading_factor + speed_factor + incline_factor
                        consumption = (
                            1.0  # base consumption
                            + (20 - temp) * 0.01  # temperature effect
                            + loading * 0.3  # loading effect
                            + speed * 0.02  # speed effect
                            + incline * 5.0  # incline effect
                        )
                        data_points.append([incline, temp, loading, speed])
                        values.append(consumption)

        consumption_lut.columns = [
            "incline",
            "t_amb",
            "level_of_loading",
            "mean_speed_kmh",
        ]
        consumption_lut.data_points = data_points
        consumption_lut.values = values

        session.add(consumption_lut)
        session.flush()
        return consumption_lut

    @pytest.fixture
    def trip_with_lut(self, session, scenario, consumption_lut):
        """Creates a complete trip setup with vehicle type, vehicle class, and LUT."""
        # Create vehicle type
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Bus with LUT",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            allowed_mass=18000,
            empty_mass=12000,
            consumption=None,  # Will use LUT
            opportunity_charging_capable=False,
        )
        session.add(vehicle_type)

        # Create vehicle class with consumption LUT
        vehicle_class = VehicleClass(
            scenario=scenario,
            name="Test Vehicle Class",
            vehicle_types=[vehicle_type],
            consumption_lut=consumption_lut,
        )
        session.add(vehicle_class)

        # Create stations
        station_1 = Station(
            scenario=scenario,
            name="Station 1",
            name_short="S1",
            geom=from_shape(Point(0, 0, 0), srid=4326),
            is_electrified=False,
        )
        station_2 = Station(
            scenario=scenario,
            name="Station 2",
            name_short="S2",
            geom=from_shape(Point(1, 0, 0), srid=4326),
            is_electrified=False,
        )
        session.add_all([station_1, station_2])

        # Create line
        line = Line(scenario=scenario, name="Test Line", name_short="TL")
        session.add(line)

        # Create route
        route = Route(
            scenario=scenario,
            name="Test Route",
            name_short="TR",
            departure_station=station_1,
            arrival_station=station_2,
            line=line,
            distance=10000,  # 10 km
        )
        assocs = [
            AssocRouteStation(
                scenario=scenario, station=station_1, route=route, elapsed_distance=0
            ),
            AssocRouteStation(
                scenario=scenario,
                station=station_2,
                route=route,
                elapsed_distance=10000,
            ),
        ]
        route.assoc_route_stations = assocs
        session.add(route)

        # Create rotation
        rotation = Rotation(
            scenario=scenario,
            vehicle_type=vehicle_type,
            allow_opportunity_charging=False,
        )
        session.add(rotation)

        # Create trip
        trip = Trip(
            scenario=scenario,
            route=route,
            rotation=rotation,
            trip_type=TripType.PASSENGER,
            departure_time=datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            arrival_time=datetime(2020, 1, 1, 12, 30, 0, tzinfo=timezone.utc),
        )
        session.add(trip)

        # Create temperature data
        temp_datetimes = [
            datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2020, 1, 1, 23, 59, 59, tzinfo=timezone.utc),
        ]
        temp_data = [5.0, 10.0, 8.0]

        temperatures = Temperatures(
            scenario=scenario,
            name="Test Temperatures",
            datetimes=temp_datetimes,
            data=temp_data,
            use_only_time=False,
        )
        session.add(temperatures)
        session.flush()

        return trip

    @pytest.fixture
    def trip_without_lut(self, session, scenario):
        """Creates a trip setup without LUT, using direct consumption value."""
        # Create vehicle type with direct consumption
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Bus without LUT",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            allowed_mass=18000,
            empty_mass=12000,
            consumption=1.5,  # Direct consumption in kWh/km
            opportunity_charging_capable=False,
        )
        session.add(vehicle_type)

        # Create vehicle class without LUT
        vehicle_class = VehicleClass(
            scenario=scenario,
            name="Test Vehicle Class No LUT",
            vehicle_types=[vehicle_type],
            consumption_lut=None,
        )
        session.add(vehicle_class)

        # Create stations
        station_1 = Station(
            scenario=scenario,
            name="Station 3",
            name_short="S3",
            geom=from_shape(Point(0, 0, 0), srid=4326),
            is_electrified=False,
        )
        station_2 = Station(
            scenario=scenario,
            name="Station 4",
            name_short="S4",
            geom=from_shape(Point(1, 0, 0), srid=4326),
            is_electrified=False,
        )
        session.add_all([station_1, station_2])

        # Create line
        line = Line(scenario=scenario, name="Test Line 2", name_short="TL2")
        session.add(line)

        # Create route
        route = Route(
            scenario=scenario,
            name="Test Route 2",
            name_short="TR2",
            departure_station=station_1,
            arrival_station=station_2,
            line=line,
            distance=15000,  # 15 km
        )
        assocs = [
            AssocRouteStation(
                scenario=scenario, station=station_1, route=route, elapsed_distance=0
            ),
            AssocRouteStation(
                scenario=scenario,
                station=station_2,
                route=route,
                elapsed_distance=15000,
            ),
        ]
        route.assoc_route_stations = assocs
        session.add(route)

        # Create rotation
        rotation = Rotation(
            scenario=scenario,
            vehicle_type=vehicle_type,
            allow_opportunity_charging=False,
        )
        session.add(rotation)

        # Create trip
        trip = Trip(
            scenario=scenario,
            route=route,
            rotation=rotation,
            trip_type=TripType.PASSENGER,
            departure_time=datetime(2020, 1, 1, 14, 0, 0, tzinfo=timezone.utc),
            arrival_time=datetime(2020, 1, 1, 14, 30, 0, tzinfo=timezone.utc),
        )
        session.add(trip)

        # Create temperature data
        temp_datetimes = [
            datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2020, 1, 1, 23, 59, 59, tzinfo=timezone.utc),
        ]
        temp_data = [5.0, 10.0, 8.0]

        temperatures = Temperatures(
            scenario=scenario,
            name="Test Temperatures 2",
            datetimes=temp_datetimes,
            data=temp_data,
            use_only_time=False,
        )
        session.add(temperatures)
        session.flush()

        return trip

    @pytest.fixture
    def trip_without_temperature(self, session, scenario):
        """Creates a trip setup without temperature data."""
        # Create vehicle type with direct consumption
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Bus without Temp",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            allowed_mass=18000,
            empty_mass=12000,
            consumption=1.2,
            opportunity_charging_capable=False,
        )
        session.add(vehicle_type)

        # Create vehicle class without LUT
        vehicle_class = VehicleClass(
            scenario=scenario,
            name="Test Vehicle Class No Temp",
            vehicle_types=[vehicle_type],
            consumption_lut=None,
        )
        session.add(vehicle_class)

        # Create stations
        station_1 = Station(
            scenario=scenario,
            name="Station 5",
            name_short="S5",
            geom=from_shape(Point(0, 0, 0), srid=4326),
            is_electrified=False,
        )
        station_2 = Station(
            scenario=scenario,
            name="Station 6",
            name_short="S6",
            geom=from_shape(Point(1, 0, 0), srid=4326),
            is_electrified=False,
        )
        session.add_all([station_1, station_2])

        # Create line
        line = Line(scenario=scenario, name="Test Line 3", name_short="TL3")
        session.add(line)

        # Create route
        route = Route(
            scenario=scenario,
            name="Test Route 3",
            name_short="TR3",
            departure_station=station_1,
            arrival_station=station_2,
            line=line,
            distance=20000,  # 20 km
        )
        assocs = [
            AssocRouteStation(
                scenario=scenario, station=station_1, route=route, elapsed_distance=0
            ),
            AssocRouteStation(
                scenario=scenario,
                station=station_2,
                route=route,
                elapsed_distance=20000,
            ),
        ]
        route.assoc_route_stations = assocs
        session.add(route)

        # Create rotation
        rotation = Rotation(
            scenario=scenario,
            vehicle_type=vehicle_type,
            allow_opportunity_charging=False,
        )
        session.add(rotation)

        # Create trip
        trip = Trip(
            scenario=scenario,
            route=route,
            rotation=rotation,
            trip_type=TripType.PASSENGER,
            departure_time=datetime(2020, 1, 1, 16, 0, 0, tzinfo=timezone.utc),
            arrival_time=datetime(2020, 1, 1, 16, 40, 0, tzinfo=timezone.utc),
        )
        session.add(trip)

        # Do NOT create temperature data for this scenario
        session.flush()

        return trip

    def test_extract_trip_information_with_lut(self, session, scenario, trip_with_lut):
        """Test extracting trip information when LUT is available (normal case)."""
        info = extract_trip_information(trip_with_lut.id, scenario)

        assert info is not None
        assert info.trip_id == trip_with_lut.id
        assert (
            info.consumption_lut is None
        )  # Should be None after calculation to save memory
        assert len(info.segments) >= 1
        assert sum(s.distance_m for s in info.segments) == pytest.approx(10_000.0)
        assert all(s.mean_speed_kmh > 0 for s in info.segments)
        assert all(s.t_amb is not None for s in info.segments)
        assert all(
            s.level_of_loading is not None and s.level_of_loading > 0
            for s in info.segments
        )
        total_kwh = sum(s.consumption_kwh for s in info.segments)
        assert total_kwh > 0

    def test_extract_trip_information_without_lut(
        self, session, scenario, trip_without_lut
    ):
        """Test extracting trip information without LUT (uses VehicleType.consumption directly)."""
        with pytest.warns(ConsistencyWarning, match="No consumption LUT found"):
            info = extract_trip_information(trip_without_lut.id, scenario)

        assert info is not None
        assert info.trip_id == trip_without_lut.id
        assert info.consumption_lut is None
        assert info.flat_consumption_per_km == 1.5
        assert sum(s.distance_m for s in info.segments) == pytest.approx(15_000.0)
        assert sum(s.consumption_kwh for s in info.segments) == pytest.approx(
            1.5 * 15.0
        )
        assert all(s.t_amb is not None for s in info.segments)

    def test_extract_trip_information_without_temperature(
        self, session, scenario, trip_without_temperature
    ):
        """Test extracting trip information without temperature data."""
        with pytest.warns(ConsistencyWarning, match="No temperatures found"):
            info = extract_trip_information(trip_without_temperature.id, scenario)

        assert info is not None
        assert info.trip_id == trip_without_temperature.id
        assert all(s.t_amb is None for s in info.segments)
        assert info.flat_consumption_per_km == 1.2
        assert sum(s.consumption_kwh for s in info.segments) == pytest.approx(
            1.2 * 20.0
        )

    def test_extract_trip_information_no_vehicle_class(self, session, scenario):
        """Test extracting trip information for VehicleType without VehicleClass but with consumption value."""
        # Create vehicle type with direct consumption but NO vehicle class
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Bus without Vehicle Class",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            allowed_mass=18000,
            empty_mass=12000,
            consumption=2.0,  # Direct consumption in kWh/km
            opportunity_charging_capable=False,
        )
        session.add(vehicle_type)

        # Create stations
        station_1 = Station(
            scenario=scenario,
            name="Station 7",
            name_short="S7",
            geom=from_shape(Point(0, 0, 0), srid=4326),
            is_electrified=False,
        )
        station_2 = Station(
            scenario=scenario,
            name="Station 8",
            name_short="S8",
            geom=from_shape(Point(1, 0, 0), srid=4326),
            is_electrified=False,
        )
        session.add_all([station_1, station_2])

        # Create line
        line = Line(scenario=scenario, name="Test Line 4", name_short="TL4")
        session.add(line)

        # Create route
        route = Route(
            scenario=scenario,
            name="Test Route 4",
            name_short="TR4",
            departure_station=station_1,
            arrival_station=station_2,
            line=line,
            distance=25000,  # 25 km
        )
        assocs = [
            AssocRouteStation(
                scenario=scenario, station=station_1, route=route, elapsed_distance=0
            ),
            AssocRouteStation(
                scenario=scenario,
                station=station_2,
                route=route,
                elapsed_distance=25000,
            ),
        ]
        route.assoc_route_stations = assocs
        session.add(route)

        # Create rotation
        rotation = Rotation(
            scenario=scenario,
            vehicle_type=vehicle_type,
            allow_opportunity_charging=False,
        )
        session.add(rotation)

        # Create trip
        trip = Trip(
            scenario=scenario,
            route=route,
            rotation=rotation,
            trip_type=TripType.PASSENGER,
            departure_time=datetime(2020, 1, 1, 18, 0, 0, tzinfo=timezone.utc),
            arrival_time=datetime(2020, 1, 1, 18, 50, 0, tzinfo=timezone.utc),
        )
        session.add(trip)

        # Create temperature data
        temp_datetimes = [
            datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2020, 1, 1, 23, 59, 59, tzinfo=timezone.utc),
        ]
        temp_data = [5.0, 10.0, 8.0]

        temperatures = Temperatures(
            scenario=scenario,
            name="Test Temperatures 3",
            datetimes=temp_datetimes,
            data=temp_data,
            use_only_time=False,
        )
        session.add(temperatures)
        session.flush()

        # Extract trip information - should work without VehicleClass
        with pytest.warns(ConsistencyWarning, match="No consumption LUT found"):
            info = extract_trip_information(trip.id, scenario)

        assert info is not None
        assert info.trip_id == trip.id
        assert info.consumption_lut is None
        assert info.flat_consumption_per_km == 2.0
        assert sum(s.distance_m for s in info.segments) == pytest.approx(25_000.0)
        assert sum(s.consumption_kwh for s in info.segments) == pytest.approx(
            2.0 * 25.0
        )
        assert all(s.t_amb is not None for s in info.segments)

    def test_consumption_information_calculate_with_lut(
        self, session, scenario, trip_with_lut, consumption_lut
    ):
        """Test the calculate() method with a valid LUT."""
        info = ConsumptionInformation(
            trip_id=1,
            segments=[_segment(distance_m=10_000.0, t_amb=10.0, incline=0.0)],
            consumption_lut=consumption_lut,
        )

        info.calculate()

        assert len(info.segments) == 1
        assert info.segments[0].consumption_kwh is not None
        assert info.segments[0].consumption_kwh > 0
        assert info.consumption_lut is None

    def test_consumption_information_calculate_with_interpolation(
        self, session, scenario, consumption_lut
    ):
        """Test that interpolation works for values between LUT points."""
        info = ConsumptionInformation(
            trip_id=1,
            segments=[
                _segment(
                    distance_m=10_000.0,
                    duration_s=10_000.0 / (25.0 / 3.6),  # mean_speed_kmh = 25
                    incline=0.025,
                    level_of_loading=0.25,
                    t_amb=5.0,
                )
            ],
            consumption_lut=consumption_lut,
        )

        info.calculate()

        assert info.segments[0].consumption_kwh > 0

    def test_consumption_information_calculate_extrapolation(
        self, session, scenario, consumption_lut
    ):
        """Test that extrapolation with nearest neighbor works when out of bounds."""
        info = ConsumptionInformation(
            trip_id=1,
            segments=[
                _segment(
                    distance_m=10_000.0,
                    duration_s=10_000.0
                    / (50.0 / 3.6),  # mean_speed_kmh = 50, outside LUT
                    incline=0.0,
                    level_of_loading=0.5,
                    t_amb=30.0,  # outside LUT
                )
            ],
            consumption_lut=consumption_lut,
        )

        info.calculate()

        assert info.segments[0].consumption_kwh > 0

    def test_consumption_information_calculate_vectorized_per_segment(
        self, session, scenario, consumption_lut
    ):
        """Vectorized calculate() should match independent per-segment evaluations."""
        clear_interpolator_cache()
        seg_inputs = [
            dict(
                distance_m=4_000.0,
                duration_s=600.0,
                incline=0.0,
                level_of_loading=0.0,
                t_amb=10.0,
            ),
            dict(
                distance_m=3_000.0,
                duration_s=400.0,
                incline=0.05,
                level_of_loading=0.5,
                t_amb=0.0,
            ),
            dict(
                distance_m=2_500.0,
                duration_s=300.0,
                incline=0.025,
                level_of_loading=1.0,
                t_amb=20.0,
            ),
        ]
        bulk = ConsumptionInformation(
            trip_id=1,
            segments=[_segment(**si) for si in seg_inputs],
            consumption_lut=consumption_lut,
        )
        bulk.calculate()

        for i, si in enumerate(seg_inputs):
            single = ConsumptionInformation(
                trip_id=1,
                segments=[_segment(**si)],
                consumption_lut=consumption_lut,
            )
            single.calculate()
            assert bulk.segments[i].consumption_kwh == pytest.approx(
                single.segments[0].consumption_kwh
            )

    def test_consumption_information_generate_result(self):
        """Test generating a ConsumptionResult from a multi-segment ConsumptionInformation."""
        t0 = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        info = ConsumptionInformation(
            trip_id=1,
            segments=[
                _segment(
                    distance_m=5_000.0,
                    duration_s=600.0,
                    end_time=t0 + timedelta(minutes=10),
                ),
                _segment(
                    distance_m=5_000.0,
                    duration_s=600.0,
                    end_time=t0 + timedelta(minutes=20),
                ),
            ],
            flat_consumption_per_km=1.5,
        )
        info.calculate()

        battery_capacity = 100.0
        result = info.generate_consumption_result(battery_capacity)

        assert isinstance(result, ConsumptionResult)
        assert result.delta_soc_total == pytest.approx(-15.0 / 100.0)
        assert result.timestamps == [s.end_time for s in info.segments]
        assert len(result.delta_soc) == 2
        assert result.delta_soc[0] == pytest.approx(-7.5 / 100.0)
        assert result.delta_soc[1] == pytest.approx(-15.0 / 100.0)
        assert (
            result.delta_soc[0] >= result.delta_soc[1]
        )  # monotonically non-increasing

    def test_consumption_information_generate_result_without_calculation(self):
        """Test that generating a result before calculate() raises an error."""
        info = ConsumptionInformation(
            trip_id=1,
            segments=[_segment()],
            flat_consumption_per_km=1.5,
        )

        with pytest.raises(
            ValueError,
            match="Consumption must be calculated before generating a result",
        ):
            info.generate_consumption_result(100.0)

    def test_consumption_information_missing_lut_columns(
        self, session, scenario, consumption_lut
    ):
        """Test that calculate() fails when LUT has incorrect columns."""
        # Clear cache so that the modified columns are re-validated
        clear_interpolator_cache()
        # Modify the LUT to have wrong columns
        consumption_lut.columns = ["wrong", "columns"]

        info = ConsumptionInformation(
            trip_id=1,
            segments=[_segment()],
            consumption_lut=consumption_lut,
        )

        with pytest.raises(
            ValueError,
            match="The consumption LUT must have the columns 'incline', 't_amb', 'level_of_loading', 'mean_speed_kmh'",
        ):
            info.calculate()

    def test_consumption_result_dataclass(self):
        """Test the ConsumptionResult dataclass."""
        result = ConsumptionResult(
            delta_soc_total=-0.15,
            timestamps=[
                datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                datetime(2020, 1, 1, 12, 15, 0, tzinfo=timezone.utc),
            ],
            delta_soc=[-0.075, -0.15],
        )

        assert result.delta_soc_total == -0.15
        assert len(result.timestamps) == 2
        assert len(result.delta_soc) == 2
        assert result.delta_soc[0] == -0.075
        assert result.delta_soc[1] == -0.15

    def test_multiple_consumption_luts_error(
        self, session, scenario, consumption_lut, trip_with_lut
    ):
        """Test that having multiple LUTs raises an error."""
        # Create a second vehicle type for the second LUT
        vehicle_type2 = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type 2 for LUT",
            name_short="TVT2",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            allowed_mass=18000,
            empty_mass=12000,
            opportunity_charging_capable=False,
        )
        session.add(vehicle_type2)
        session.flush()

        # Create a second vehicle class for the second LUT
        vehicle_class2 = VehicleClass(
            scenario_id=vehicle_type2.scenario_id,
            name="Second Vehicle Class",
            vehicle_types=[
                trip_with_lut.rotation.vehicle_type
            ],  # Reuse the first vehicle type
        )
        session.add(vehicle_class2)
        session.flush()

        # Create a second LUT using from_vehicle_type
        lut2 = ConsumptionLut.from_vehicle_type(vehicle_type2, vehicle_class2)
        session.add(lut2)
        session.flush()

        with pytest.raises(ValueError, match="Expected at most one consumption LUT"):
            extract_trip_information(trip_with_lut.id, scenario)

    @pytest.fixture
    def consumption_lut_with_hole(self, session, scenario):
        """Creates a consumption LUT that has one interior grid point missing (NaN hole).

        The hole is at (incline=0.0, t_amb=0.0, level_of_loading=0.5, speed=30.0).
        Querying at that exact point forces the RegularGridInterpolator to return NaN,
        which triggers the nearest-neighbor fallback.
        """
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type for Holey LUT",
            name_short="TVTH",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            allowed_mass=18000,
            empty_mass=12000,
            opportunity_charging_capable=False,
        )
        session.add(vehicle_type)
        session.flush()

        vehicle_class = VehicleClass(
            scenario_id=vehicle_type.scenario_id,
            name=f"Holey LUT for {vehicle_type.name_short}",
            vehicle_types=[vehicle_type],
        )
        session.add(vehicle_class)
        session.flush()

        consumption_lut = ConsumptionLut.from_vehicle_type(vehicle_type, vehicle_class)
        consumption_lut.columns = [
            "incline",
            "t_amb",
            "level_of_loading",
            "mean_speed_kmh",
        ]

        data_points = []
        values = []
        hole = (0.0, 0.0, 0.5, 30.0)  # (incline, t_amb, level_of_loading, speed)

        for incline in [0.0, 0.05]:
            for temp in [-10.0, 0.0, 10.0, 20.0]:
                for loading in [0.0, 0.5, 1.0]:
                    for speed in [10.0, 20.0, 30.0, 40.0]:
                        if (incline, temp, loading, speed) == hole:
                            continue  # leave this grid point missing
                        consumption = (
                            1.0
                            + (20 - temp) * 0.01
                            + loading * 0.3
                            + speed * 0.02
                            + incline * 5.0
                        )
                        data_points.append([incline, temp, loading, speed])
                        values.append(consumption)

        consumption_lut.data_points = data_points
        consumption_lut.values = values

        session.add(consumption_lut)
        session.flush()
        return consumption_lut

    def test_consumption_information_calculate_with_hole_in_lut(
        self, session, scenario, consumption_lut_with_hole
    ):
        """Test that the NN fallback is used (and cached) when the LUT has a NaN hole.

        The query point lands exactly on the missing grid entry, so the
        RegularGridInterpolator returns NaN and the NearestNDInterpolator takes over.
        The ConsistencyWarning should be emitted exactly once even when calculate()
        is called a second time for the same LUT.
        """
        clear_interpolator_cache()

        def make_info():
            # Single-segment trip whose inputs land exactly on the LUT's NaN hole.
            return ConsumptionInformation(
                trip_id=1,
                segments=[
                    _segment(
                        distance_m=10_000.0,
                        duration_s=10_000.0 / (30.0 / 3.6),  # mean_speed_kmh = 30
                        incline=0.0,
                        level_of_loading=0.5,
                        t_amb=0.0,
                    )
                ],
                consumption_lut=consumption_lut_with_hole,
            )

        # First call: NN interpolator is built and warning is emitted
        info = make_info()
        with pytest.warns(
            ConsistencyWarning, match=f"Consumption LUT {consumption_lut_with_hole.id}"
        ):
            info.calculate()

        assert info.segments[0].consumption_kwh > 0

        # Second call: NN interpolator is already cached — no NN warning this time
        info2 = make_info()
        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            info2.calculate()
        nn_warnings = [
            w
            for w in record
            if issubclass(w.category, ConsistencyWarning)
            and "nearest neighbor" in str(w.message).lower()
        ]
        assert len(nn_warnings) == 0, "NN warning should not fire on cache hit"
        assert info2.segments[0].consumption_kwh > 0

    def test_no_lut_no_consumption_value_error(
        self, session, scenario, trip_without_lut
    ):
        """Test that having no LUT and no consumption value raises an error."""
        # Unset the consumption value
        trip_without_lut.rotation.vehicle_type.consumption = None
        session.flush()

        with pytest.warns(ConsistencyWarning, match="No consumption LUT found"):
            with pytest.raises(
                ValueError,
                match="must have a consumption value set if no consumption LUT is available.",
            ):
                extract_trip_information(trip_without_lut.id, scenario)

    def test_extract_trip_information_inserts_route_vertex_knots(
        self, session, scenario, trip_without_lut
    ):
        """A >1km gap between stop_times with intermediate Z vertices should.

        produce extra segments — and the inclines should reflect Δz.
        """
        trip = trip_without_lut
        route = trip.route
        # Build a 5-vertex LineString whose 2D geodesic length matches route.distance
        # (15 km). 1° lon at the equator ≈ 111195 m, so 15 km ≈ 0.13490°. The middle
        # vertex sits at 100 m elevation: a smooth ramp up then back down.
        end_lon = 15_000.0 / 111_195.0
        route.geom = from_shape(
            LineString(
                [
                    (0.0, 0.0, 0.0),
                    (end_lon * 0.25, 0.0, 50.0),
                    (end_lon * 0.5, 0.0, 100.0),
                    (end_lon * 0.75, 0.0, 50.0),
                    (end_lon, 0.0, 0.0),
                ]
            ),
            srid=4326,
        )
        # Stop times only at the two endpoints — the gap between them is 15 km,
        # so route-vertex knots should be inserted.
        session.add(
            StopTime(
                scenario=scenario,
                station=route.departure_station,
                trip=trip,
                arrival_time=trip.departure_time,
                dwell_duration=timedelta(seconds=0),
            )
        )
        session.add(
            StopTime(
                scenario=scenario,
                station=route.arrival_station,
                trip=trip,
                arrival_time=trip.arrival_time,
                dwell_duration=timedelta(seconds=0),
            )
        )
        session.flush()

        with pytest.warns(ConsistencyWarning, match="No consumption LUT found"):
            info = extract_trip_information(trip.id, scenario)

        # The three interior vertices each split the trip further.
        assert len(info.segments) > 1
        # Cumulative distance still matches the route distance.
        assert sum(s.distance_m for s in info.segments) == pytest.approx(15_000.0)
        # First half climbs, second half descends — at least one positive and one negative incline.
        inclines = [s.incline for s in info.segments]
        assert any(i > 0 for i in inclines), "expected an uphill segment"
        assert any(i < 0 for i in inclines), "expected a downhill segment"

    def test_extract_trip_information_warns_on_missing_z(
        self, session, scenario, trip_without_lut
    ):
        """Stations without Z should trigger a single ConsistencyWarning per trip.

        and produce zero-incline segments.
        """
        # Strip the Z by clearing the station geom entirely (geom is nullable).
        trip_without_lut.route.departure_station.geom = None
        trip_without_lut.route.arrival_station.geom = None
        session.flush()

        with warnings.catch_warnings(record=True) as record:
            warnings.simplefilter("always")
            info = extract_trip_information(trip_without_lut.id, scenario)

        z_warnings = [
            w
            for w in record
            if issubclass(w.category, ConsistencyWarning)
            and "lacks a Z coordinate" in str(w.message)
        ]
        assert len(z_warnings) == 1
        assert all(s.incline == 0.0 for s in info.segments)
