import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from eflips.model import (
    AssocRouteStation,
    ConsistencyWarning,
    ConsumptionLut,
    Line,
    Rotation,
    Route,
    Station,
    Temperatures,
    Trip,
    TripType,
    VehicleClass,
    VehicleType,
)
from geoalchemy2.shape import from_shape
from shapely import Point

from tests.api.test_api import TestHelpers
from eflips.depot.api.private.consumption import (
    ConsumptionInformation,
    ConsumptionResult,
    extract_trip_information,
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
            geom=from_shape(Point(0, 0), srid=4326),
            is_electrified=False,
        )
        station_2 = Station(
            scenario=scenario,
            name="Station 2",
            name_short="S2",
            geom=from_shape(Point(1, 0), srid=4326),
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
            geom=from_shape(Point(0, 0), srid=4326),
            is_electrified=False,
        )
        station_2 = Station(
            scenario=scenario,
            name="Station 4",
            name_short="S4",
            geom=from_shape(Point(1, 0), srid=4326),
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
            geom=from_shape(Point(0, 0), srid=4326),
            is_electrified=False,
        )
        station_2 = Station(
            scenario=scenario,
            name="Station 6",
            name_short="S6",
            geom=from_shape(Point(1, 0), srid=4326),
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
        assert info.distance == 10.0  # 10 km
        assert info.average_speed > 0
        assert info.temperature is not None
        assert info.level_of_loading > 0
        assert info.consumption is not None
        assert info.consumption > 0
        assert info.consumption_per_km is not None
        assert info.consumption_per_km > 0

    def test_extract_trip_information_without_lut(
        self, session, scenario, trip_without_lut
    ):
        """Test extracting trip information without LUT (new functionality from commit)."""
        with pytest.warns(ConsistencyWarning, match="No consumption LUT found"):
            info = extract_trip_information(trip_without_lut.id, scenario)

        assert info is not None
        assert info.trip_id == trip_without_lut.id
        assert info.consumption_lut is None
        assert info.distance == 15.0  # 15 km
        assert info.consumption_per_km == 1.5  # Direct from vehicle type
        assert info.consumption == 1.5 * 15.0  # 22.5 kWh
        assert info.temperature is not None

    def test_extract_trip_information_without_temperature(
        self, session, scenario, trip_without_temperature
    ):
        """Test extracting trip information without temperature data (new functionality from commit)."""
        with pytest.warns(ConsistencyWarning, match="No temperatures found"):
            info = extract_trip_information(trip_without_temperature.id, scenario)

        assert info is not None
        assert info.trip_id == trip_without_temperature.id
        assert info.temperature is None  # Should be None when no temperature data
        assert info.consumption_per_km == 1.2
        assert info.consumption == 1.2 * 20.0  # 24 kWh

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
            geom=from_shape(Point(0, 0), srid=4326),
            is_electrified=False,
        )
        station_2 = Station(
            scenario=scenario,
            name="Station 8",
            name_short="S8",
            geom=from_shape(Point(1, 0), srid=4326),
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
        assert info.distance == 25.0  # 25 km
        assert info.consumption_per_km == 2.0  # Direct from vehicle type
        assert info.consumption == 2.0 * 25.0  # 50 kWh
        assert info.temperature is not None

    def test_consumption_information_calculate_with_lut(
        self, session, scenario, trip_with_lut, consumption_lut
    ):
        """Test the calculate() method with a valid LUT."""
        info = ConsumptionInformation(
            trip_id=1,
            consumption_lut=consumption_lut,
            average_speed=20.0,
            distance=10.0,
            temperature=10.0,
            level_of_loading=0.5,
            incline=0.0,
        )

        info.calculate()

        assert info.consumption is not None
        assert info.consumption > 0
        assert info.consumption_per_km is not None
        assert info.consumption_per_km > 0
        # After calculation, LUT should be cleared to save memory
        assert info.consumption_lut is None

    def test_consumption_information_calculate_with_interpolation(
        self, session, scenario, consumption_lut
    ):
        """Test that interpolation works for values between LUT points."""
        info = ConsumptionInformation(
            trip_id=1,
            consumption_lut=consumption_lut,
            average_speed=25.0,  # Between 20 and 30
            distance=10.0,
            temperature=5.0,  # Between 0 and 10
            level_of_loading=0.25,  # Between 0 and 0.5
            incline=0.025,  # Between 0 and 0.05
        )

        info.calculate()

        assert info.consumption is not None
        assert info.consumption > 0
        assert info.consumption_per_km is not None

    def test_consumption_information_calculate_extrapolation(
        self, session, scenario, consumption_lut
    ):
        """Test that extrapolation with nearest neighbor works when out of bounds."""
        info = ConsumptionInformation(
            trip_id=1,
            consumption_lut=consumption_lut,
            average_speed=50.0,  # Outside LUT range (max is 40)
            distance=10.0,
            temperature=30.0,  # Outside LUT range (max is 20)
            level_of_loading=0.5,
            incline=0.0,
        )

        # Calculate - should work with nearest neighbor (no warning expected)
        info.calculate()

        # Should still get a valid result from nearest neighbor
        assert info.consumption is not None
        assert info.consumption > 0
        assert info.consumption_per_km is not None

    def test_consumption_information_generate_result(self):
        """Test generating a ConsumptionResult from ConsumptionInformation."""
        info = ConsumptionInformation(
            trip_id=1,
            consumption_lut=None,
            average_speed=20.0,
            distance=10.0,
            temperature=10.0,
            level_of_loading=0.5,
            consumption=15.0,  # Pre-calculated
            consumption_per_km=1.5,
        )

        battery_capacity = 100.0
        result = info.generate_consumption_result(battery_capacity)

        assert isinstance(result, ConsumptionResult)
        assert result.delta_soc_total == -15.0 / 100.0  # -0.15
        assert result.timestamps is None  # Not implemented yet
        assert result.delta_soc is None  # Not implemented yet

    def test_consumption_information_generate_result_without_calculation(self):
        """Test that generating result without calculation raises error."""
        info = ConsumptionInformation(
            trip_id=1,
            consumption_lut=None,
            average_speed=20.0,
            distance=10.0,
            temperature=10.0,
            level_of_loading=0.5,
            consumption=None,  # Not calculated
            consumption_per_km=None,
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
        # Modify the LUT to have wrong columns
        consumption_lut.columns = ["wrong", "columns"]

        info = ConsumptionInformation(
            trip_id=1,
            consumption_lut=consumption_lut,
            average_speed=20.0,
            distance=10.0,
            temperature=10.0,
            level_of_loading=0.5,
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

        with pytest.raises(ValueError, match="Expected exactly one consumption LUT"):
            extract_trip_information(trip_with_lut.id, scenario)

    def test_no_lut_no_consumption_value_error(
        self, session, scenario, trip_without_lut
    ):
        """Test that having no LUT and no consumption value raises an error."""
        # Unset the consumption value
        trip_without_lut.rotation.vehicle_type.consumption = None
        session.flush()

        with pytest.warns(ConsistencyWarning, match="No consumption LUT found"):
            with pytest.raises(
                TypeError,
                match="unsupported operand type",
            ):
                extract_trip_information(trip_without_lut.id, scenario)
