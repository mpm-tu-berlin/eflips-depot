import logging
import warnings
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta, datetime
from math import ceil
from typing import TYPE_CHECKING, Any, DefaultDict, Dict, Tuple, List, Optional

if TYPE_CHECKING:
    import scipy.interpolate
from zoneinfo import ZoneInfo

import numpy as np
import scipy
import sqlalchemy.orm
from eflips.model import (
    AssocRouteStation,
    Event,
    EventType,
    Rotation,
    Route,
    Vehicle,
    VehicleType,
    VehicleClass,
    Trip,
    Station,
    StopTime,
    ChargeType,
    ConsistencyWarning,
    ConsumptionLut,
    Scenario,
    Temperatures,
)
from geoalchemy2.shape import to_shape
from sqlalchemy.orm import joinedload, selectinload

from eflips.depot.api.private.util import temperature_for_trip, create_session

# Module-level cache for parsed interpolators, keyed by ConsumptionLut.id.
# Avoids rebuilding the 4D numpy array and scipy RegularGridInterpolator
# for every trip when many trips share the same vehicle class (same LUT).
_interpolator_cache: Dict[int, Dict[str, Any]] = {}
_nn_interpolator_cache: "Dict[int, scipy.interpolate.NearestNDInterpolator]" = {}

# Dedup set for nearest-neighbor fallback warnings.
# Key: (lut_id, kind, dim_tuple). ``kind`` is "out_of_range" or "ragged"; for
# out_of_range the dim_tuple lists the offending LUT axes (sorted).
_nn_fallback_warned: set = set()


def clear_interpolator_cache() -> None:
    """Clear the module-level interpolator cache."""
    _interpolator_cache.clear()
    _nn_interpolator_cache.clear()
    _nn_fallback_warned.clear()


_LUT_DIM_NAMES: Tuple[str, str, str, str] = (
    "incline",
    "t_amb",
    "level_of_loading",
    "mean_speed_kmh",
)


def _classify_nan_query(
    point: np.ndarray, cached: Dict[str, Any]
) -> Tuple[str, Tuple[str, ...]]:
    """
    Classify a 4D query that produced NaN under the regular-grid interpolator.

    Returns ``("out_of_range", (dim_names...))`` if any axis value is outside
    its scale, else ``("ragged", ())`` — meaning the query lies inside the
    bounding box but a neighbouring grid cell is unpopulated.
    """
    scales = (
        cached["incline_scale"],
        cached["temperature_scale"],
        cached["level_of_loading_scale"],
        cached["speed_scale"],
    )
    out_of_range: List[str] = []
    for i, scale in enumerate(scales):
        v = float(point[i])
        if v < scale[0] or v > scale[-1]:
            out_of_range.append(_LUT_DIM_NAMES[i])
    if out_of_range:
        return "out_of_range", tuple(sorted(out_of_range))
    return "ragged", ()


def _get_or_build_interpolator(consumption_lut: ConsumptionLut) -> Dict[str, Any]:
    """
    Build or retrieve a cached RegularGridInterpolator for a ConsumptionLut.

    Returns a dict with keys: 'interpolator', 'consumption_array', 'incline_scale',
    'temperature_scale', 'level_of_loading_scale', 'speed_scale'.
    """
    lut_id = consumption_lut.id
    if lut_id in _interpolator_cache:
        return _interpolator_cache[lut_id]

    # Validate columns
    if not all(
        col in consumption_lut.columns
        for col in [
            "incline",
            "t_amb",
            "level_of_loading",
            "mean_speed_kmh",
        ]
    ):
        raise ValueError(
            "The consumption LUT must have the columns 'incline', 't_amb', 'level_of_loading', 'mean_speed_kmh'"
        )

    # Recover the scales along each of the four axes from the datapoints
    incline_col_index = consumption_lut.columns.index("incline")
    temperature_col_index = consumption_lut.columns.index("t_amb")
    level_of_loading_col_index = consumption_lut.columns.index("level_of_loading")
    speed_col_index = consumption_lut.columns.index("mean_speed_kmh")

    incline_scale = sorted(
        set([x[incline_col_index] for x in consumption_lut.data_points])
    )
    temperature_scale = sorted(
        set([x[temperature_col_index] for x in consumption_lut.data_points])
    )
    level_of_loading_scale = sorted(
        set([x[level_of_loading_col_index] for x in consumption_lut.data_points])
    )
    speed_scale = sorted(set([x[speed_col_index] for x in consumption_lut.data_points]))

    # Create and populate the 4D array
    consumption_array = np.zeros(
        (
            len(incline_scale),
            len(temperature_scale),
            len(level_of_loading_scale),
            len(speed_scale),
        )
    )
    consumption_array.fill(np.nan)

    for i, data_point in enumerate(consumption_lut.data_points):
        incline = data_point[incline_col_index]
        temperature = data_point[temperature_col_index]
        level_of_loading = data_point[level_of_loading_col_index]
        speed = data_point[speed_col_index]
        consumption_array[
            incline_scale.index(incline),
            temperature_scale.index(temperature),
            level_of_loading_scale.index(level_of_loading),
            speed_scale.index(speed),
        ] = consumption_lut.values[i]

    # Build the interpolator. ``fill_value=np.nan`` disables linear extrapolation
    # outside the grid; any out-of-range query produces NaN and is routed to the
    # nearest-neighbor fallback in :meth:`ConsumptionInformation.calculate`.
    interpolator = scipy.interpolate.RegularGridInterpolator(
        (incline_scale, temperature_scale, level_of_loading_scale, speed_scale),
        consumption_array,
        bounds_error=False,
        fill_value=np.nan,
        method="linear",
    )

    result = {
        "interpolator": interpolator,
        "consumption_array": consumption_array,
        "incline_scale": incline_scale,
        "temperature_scale": temperature_scale,
        "level_of_loading_scale": level_of_loading_scale,
        "speed_scale": speed_scale,
    }
    _interpolator_cache[lut_id] = result
    return result


