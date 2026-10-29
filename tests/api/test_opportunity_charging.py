import math
from datetime import datetime, timedelta, timezone

import pytest
from eflips.model import (
    ChargeType,
    Event,
    EventType,
    Line,
    Rotation,
    Route,
    Station,
    Trip,
    TripType,
    Vehicle,
    VehicleType,
    VoltageLevel,
)
from geoalchemy2.shape import from_shape
from shapely import Point

from eflips.depot.api.private.consumption import (
    _integrate_opportunity_charge,
    attempt_opportunity_charging_event,
)
from tests.api.test_api import TestHelpers

# A curve that is flat up to 80 % SoC and then tapers linearly from 200 kW to 50 kW. Between 0.8 and 1.0 the power is
# P(soc) = 200 - 750 * (soc - 0.8), which allows analytic reference values.
TAPERED_CURVE = [[0.0, 200.0], [0.8, 200.0], [1.0, 50.0]]
CAPACITY = 100.0


def _tapered_full_charge_seconds(start_soc: float) -> float:
    """Analytic time to charge from ``start_soc`` (< 0.8) to 1.0 on ``TAPERED_CURVE``."""
    flat_part = (0.8 - start_soc) * CAPACITY / 200
    taper_part = CAPACITY / 750 * math.log(200 / 50)
    return (flat_part + taper_part) * 3600


class TestIntegrateOpportunityCharge:
    def test_charger_power_caps_curve(self):
        duration, soc, _ = _integrate_opportunity_charge(
            [[0, 300], [1, 300]], CAPACITY, 0.2, charger_power=150, min_power=0
        )
        assert soc == 1.0
        assert duration.total_seconds() == pytest.approx(80 / 150 * 3600, abs=1)

        uncapped_duration, _, _ = _integrate_opportunity_charge(
            [[0, 300], [1, 300]], CAPACITY, 0.2, charger_power=None, min_power=0
        )
        assert uncapped_duration.total_seconds() == pytest.approx(
            80 / 300 * 3600, abs=1
        )

    def test_taper_matches_analytic_integral(self):
        duration, soc, _ = _integrate_opportunity_charge(
            TAPERED_CURVE, CAPACITY, 0.2, charger_power=None, min_power=0
        )
        assert soc == 1.0
        assert duration.total_seconds() == pytest.approx(
            _tapered_full_charge_seconds(0.2), abs=2
        )

        # The old flat-peak estimate would have been considerably shorter
        flat_peak_seconds = 0.8 * CAPACITY / 200 * 3600
        assert duration.total_seconds() > flat_peak_seconds + 60

    def test_timestep_convergence(self):
        fine = _integrate_opportunity_charge(
            TAPERED_CURVE,
            CAPACITY,
            0.2,
            None,
            0,
            timestep=timedelta(seconds=1),
        )
        coarse = _integrate_opportunity_charge(
            TAPERED_CURVE,
            CAPACITY,
            0.2,
            None,
            0,
            timestep=timedelta(seconds=10),
        )
        assert coarse[0].total_seconds() == pytest.approx(
            fine[0].total_seconds(), abs=10
        )
        assert coarse[1] == pytest.approx(fine[1], abs=1e-3)

    def test_minimum_power_cutoff(self):
        # P drops below 125 kW at SoC 0.9
        duration, soc, _ = _integrate_opportunity_charge(
            TAPERED_CURVE, CAPACITY, 0.2, charger_power=None, min_power=125
        )
        assert soc == pytest.approx(0.9, abs=1e-3)
        expected = (0.6 * CAPACITY / 200 + CAPACITY / 750 * math.log(200 / 125)) * 3600
        assert duration.total_seconds() == pytest.approx(expected, abs=2)

    def test_below_minimum_from_start(self):
        duration, soc, points = _integrate_opportunity_charge(
            TAPERED_CURVE, CAPACITY, 0.95, charger_power=None, min_power=125
        )
        assert duration == timedelta(0)
        assert soc == 0.95
        assert points == [(timedelta(0), 0.95)]

    def test_full_battery(self):
        duration, soc, _ = _integrate_opportunity_charge(
            TAPERED_CURVE, CAPACITY, 1.0, charger_power=None, min_power=0
        )
        assert duration == timedelta(0)
        assert soc == 1.0

    def test_max_duration_truncates(self):
        duration, soc, _ = _integrate_opportunity_charge(
            TAPERED_CURVE,
            CAPACITY,
            0.2,
            charger_power=None,
            min_power=0,
            max_duration=timedelta(minutes=9),
        )
        assert duration == timedelta(minutes=9)
        assert soc == pytest.approx(0.2 + 200 * 0.15 / CAPACITY, abs=1e-9)

    def test_profile_points(self):
        duration, soc, points = _integrate_opportunity_charge(
            TAPERED_CURVE, CAPACITY, 0.2, charger_power=None, min_power=0
        )
        offsets = [offset for offset, _ in points]
        socs = [point_soc for _, point_soc in points]
        assert offsets[0] == timedelta(0)
        assert offsets[-1] == duration
        assert socs[0] == 0.2
        assert socs[-1] == soc
        assert offsets == sorted(offsets)
        assert socs == sorted(socs)
        assert all(
            later - earlier <= timedelta(seconds=60)
            for earlier, later in zip(offsets, offsets[1:])
        )
        # Downsampled, not one point per integration step
        assert len(points) < duration.total_seconds() / 30


