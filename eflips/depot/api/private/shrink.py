"""Shrink Areas and electrified terminus Stations to peak observed concurrency.

Internal helpers for :func:`eflips.depot.api.shrink_to_peak_usage`. Operates on
events already persisted to the database (run after
:func:`eflips.depot.api.add_evaluation_to_database`).
"""

import math
from datetime import timedelta
from typing import Iterable, List

import numpy as np
from eflips.model import (
    Area,
    AreaType,
    AssocAreaProcess,
    ChargeType,
    Event,
    EventType,
    Scenario,
    Station,
)
from sqlalchemy.orm import Session


def _compute_peak_concurrency(events: Iterable[Event], resolution: timedelta) -> int:
    """Count the maximum number of simultaneously-active events.

    Each event occupies time blocks ``[floor(start/res), max(start+1, ceil(end/res)))``
    relative to the earliest start. Back-to-back events at a block boundary do not
    overlap; sub-resolution events still count as one block.
    """
    events_list: List[Event] = list(events)
    if not events_list:
        return 0

    res_s = resolution.total_seconds()
    if res_s <= 0:
        raise ValueError("resolution must be a positive timedelta")

    t_min = min(e.time_start for e in events_list)
    t_max = max(e.time_end for e in events_list)
    span_s = (t_max - t_min).total_seconds()
    n_blocks = max(1, math.ceil(span_s / res_s))
    arr = np.zeros(n_blocks, dtype=np.int64)

    for e in events_list:
        start_offset = (e.time_start - t_min).total_seconds()
        end_offset = (e.time_end - t_min).total_seconds()
        s = int(start_offset // res_s)
        f = int(math.ceil(end_offset / res_s))
        if f <= s:
            f = s + 1
        arr[s:f] += 1

    return int(arr.max())


def _round_capacity_for_area_type(peak: int, area: Area) -> int:
    """Round ``peak`` UP to the next valid capacity for ``area.area_type``.

    Preserves the CHECK constraint defined on :class:`eflips.model.Area`:

    - DIRECT_ONESIDE: any positive int
    - DIRECT_TWOSIDE: capacity must be even
    - LINE: capacity must be a multiple of ``row_count`` (which is preserved)
    """
    if peak <= 0:
        raise ValueError("peak must be > 0 for capacity rounding")

    if area.area_type == AreaType.DIRECT_ONESIDE:
        return peak
    if area.area_type == AreaType.DIRECT_TWOSIDE:
        return peak + (peak % 2)
    if area.area_type == AreaType.LINE:
        if area.row_count is None or area.row_count <= 0:
            raise ValueError(
                f"LINE area {area.id} has invalid row_count={area.row_count}"
            )
        return math.ceil(peak / area.row_count) * area.row_count
    raise ValueError(f"Unknown area_type {area.area_type!r} on area {area.id}")


def _shrink_areas_to_peak(
    scenario: Scenario, session: Session, resolution: timedelta
) -> None:
    """Shrink every :class:`Area` in ``scenario`` to its peak observed usage.

    Areas whose peak concurrency is zero are deleted along with their
    :class:`AssocAreaProcess` rows. If we somehow compute a zero peak while
    events still reference the area we raise :class:`RuntimeError` rather than
    silently delete persisted state.
    """
    areas = session.query(Area).filter(Area.scenario_id == scenario.id).all()
    area_ids_to_delete: List[int] = []

    for area in areas:
        peak = _compute_peak_concurrency(area.events, resolution)

        if peak > 0:
            area.capacity = _round_capacity_for_area_type(peak, area)
            continue

        lingering = session.query(Event).filter(Event.area_id == area.id).count()
        if lingering > 0:
            raise RuntimeError(
                f"Area {area.id} has peak concurrency 0 but {lingering} "
                "lingering events: refusing to delete."
            )

        area_ids_to_delete.append(area.id)

    if area_ids_to_delete:
        # Detach soon-to-be-deleted areas from the ORM so cascades on
        # AssocAreaProcess do not fight the bulk delete below.
        for area in areas:
            if area.id in area_ids_to_delete:
                session.expunge(area)

        session.query(AssocAreaProcess).filter(
            AssocAreaProcess.area_id.in_(area_ids_to_delete)
        ).delete(synchronize_session=False)
        session.query(Area).filter(Area.id.in_(area_ids_to_delete)).delete(
            synchronize_session=False
        )

    session.flush()


def _shrink_stations_to_peak(
    scenario: Scenario, session: Session, resolution: timedelta
) -> None:
    """Shrink electrified opportunity-charging Stations to peak observed usage.

    Only Stations with ``is_electrified=True`` and
    ``charge_type=ChargeType.OPPORTUNITY`` are considered. If peak > 0 we update
    ``amount_charging_places`` and recompute ``power_total``. If peak == 0 we
    un-electrify the station, atomically nulling all electrification fields to
    keep the CHECK constraint satisfied.
    """
    stations = (
        session.query(Station)
        .filter(Station.scenario_id == scenario.id)
        .filter(Station.is_electrified.is_(True))
        .filter(Station.charge_type == ChargeType.OPPORTUNITY)
        .all()
    )

    for station in stations:
        events = (
            session.query(Event)
            .filter(Event.station_id == station.id)
            .filter(Event.event_type == EventType.CHARGING_OPPORTUNITY)
            .all()
        )
        peak = _compute_peak_concurrency(events, resolution)

        if peak > 0:
            station.amount_charging_places = peak
            if station.power_per_charger is not None:
                station.power_total = peak * station.power_per_charger
            continue

        lingering = (
            session.query(Event)
            .filter(Event.station_id == station.id)
            .filter(Event.event_type == EventType.CHARGING_OPPORTUNITY)
            .count()
        )
        if lingering > 0:
            raise RuntimeError(
                f"Station {station.id} has peak concurrency 0 but {lingering} "
                "CHARGING_OPPORTUNITY events: refusing to un-electrify."
            )

        station.is_electrified = False
        station.amount_charging_places = None
        station.power_per_charger = None
        station.power_total = None
        station.charge_type = None
        station.voltage_level = None
        session.flush()