def _get_or_build_nearest_neighbor_interpolator(
    cached: Dict[str, Any], lut_id: int
) -> "scipy.interpolate.NearestNDInterpolator":
    """
    Build or retrieve a cached NearestNDInterpolator for a ConsumptionLut.

    This is used as a fallback when the RegularGridInterpolator returns NaN.
    """
    if lut_id in _nn_interpolator_cache:
        return _nn_interpolator_cache[lut_id]

    x, y, z, alpha = np.meshgrid(
        cached["incline_scale"],
        cached["temperature_scale"],
        cached["level_of_loading_scale"],
        cached["speed_scale"],
        indexing="ij",
    )
    points_array = np.column_stack([x.ravel(), y.ravel(), z.ravel(), alpha.ravel()])
    consumption_array_flattened = cached["consumption_array"].ravel()

    # Remove the NaN entries from consumption_array and points_array
    valid_mask = ~np.isnan(consumption_array_flattened)
    points_array = points_array[valid_mask]
    valid_values = consumption_array_flattened[valid_mask]

    interpolator_nn = scipy.interpolate.NearestNDInterpolator(
        x=points_array,
        y=valid_values,
    )
    _nn_interpolator_cache[lut_id] = interpolator_nn
    return interpolator_nn


@dataclass
class ConsumptionResult:
    """
    A dataclass that stores the results of a charging simulation for a single trip.

    This class holds both the total change in battery State of Charge (SoC) over the trip
    as well as an optional timeseries of timestamps and incremental SoC changes. When
    an entry exists for a given trip in ``consumption_result``, the simulation will use
    these precomputed values instead of recalculating the SoC changes from the vehicle
    distance and consumption.

    :param delta_soc_total:
        The total change in the vehicle's State of Charge over the trip, typically
        negative if the vehicle is consuming energy (e.g., -0.15 means the SoC
        dropped by 15%).

    :param timestamps:
        A list of timestamps (e.g., arrival times at stops) that mark the times
        associated with the SoC changes. The number of timestamps must match the
        number of entries in ``delta_soc``.

    :param delta_soc:
        A list of cumulative SoC changes corresponding to the ``timestamps``.
        For example, if ``delta_soc[i] = -0.02``, it means the SoC decreased by 2%
        between from the start of the trip to ``timestamps[i]``. This list should typically
        be a monotonic decreasing sequence.
    """

    delta_soc_total: float
    timestamps: List[datetime] | None
    delta_soc: List[float] | None


@dataclass
class TripSegment:
    """
    A piece of a trip between two adjacent knots — either consecutive.

    :class:`StopTime` boundaries, or synthetic knots inserted from
    ``Route.geom`` vertices to capture intermediate elevation changes.

    Each segment is the unit at which the consumption LUT is evaluated.
    """

    distance_m: float
    """2D ground distance in meters."""

    duration_s: float
    """Duration of this segment in seconds."""

    mean_speed_kmh: float
    """Mean speed in km/h."""

    incline: float
    """Signed Δz / distance_m.

    0.0 if either knot lacks an elevation.
    """

    level_of_loading: Optional[float]
    """Vehicle loading as a fraction of max payload, or ``None`` when no LUT is in use."""

    t_amb: Optional[float]
    """Ambient temperature in °C at the segment midpoint, or ``None`` when no temperature data is available."""

    end_time: datetime
    """Absolute timestamp at the end of this segment."""

    consumption_kwh: Optional[float] = None
    """Energy used on this segment in kWh; populated by :meth:`ConsumptionInformation.calculate`."""