class TestAttemptOpportunityChargingEvent(TestHelpers):
    ARRIVAL = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    DEADTIME = timedelta(minutes=6)

    @pytest.fixture()
    def make_stop(self, session, scenario):
        """
        Return a factory that builds two consecutive trips with a break at an opportunity charging terminus.

        :return: A callable returning ``(previous_trip, next_trip, vehicle)``.
        """

        def _make_stop(charging_curve, charger_power, break_time, min_power=0.0):
            vehicle_type = VehicleType(
                scenario=scenario,
                name="Opportunity Vehicle Type",
                battery_capacity=CAPACITY,
                charging_curve=charging_curve,
                minimum_charging_power=min_power,
                opportunity_charging_capable=True,
            )
            depot_station = Station(
                scenario=scenario,
                name="Depot Station",
                name_short="DS",
                geom=from_shape(Point(0, 0, 0), srid=4326),
                is_electrified=False,
            )
            terminus = Station(
                scenario=scenario,
                name="Terminus",
                name_short="TER",
                geom=from_shape(Point(1, 0, 0), srid=4326),
                is_electrified=True,
                amount_charging_places=10,
                power_per_charger=charger_power,
                power_total=10 * charger_power,
                charge_type=ChargeType.OPPORTUNITY,
                voltage_level=VoltageLevel.MV,
            )
            line = Line(scenario=scenario, name="Line", name_short="L")
            route_out = Route(
                scenario=scenario,
                name="Out",
                departure_station=depot_station,
                arrival_station=terminus,
                line=line,
                distance=1000,
            )
            route_back = Route(
                scenario=scenario,
                name="Back",
                departure_station=terminus,
                arrival_station=depot_station,
                line=line,
                distance=1000,
            )
            rotation = Rotation(
                scenario=scenario,
                vehicle_type=vehicle_type,
                allow_opportunity_charging=True,
            )
            previous_trip = Trip(
                scenario=scenario,
                route=route_out,
                rotation=rotation,
                trip_type=TripType.PASSENGER,
                departure_time=self.ARRIVAL - timedelta(minutes=20),
                arrival_time=self.ARRIVAL,
            )
            next_trip = Trip(
                scenario=scenario,
                route=route_back,
                rotation=rotation,
                trip_type=TripType.PASSENGER,
                departure_time=self.ARRIVAL + break_time,
                arrival_time=self.ARRIVAL + break_time + timedelta(minutes=20),
            )
            vehicle = Vehicle(
                scenario=scenario, vehicle_type=vehicle_type, name="Vehicle"
            )
            session.add_all([rotation, previous_trip, next_trip, vehicle])
            session.flush()
            return previous_trip, next_trip, vehicle

        return _make_stop

    @staticmethod
    def _events(session, vehicle):
        return (
            session.query(Event)
            .filter(Event.vehicle_id == vehicle.id)
            .order_by(Event.time_start)
            .all()
        )

    def _assert_events_fill_break(self, events, previous_trip, next_trip):
        assert events[0].time_start == previous_trip.arrival_time
        assert events[-1].time_end == next_trip.departure_time
        for earlier, later in zip(events, events[1:]):
            assert earlier.time_end == later.time_start
            assert earlier.soc_end == later.soc_start
        for event in events:
            assert event.time_start.microsecond == 0
            assert event.time_end.microsecond == 0

    @staticmethod
    def _assert_timeseries_consistent(event):
        times = [datetime.fromisoformat(t) for t in event.timeseries["time"]]
        socs = event.timeseries["soc"]
        assert times[0] == event.time_start
        assert times[-1] == event.time_end
        assert times == sorted(times)
        assert socs == sorted(socs)
        assert socs[0] == event.soc_start
        assert socs[-1] == event.soc_end

    def test_charger_power_limits_charging(self, session, make_stop):
        previous_trip, next_trip, vehicle = make_stop(
            [[0, 300], [1, 300]], charger_power=150, break_time=timedelta(minutes=60)
        )
        soc = attempt_opportunity_charging_event(
            previous_trip, next_trip, vehicle, 0.2, self.DEADTIME, session
        )
        session.flush()

        assert soc == 1.0
        events = self._events(session, vehicle)
        self._assert_events_fill_break(events, previous_trip, next_trip)
        (charging,) = [
            e for e in events if e.event_type == EventType.CHARGING_OPPORTUNITY
        ]
        # 80 kWh at 150 kW = 32 min, plus the deadtime
        assert (charging.time_end - charging.time_start).total_seconds() == (
            pytest.approx((32 * 60) + self.DEADTIME.total_seconds(), abs=1)
        )
        self._assert_timeseries_consistent(charging)

    def test_taper_lengthens_charging(self, session, make_stop):
        previous_trip, next_trip, vehicle = make_stop(
            TAPERED_CURVE, charger_power=500, break_time=timedelta(minutes=60)
        )
        soc = attempt_opportunity_charging_event(
            previous_trip, next_trip, vehicle, 0.2, self.DEADTIME, session
        )
        session.flush()

        assert soc == 1.0
        (charging,) = [
            e
            for e in self._events(session, vehicle)
            if e.event_type == EventType.CHARGING_OPPORTUNITY
        ]
        assert (charging.time_end - charging.time_start).total_seconds() == (
            pytest.approx(
                _tapered_full_charge_seconds(0.2) + self.DEADTIME.total_seconds(),
                abs=2,
            )
        )
        self._assert_timeseries_consistent(charging)

    def test_minimum_power_cutoff(self, session, make_stop):
        previous_trip, next_trip, vehicle = make_stop(
            TAPERED_CURVE,
            charger_power=500,
            break_time=timedelta(minutes=60),
            min_power=125,
        )
        soc = attempt_opportunity_charging_event(
            previous_trip, next_trip, vehicle, 0.2, self.DEADTIME, session
        )
        session.flush()

        assert soc == pytest.approx(0.9, abs=1e-3)
        events = self._events(session, vehicle)
        self._assert_events_fill_break(events, previous_trip, next_trip)
        (charging,) = [
            e for e in events if e.event_type == EventType.CHARGING_OPPORTUNITY
        ]
        assert charging.soc_end == soc
        assert charging.time_end - charging.time_start < next_trip.departure_time - (
            previous_trip.arrival_time
        )
        (departure,) = [
            e for e in events if e.event_type == EventType.STANDBY_DEPARTURE
        ]
        assert departure.soc_start == departure.soc_end == soc
        self._assert_timeseries_consistent(charging)

    def test_short_stop_truncates_charging(self, session, make_stop):
        previous_trip, next_trip, vehicle = make_stop(
            [[0, 150], [1, 150]], charger_power=150, break_time=timedelta(minutes=15)
        )
        soc = attempt_opportunity_charging_event(
            previous_trip, next_trip, vehicle, 0.2, self.DEADTIME, session
        )
        session.flush()

        # 9 minutes of charging at 150 kW
        assert soc == pytest.approx(0.2 + 150 * 0.15 / CAPACITY)
        events = self._events(session, vehicle)
        assert len(events) == 1
        (charging,) = events
        assert charging.event_type == EventType.CHARGING_OPPORTUNITY
        assert charging.time_start == previous_trip.arrival_time
        assert charging.time_end == next_trip.departure_time
        assert charging.soc_end == soc
        self._assert_timeseries_consistent(charging)

    def test_no_event_when_power_below_minimum(self, session, make_stop):
        previous_trip, next_trip, vehicle = make_stop(
            TAPERED_CURVE,
            charger_power=500,
            break_time=timedelta(minutes=60),
            min_power=125,
        )
        soc = attempt_opportunity_charging_event(
            previous_trip, next_trip, vehicle, 0.95, self.DEADTIME, session
        )
        session.flush()

        assert soc == 0.95
        assert self._events(session, vehicle) == []
