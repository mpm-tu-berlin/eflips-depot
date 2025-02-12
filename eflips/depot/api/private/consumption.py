import logging
import warnings
from datetime import timedelta, datetime
from math import ceil
from typing import Tuple
from zoneinfo import ZoneInfo

import numpy as np
import sqlalchemy.orm
from eflips.model import (
    Event,
    EventType,
    Rotation,
    Vehicle,
    Trip,
    Station,
    ChargeType,
    ConsistencyWarning,
)


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