@dataclass
class ConsumptionInformation:
    """
    Per-trip consumption inputs decomposed into route-aware segments.

    Either ``consumption_lut`` or ``flat_consumption_per_km`` must be set.
    Calling :meth:`calculate` populates ``segment.consumption_kwh`` for every
    segment; :meth:`generate_consumption_result` then turns that into a
    :class:`ConsumptionResult` with cumulative SoC.
    """

    trip_id: int
    segments: List["TripSegment"]
    consumption_lut: Optional[ConsumptionLut] = None
    flat_consumption_per_km: Optional[float] = None
    line_name: Optional[str] = None
    """Human-readable line name, used purely for diagnostic warnings."""
    route_name: Optional[str] = None
    """Human-readable route name, used purely for diagnostic warnings."""
    trip_departure: Optional[datetime] = None
    """Trip departure time, used purely for diagnostic warnings."""
    trip_arrival: Optional[datetime] = None
    """Trip arrival time, used purely for diagnostic warnings."""

    def calculate(self) -> None:
        """
        Compute energy consumption for every segment.

        - When ``consumption_lut`` is set, the LUT is evaluated once for all
          segments via a vectorized :class:`RegularGridInterpolator` call. Any
          segment whose result is NaN is filled in via a
          :class:`NearestNDInterpolator` fallback (a single warning is emitted).
        - When only ``flat_consumption_per_km`` is set, each segment's energy
          is just ``flat_consumption_per_km * distance_m / 1000``.

        The LUT reference is dropped after evaluation to avoid pinning the
        whole table in memory.
        """

        if not self.segments:
            raise ValueError(
                f"ConsumptionInformation for trip {self.trip_id} has no segments."
            )

        if self.consumption_lut is None and self.flat_consumption_per_km is None:
            raise ValueError(
                f"ConsumptionInformation for trip {self.trip_id} has neither a "
                "consumption_lut nor a flat_consumption_per_km."
            )

        if self.consumption_lut is None:
            for segment in self.segments:
                segment.consumption_kwh = (
                    self.flat_consumption_per_km * segment.distance_m / 1000.0
                )
            return

        cached = _get_or_build_interpolator(self.consumption_lut)
        interpolator = cached["interpolator"]

        points = np.array(
            [
                [s.incline, s.t_amb, s.level_of_loading, s.mean_speed_kmh]
                for s in self.segments
            ],
            dtype=float,
        )
        kwh_per_km = np.asarray(interpolator(points), dtype=float)

        nan_mask = np.isnan(kwh_per_km)
        if nan_mask.any():
            lut_id = self.consumption_lut.id
            self._warn_nn_fallback(points, nan_mask, cached, lut_id)
            interpolator_nn = _get_or_build_nearest_neighbor_interpolator(
                cached, lut_id
            )
            kwh_per_km[nan_mask] = np.asarray(
                interpolator_nn(points[nan_mask]), dtype=float
            )

        if np.isnan(kwh_per_km).any():
            raise ValueError(
                f"Could not calculate consumption for trip {self.trip_id}. "
                "Possible reason: data points missing in the LUT."
            )

        for i, segment in enumerate(self.segments):
            segment.consumption_kwh = float(kwh_per_km[i]) * segment.distance_m / 1000.0

        self.consumption_lut = None  # release the LUT reference

    def _warn_nn_fallback(
        self,
        points: np.ndarray,
        nan_mask: np.ndarray,
        cached: Dict[str, Any],
        lut_id: int,
    ) -> None:
        """
        Emit a deduplicated :class:`ConsistencyWarning` for every distinct
        nearest-neighbor fallback reason encountered on this trip.

        Each NaN query is classified as either ``out_of_range`` (at least one
        LUT axis outside its scale; the offending axes are listed) or
        ``ragged`` (all four axes in-range, but a neighbouring grid cell is
        unpopulated). Warnings are deduplicated by
        ``(lut_id, kind, dim_tuple)`` across the whole process.
        """
        nan_indices = np.flatnonzero(nan_mask)
        groups: Dict[Tuple[str, Tuple[str, ...]], List[int]] = {}
        for idx in nan_indices:
            kind, dims = _classify_nan_query(points[idx], cached)
            groups.setdefault((kind, dims), []).append(int(idx))

        ctx_parts: List[str] = []
        if self.line_name:
            ctx_parts.append(f"line={self.line_name!r}")
        if self.route_name:
            ctx_parts.append(f"route={self.route_name!r}")
        if self.trip_departure is not None:
            ctx_parts.append(f"departure={self.trip_departure.isoformat()}")
        if self.trip_arrival is not None:
            ctx_parts.append(f"arrival={self.trip_arrival.isoformat()}")
        ctx_suffix = (
            (" Trip context: " + ", ".join(ctx_parts) + ".") if ctx_parts else ""
        )

        for (kind, dims), idxs in groups.items():
            key = (lut_id, kind, dims)
            if key in _nn_fallback_warned:
                continue
            _nn_fallback_warned.add(key)

            example_seg = self.segments[idxs[0]]
            example = (
                f" Example segment: incline={example_seg.incline:.4f}, "
                f"t_amb={example_seg.t_amb}, "
                f"level_of_loading={example_seg.level_of_loading}, "
                f"mean_speed_kmh={example_seg.mean_speed_kmh:.2f}."
            )

            if kind == "out_of_range":
                msg = (
                    f"Consumption LUT {lut_id}: {len(idxs)} segment(s) on "
                    f"trip {self.trip_id} were outside the LUT grid on "
                    f"dimension(s) {list(dims)}. Falling back to "
                    f"nearest-neighbor interpolation; the result may be less "
                    f"accurate." + ctx_suffix + example
                )
            else:
                msg = (
                    f"Consumption LUT {lut_id}: {len(idxs)} segment(s) on "
                    f"trip {self.trip_id} fell into an unpopulated cell of "
                    f"the 4D grid (ragged grid; all axes in range). Falling "
                    f"back to nearest-neighbor interpolation; the result may "
                    f"be less accurate." + ctx_suffix + example
                )
            warnings.warn(msg, ConsistencyWarning)

    def generate_consumption_result(self, battery_capacity: float) -> ConsumptionResult:
        """
        Build a :class:`ConsumptionResult` from per-segment consumption_kwh values.

        ``timestamps`` matches ``[s.end_time for s in segments]`` and ``delta_soc``
        is the cumulative SoC drop at the end of each segment.
        """
        if any(s.consumption_kwh is None for s in self.segments):
            raise ValueError(
                "Consumption must be calculated before generating a result."
            )

        per_segment_kwh = np.array(
            [s.consumption_kwh for s in self.segments], dtype=float
        )
        delta_soc = (-np.cumsum(per_segment_kwh) / battery_capacity).tolist()
        timestamps = [s.end_time for s in self.segments]

        return ConsumptionResult(
            delta_soc_total=delta_soc[-1] if delta_soc else 0.0,
            timestamps=timestamps,
            delta_soc=delta_soc,
        )


_EARTH_RADIUS_M = 6_371_008.8
_VERTEX_GAP_THRESHOLD_M = 1_000.0


def _haversine_cumulative(coords: np.ndarray) -> np.ndarray:
    """
    Cumulative haversine distance in meters along a polyline.

    ``coords`` is an ``(N, 2+)`` array of (lon, lat[, z]) in degrees. Z is
    ignored — :func:`Route.calculate_length` (PostGIS ``ST_Length(..., true)``)
    also ignores Z, so the route's stored ``distance`` is purely 2D and we want
    to match that for elapsed_distance bookkeeping.

    Returns an ``(N,)`` array, with index 0 always 0.
    """
    if len(coords) < 2:
        return np.zeros(len(coords))

    lon = np.radians(coords[:, 0].astype(float))
    lat = np.radians(coords[:, 1].astype(float))
    dlon = np.diff(lon)
    dlat = np.diff(lat)
    a = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2.0) ** 2
    )
    seg = 2.0 * _EARTH_RADIUS_M * np.arcsin(np.sqrt(a))
    out = np.empty(len(coords))
    out[0] = 0.0
    out[1:] = np.cumsum(seg)
    return out


def _route_geom_knots(route: Route) -> Optional[np.ndarray]:
    """
    Return the route's geometry as an ``(N, 4)`` array of ``[elapsed_distance_m, lon, lat, z]``.

    The cumulative haversine distance is rescaled so that the last vertex sits at
    ``route.distance``. Returns ``None`` when the route has no geometry or fewer
    than three vertices (only the endpoints — no useful intermediate Z to add).
    """
    if route.geom is None:
        return None
    line = to_shape(route.geom)
    raw = np.array(line.coords, dtype=float)  # (N, 2 or 3)
    if raw.shape[0] < 3:
        return None
    if raw.shape[1] < 3:
        coords = np.column_stack([raw[:, 0], raw[:, 1], np.zeros(raw.shape[0])])
    else:
        coords = raw[:, :3]
    cum = _haversine_cumulative(coords)
    if cum[-1] > 0:
        cum = cum * (route.distance / cum[-1])
    return np.column_stack([cum, coords])


def _station_z(station: Station) -> Optional[float]:
    """Return the Z coordinate of a station's geom, or ``None`` if absent."""
    if station is None or station.geom is None:
        return None
    pt = to_shape(station.geom)
    if not getattr(pt, "has_z", False):
        return None
    return float(pt.z)


def _assoc_z(assoc: Optional[AssocRouteStation]) -> Optional[float]:
    """Return the Z coordinate of an AssocRouteStation.location, or ``None`` if absent."""
    if assoc is None or assoc.location is None:
        return None
    pt = to_shape(assoc.location)
    if not getattr(pt, "has_z", False):
        return None
    return float(pt.z)


def _build_trip_segments(
    trip: Trip,
    level_of_loading: Optional[float],
    t_amb: Optional[float],
) -> List[TripSegment]:
    """
    Walk a trip's stop times and route geometry to produce :class:`TripSegment` objects.

    Knots come from :class:`StopTime` rows when present (otherwise from the trip's
    departure/arrival), with synthetic knots inserted from ``Route.geom`` vertices
    whenever two consecutive stop-time knots are more than
    ``_VERTEX_GAP_THRESHOLD_M`` apart. A single :class:`ConsistencyWarning` is
    emitted per trip when at least one knot has no Z. Ambient temperature is
    constant across all segments — sample it once at the trip midpoint upstream.
    """
    route = trip.route
    # On circular routes (e.g. ZOB → … → ZOB) the same station appears more
    # than once in ``assoc_route_stations`` at different elapsed_distances.
    # Keep every occurrence so we can pick the right one per StopTime; a flat
    # ``station_id -> AssocRouteStation`` dict would collapse duplicates to
    # the last occurrence and assign return-leg distances to early stops,
    # producing zero-distance/zero-consumption segments at the trip start.
    assocs_by_station: DefaultDict[int, List[AssocRouteStation]] = defaultdict(list)
    for a in route.assoc_route_stations:
        assocs_by_station[a.station_id].append(a)

    trip_duration_s = (trip.arrival_time - trip.departure_time).total_seconds()
    route_distance_m = float(route.distance)

    def _pick_assoc(
        candidates: List[AssocRouteStation], stop_time: datetime
    ) -> AssocRouteStation:
        """Choose the occurrence whose elapsed_distance best matches the
        time-fraction of ``stop_time`` along the trip. Falls back to the first
        candidate when the trip has zero duration."""
        if len(candidates) == 1 or trip_duration_s <= 0:
            return candidates[0]
        t_s = (stop_time - trip.departure_time).total_seconds()
        expected_m = (t_s / trip_duration_s) * route_distance_m
        return min(
            candidates, key=lambda a: abs(float(a.elapsed_distance) - expected_m)
        )

    # 1. Build the stop-time-derived knot list as (elapsed_distance_m, time, z).
    stop_times = sorted(trip.stop_times, key=lambda st: st.arrival_time)
    knots: List[Tuple[float, datetime, Optional[float]]] = []
    if stop_times:
        for st in stop_times:
            candidates = assocs_by_station.get(st.station_id)
            if not candidates:
                raise ValueError(
                    f"StopTime {st.id} references station {st.station_id} which is "
                    f"not in route {route.id}'s assoc_route_stations."
                )
            assoc = _pick_assoc(candidates, st.arrival_time)
            z = _assoc_z(assoc)
            if z is None:
                z = _station_z(st.station)
            knots.append((float(assoc.elapsed_distance), st.arrival_time, z))
    else:
        # On a circular route departure_station_id == arrival_station_id; take
        # the first occurrence as departure (elapsed_distance == 0) and the
        # last as arrival (elapsed_distance == route.distance).
        dep_candidates = assocs_by_station.get(route.departure_station_id, [])
        arr_candidates = assocs_by_station.get(route.arrival_station_id, [])
        dep_assoc = dep_candidates[0] if dep_candidates else None
        arr_assoc = arr_candidates[-1] if arr_candidates else None
        dep_z = _assoc_z(dep_assoc) if dep_assoc else None
        if dep_z is None:
            dep_z = _station_z(route.departure_station)
        arr_z = _assoc_z(arr_assoc) if arr_assoc else None
        if arr_z is None:
            arr_z = _station_z(route.arrival_station)
        knots.append((0.0, trip.departure_time, dep_z))
        knots.append((float(route.distance), trip.arrival_time, arr_z))

    # 2. Insert synthetic knots from route.geom when a gap > threshold.
    geom_knots = _route_geom_knots(route)
    if geom_knots is not None:
        densified: List[Tuple[float, datetime, Optional[float]]] = [knots[0]]
        for prev, curr in zip(knots, knots[1:]):
            d_prev, t_prev, _ = prev
            d_curr, t_curr, _ = curr
            if d_curr - d_prev > _VERTEX_GAP_THRESHOLD_M:
                mask = (geom_knots[:, 0] > d_prev) & (geom_knots[:, 0] < d_curr)
                gap_span = d_curr - d_prev
                for d, _lon, _lat, z in geom_knots[mask]:
                    if gap_span > 0:
                        frac = (d - d_prev) / gap_span
                    else:
                        frac = 0.0
                    t_synth = t_prev + (t_curr - t_prev) * float(frac)
                    densified.append((float(d), t_synth, float(z)))
            densified.append(curr)
        knots = densified

    # 3. Track missing-Z and resolve to floats for incline math.
    z_missing = any(k[2] is None for k in knots)
    if z_missing:
        warnings.warn(
            f"Trip {trip.id}: at least one knot lacks a Z coordinate; "
            "treating those segments as flat (incline=0).",
            ConsistencyWarning,
        )

    # 4. Build the segments. t_amb is constant per trip (sampled at the trip
    #    midpoint by the caller).
    segments: List[TripSegment] = []
    for prev, curr in zip(knots, knots[1:]):
        d_prev, t_prev, z_prev = prev
        d_curr, t_curr, z_curr = curr
        distance_m = max(0.0, d_curr - d_prev)
        duration_s = (t_curr - t_prev).total_seconds()
        if distance_m > 0 and duration_s > 0:
            mean_speed_kmh = distance_m / duration_s * 3.6
        else:
            mean_speed_kmh = 0.0
        if distance_m > 0 and z_prev is not None and z_curr is not None:
            incline = (z_curr - z_prev) / distance_m
        else:
            incline = 0.0
        segments.append(
            TripSegment(
                distance_m=distance_m,
                duration_s=duration_s,
                mean_speed_kmh=mean_speed_kmh,
                incline=incline,
                level_of_loading=level_of_loading,
                t_amb=t_amb,
                end_time=t_curr,
            )
        )
    return segments


def extract_trip_information(
    trip_id: int,
    scenario: Scenario,
    passenger_mass=68,
    passenger_count=17.6,
    *,
    temperatures: Optional[Temperatures] = None,
    consumption_luts: Optional[Dict[int, ConsumptionLut]] = None,
) -> ConsumptionInformation:
    """
    Build a :class:`ConsumptionInformation` for a trip, decomposed into segments.

    Segment knots come from the trip's :class:`StopTime` rows; route-vertex
    knots are inserted between stops more than 1 km apart so that intermediate
    elevation changes are captured. The returned object has already had
    :meth:`ConsumptionInformation.calculate` called on it.
    """

    with create_session(scenario) as (session, scenario):
        # Use selectinload for the *collections* (Route.assoc_route_stations,
        # Trip.stop_times, VehicleType.vehicle_classes) so they each become a
        # separate small query instead of multiplying out into a Cartesian
        # product on the main row. Many-to-one steps stay joinedload.
        trip = (
            session.query(Trip)
            .filter(Trip.id == trip_id)
            .options(
                joinedload(Trip.route).joinedload(Route.departure_station),
                joinedload(Trip.route).joinedload(Route.arrival_station),
                joinedload(Trip.route).joinedload(Route.line),
                joinedload(Trip.route)
                .selectinload(Route.assoc_route_stations)
                .joinedload(AssocRouteStation.station),
                selectinload(Trip.stop_times).joinedload(StopTime.station),
                joinedload(Trip.rotation)
                .joinedload(Rotation.vehicle_type)
                .selectinload(VehicleType.vehicle_classes),
            )
            .one()
        )

        # Resolve LUTs from the caller-provided dict when present; otherwise fall
        # back to lazy-loading the relationship. Preloading once per scenario
        # avoids re-decoding ConsumptionLut's large JSON columns (columns,
        # data_points, values) on every joined row of every trip.
        if consumption_luts is not None:
            all_consumption_luts = [
                consumption_luts[vc.id]
                for vc in trip.rotation.vehicle_type.vehicle_classes
                if vc.id in consumption_luts
            ]
        else:
            all_consumption_luts = [
                vc.consumption_lut
                for vc in trip.rotation.vehicle_type.vehicle_classes
                if vc.consumption_lut is not None
            ]

        if len(all_consumption_luts) > 1:
            raise ValueError(
                f"Expected at most one consumption LUT, got {len(all_consumption_luts)}"
            )

        line = getattr(trip.route, "line", None)
        line_name = getattr(line, "name", None) if line is not None else None
        route_name = trip.route.name

        # Sample ambient temperature once at the trip midpoint; constant per trip.
        trip_midpoint = (
            trip.departure_time + (trip.arrival_time - trip.departure_time) / 2
        )
        t_amb = temperature_for_trip(
            trip.id, session, at_time=trip_midpoint, temperatures=temperatures
        )

        if len(all_consumption_luts) == 1:
            assert (
                trip.rotation.vehicle_type.allowed_mass is not None
            ), f"allowed_mass of vehicle {trip.rotation.vehicle_type} must be set"
            assert (
                trip.rotation.vehicle_type.empty_mass is not None
            ), f"empty_mass of vehicle {trip.rotation.vehicle_type} must be set"

            full_payload = (
                trip.rotation.vehicle_type.allowed_mass
                - trip.rotation.vehicle_type.empty_mass
            )
            level_of_loading = (passenger_mass * passenger_count) / full_payload

            segments = _build_trip_segments(trip, level_of_loading, t_amb)
            info = ConsumptionInformation(
                trip_id=trip.id,
                segments=segments,
                consumption_lut=all_consumption_luts[0],
                line_name=line_name,
                route_name=route_name,
                trip_departure=trip.departure_time,
                trip_arrival=trip.arrival_time,
            )
            info.calculate()
        else:
            warnings.warn(
                f"No consumption LUT found for vehicle type {trip.rotation.vehicle_type}.",
                ConsistencyWarning,
            )
            if trip.rotation.vehicle_type.consumption is None:
                raise ValueError(
                    f"Vehicle type {trip.rotation.vehicle_type} must have a "
                    "consumption value set if no consumption LUT is available."
                )
            segments = _build_trip_segments(trip, level_of_loading=None, t_amb=t_amb)
            info = ConsumptionInformation(
                trip_id=trip.id,
                segments=segments,
                flat_consumption_per_km=trip.rotation.vehicle_type.consumption,
                line_name=line_name,
                route_name=route_name,
                trip_departure=trip.departure_time,
                trip_arrival=trip.arrival_time,
            )
            info.calculate()

    return info


def initialize_vehicle(rotation: Rotation, session: sqlalchemy.orm.session.Session):
    """
    Create and add a new Vehicle object in the database for the given rotation.

    This function:
      1. Creates a new ``Vehicle`` instance using the provided rotation’s
         vehicle type and scenario ID.
      2. Names it based on the rotation’s ID.
      3. Adds the vehicle to the specified SQLAlchemy session.
      4. Assigns the new vehicle to the rotation’s ``vehicle`` attribute.

    :param rotation:
        A :class:`Rotation` instance for which a new ``Vehicle`` should be created.
        The new vehicle will inherit its type and scenario from this rotation.

    :param session:
        An active SQLAlchemy :class:`Session` used to persist the new vehicle to
        the database. The vehicle is added to the session but not committed here.

    :return:
        ``None``. Changes are made to the session but are not committed yet.
    """
    vehicle = Vehicle(
        vehicle_type_id=rotation.vehicle_type_id,
        scenario_id=rotation.scenario_id,
        name=f"Vehicle for rotation {rotation.id}",
    )
    session.add(vehicle)
    rotation.vehicle = vehicle


def add_initial_standby_event(
    vehicle: Vehicle, session: sqlalchemy.orm.session.Session
) -> None:
    """
    Create and add a standby event immediately before the earliest trip of the given vehicle.

    This function:
      1. Gathers all rotations assigned to the vehicle, sorted by their first trip’s departure time.
      2. Identifies the earliest trip across those rotations.
      3. Fetches an appropriate :class:`Area` record from the database based on
         the vehicle's scenario and vehicle type (for depot and subloc capacity).
      4. Constructs a dummy standby event starting one second before the earliest trip’s
         departure time, ending at the trip’s departure time, with 100% SoC.
      5. Adds the event to the session without committing (the caller is responsible for commits).

    :param vehicle:
        A :class:`Vehicle` instance for which to add a new standby event.
        Must have associated rotations and trips.

    :param session:
        An active SQLAlchemy :class:`Session` used to persist the new event to
        the database. The event is added to the session but not committed here.

    :return:
        ``None``. A new event is added to the session for the earliest trip,
        but changes are not yet committed.
    """

    earliest_trip_q = (
        session.query(Trip)
        .join(Rotation)
        .filter(Rotation.vehicle == vehicle)
        .order_by(Trip.departure_time)
        .limit(1)
    )
    earliest_trip = earliest_trip_q.one_or_none()
    if earliest_trip is None:
        warnings.warn(
            f"No trips found for vehicle {vehicle.id}. Cannot add initial standby event.",
            ConsistencyWarning,
        )
        return

    standby_start = earliest_trip.departure_time - timedelta(seconds=1)
    standby_event = Event(
        scenario_id=vehicle.scenario_id,
        vehicle_type_id=vehicle.vehicle_type_id,
        vehicle=vehicle,
        station_id=earliest_trip.route.departure_station_id,
        subloc_no=0,
        time_start=standby_start,
        time_end=earliest_trip.departure_time,
        soc_start=1,
        soc_end=1,
        event_type=EventType.STANDBY_DEPARTURE,
        description=f"DUMMY Initial standby event for vehicle {vehicle.id}",
        timeseries=None,
    )
    session.add(standby_event)


def find_charger_occupancy(
    station: Station,
    time_start: datetime,
    time_end: datetime,
    session: sqlalchemy.orm.session.Session,
    resolution=timedelta(seconds=1),
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build a timeseries of charger occupancy at a station between two points in time.

    For each discrete timestep between ``time_start`` and ``time_end`` (at the given
    ``resolution``), this function calculates how many charging events (from the database)
    overlap with that time, thus producing a count of the active chargers at each timestep.

    :param station:
        The :class:`Station` whose charger occupancy is to be analyzed.
    :param time_start:
        The start time for the occupancy timeseries (inclusive).
    :param time_end:
        The end time for the occupancy timeseries (exclusive).
    :param session:
        An active SQLAlchemy :class:`Session` used to query the database.
    :param resolution:
        The timestep interval used to build the timeseries (default is 1 second).
        Note that using a very fine resolution over a large time range can
        produce large arrays.

    :returns:
        A tuple of two numpy arrays:
          1. ``times``: The array of discrete timesteps (shape: ``(n,)``).
          2. ``occupancy``: The array of integer occupancy values for each timestep
             (shape: ``(n,)``), indicating how many charging events are active.
    """
    # Load all charging events that could be relevant
    charging_events_q = session.query(Event).filter(
        Event.event_type == EventType.CHARGING_OPPORTUNITY,
        Event.station_id == station.id,
        Event.time_start < time_end,
        Event.time_end > time_start,
    )

    # We need to change the times to numpy datetime64 with implicit UTC timezone
    tz = ZoneInfo("UTC")
    time_start = np.datetime64(time_start.astimezone(tz).replace(tzinfo=None))
    time_end = np.datetime64(time_end.astimezone(tz).replace(tzinfo=None))

    times = np.arange(time_start, time_end, resolution)
    occupancy = np.zeros_like(times, dtype=int)
    for event in charging_events_q:
        event_start = np.datetime64(
            event.time_start.astimezone(tz).replace(tzinfo=None)
        )
        event_end = np.datetime64(event.time_end.astimezone(tz).replace(tzinfo=None))
        start_idx = np.argmax(times >= event_start)
        end_idx = np.argmax(times >= event_end)
        occupancy[start_idx:end_idx] += 1

    return times, occupancy


def find_best_timeslot(
    station: Station,
    time_start: datetime,
    time_end: datetime,
    charging_duration: timedelta,
    session: sqlalchemy.orm.session.Session,
    resolution: timedelta = timedelta(seconds=1),
) -> datetime:
    times, occupancy = find_charger_occupancy(
        station, time_start, time_end, session, resolution=resolution
    )

    total_span = times[-1] - times[0]
    if charging_duration - timedelta(seconds=1) > total_span:
        raise ValueError("The event duration exceeds the entire timeseries span.")

    ## AUTHOR: ChatGPT o-1
    # Step 1: Compute how many indices are needed to cover `event_duration`.
    steps_needed = int(charging_duration / resolution)
    if steps_needed == 0:
        raise ValueError("event_duration is too small for the timeseries resolution.")

    # Step 2: Build a prefix-sum array for occupancy
    prefix_sum = np.zeros(len(occupancy) + 1, dtype=float)
    for i in range(len(occupancy)):
        prefix_sum[i + 1] = prefix_sum[i] + occupancy[i]

    # Step 3: Slide over every possible start index, compute sum in O(1)
    best_start_idx = 0
    min_sum = float("inf")
    max_start_idx = len(occupancy) - steps_needed
    if max_start_idx < 0:
        raise ValueError("event_duration is too large for the timeseries resolution.")

    for start_idx in range(max_start_idx + 1):
        window_sum = prefix_sum[start_idx + steps_needed] - prefix_sum[start_idx]
        if window_sum < min_sum:
            min_sum = window_sum
            best_start_idx = start_idx

    best_start_time = times[best_start_idx]
    # Turn it back into a datetime object with explicit UTC timezone
    tz = ZoneInfo("UTC")
    best_start_time = best_start_time.astype(datetime).replace(tzinfo=tz)

    # Unused plot code to visually verify that it's working
    if False:
        # Convert numpy datetime array to matplotlib format
        # If `times` is not numpy datetime64, you can skip this or adapt as needed.
        # If `times` is a list of Python `datetime` objects, also skip the conversion step.
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        fig, ax = plt.subplots(figsize=(10, 6))

        # Plot the occupancy as a step or line plot
        ax.plot(times, occupancy, label="Occupancy", drawstyle="steps-post", color="C0")

        # Create a shaded region representing the best interval for the event
        event_start = best_start_time
        event_end = best_start_time + charging_duration
        ax.axvspan(
            event_start, event_end, color="C2", alpha=0.3, label="Chosen Interval"
        )

        # Format the x-axis to show date/time
        # This only applies if your `times` are datetime objects or convertible to them
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d %H:%M:%S"))
        plt.xticks(rotation=45, ha="right")

        ax.set_xlabel("Time")
        ax.set_ylabel("Occupancy (# of events)")
        ax.set_title("Charger Occupancy with Chosen Event Interval")
        ax.legend()
        ax.grid(True)

        plt.tight_layout()
        plt.show()

    return best_start_time


def attempt_opportunity_charging_event(
    previous_trip: Trip,
    next_trip: Trip,
    vehicle: Vehicle,
    charge_start_soc: float,
    terminus_deadtime: timedelta,
    session: sqlalchemy.orm.session.Session,
) -> float:
    logger = logging.getLogger(__name__)

    # Sanity checks
    if previous_trip.route.arrival_station_id != next_trip.route.departure_station_id:
        warnings.warn(
            f"Trips {previous_trip.id} and {next_trip.id} are not consecutive.",
            ConsistencyWarning,
        )
        return charge_start_soc
    if previous_trip.rotation_id != next_trip.rotation_id:
        raise ValueError(
            f"Trips {previous_trip.id} and {next_trip.id} are not in the same rotation."
        )
    if not (previous_trip.scenario_id == next_trip.scenario_id == vehicle.scenario_id):
        raise ValueError(
            f"Trips {previous_trip.id} and {next_trip.id} are not in the same scenario."
        )
    if not (
        vehicle.vehicle_type.opportunity_charging_capable
        and next_trip.rotation.allow_opportunity_charging
        and previous_trip.route.arrival_station.is_electrified
        and previous_trip.route.arrival_station.charge_type == ChargeType.OPPORTUNITY
    ):
        raise ValueError(
            "Opportunity charging was requested even though it is not possible."
        )

    # Identify the break time between trips
    break_time = next_trip.departure_time - previous_trip.arrival_time

    if break_time > terminus_deadtime:
        logger.debug(f"Adding opportunity charging event after trip {previous_trip.id}")

        # How much energy can be charged in this time?
        max_recharged_energy = (
            max([v[1] for v in vehicle.vehicle_type.charging_curve])
            * (break_time.total_seconds() - terminus_deadtime.total_seconds())
            / 3600
        )
        needed_energy = (1 - charge_start_soc) * vehicle.vehicle_type.battery_capacity

        if max_recharged_energy < needed_energy:
            # We do not need to shift the time around. Just charge as much as possible
            time_event_start = previous_trip.arrival_time
            time_charge_start = time_event_start + terminus_deadtime / 2
            time_charge_end = next_trip.departure_time - terminus_deadtime / 2
            time_event_end = next_trip.departure_time

            soc_event_start = charge_start_soc
            soc_charge_start = charge_start_soc
            soc_charge_end = (
                charge_start_soc
                + max_recharged_energy / vehicle.vehicle_type.battery_capacity
            )
            assert soc_charge_end <= 1
            soc_event_end = soc_charge_end
        else:
            needed_duration_purely_charing = timedelta(
                seconds=(
                    ceil(
                        needed_energy
                        * 3600
                        / max([v[1] for v in vehicle.vehicle_type.charging_curve])
                    )
                )
            )
            needed_duration_total = needed_duration_purely_charing + terminus_deadtime

            # We have to shift the time around to the time with the lowest occupancy
            # Within this time band.

            best_start_time = find_best_timeslot(
                previous_trip.route.arrival_station,
                previous_trip.arrival_time,
                next_trip.departure_time,
                needed_duration_total,
                session,
            )
            time_event_start = best_start_time
            time_charge_start = best_start_time + terminus_deadtime / 2
            time_charge_end = time_charge_start + needed_duration_purely_charing
            time_event_end = time_charge_end + (terminus_deadtime / 2)

            soc_event_start = charge_start_soc
            soc_charge_start = charge_start_soc
            soc_charge_end = 1
            soc_event_end = 1

        # Create a simple timeseries for the charging event
        timeseries = {
            "time": [
                time_event_start.isoformat(),
                time_charge_start.isoformat(),
                time_charge_end.isoformat(),
                time_event_end.isoformat(),
            ],
            "soc": [soc_event_start, soc_charge_start, soc_charge_end, soc_event_end],
        }

        # Create the charging event
        current_event = Event(
            scenario_id=vehicle.scenario_id,
            vehicle_type_id=vehicle.vehicle_type_id,
            vehicle=vehicle,
            station_id=previous_trip.route.arrival_station_id,
            time_start=time_event_start,
            time_end=time_event_end,
            soc_start=charge_start_soc,
            soc_end=soc_event_end,
            event_type=EventType.CHARGING_OPPORTUNITY,
            description=f"Opportunity charging event after trip {previous_trip.id}.",
            timeseries=timeseries,
        )
        session.add(current_event)

        # If there is time between the previous trip's end and the charging event's start, add a STANDBY event
        if time_event_start > previous_trip.arrival_time:
            standby_event = Event(
                scenario_id=vehicle.scenario_id,
                vehicle_type_id=vehicle.vehicle_type_id,
                vehicle=vehicle,
                station_id=previous_trip.route.arrival_station_id,
                time_start=previous_trip.arrival_time,
                time_end=time_event_start,
                soc_start=charge_start_soc,  # SoC is unchanged while in STANDBY
                soc_end=charge_start_soc,
                event_type=EventType.STANDBY,
                description=f"Standby event before charging after trip {previous_trip.id}.",
                timeseries=None,
            )
            session.add(standby_event)

        # If there is time between the charging event's end and the next trip's start, add a STANDBY_DEPARTURE event
        if time_event_end < next_trip.departure_time:
            standby_departure_event = Event(
                scenario_id=vehicle.scenario_id,
                vehicle_type_id=vehicle.vehicle_type_id,
                vehicle=vehicle,
                station_id=previous_trip.route.arrival_station_id,
                time_start=time_event_end,
                time_end=next_trip.departure_time,
                soc_start=soc_event_end,  # SoC is unchanged while in STANDBY
                soc_end=soc_event_end,
                event_type=EventType.STANDBY_DEPARTURE,
                description=(
                    f"Standby departure event after charging, before trip {next_trip.id}."
                ),
                timeseries=None,
            )
            session.add(standby_departure_event)

        return soc_event_end

    else:
        logger.debug(
            f"No opportunity charging event added after trip {previous_trip.id}"
        )
        return charge_start_soc
