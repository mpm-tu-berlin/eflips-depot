import logging
from datetime import timedelta

import sqlalchemy.orm
from eflips.model import (
    Area,
    Event,
    EventType,
    Rotation,
    Vehicle,
    Trip,
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
):
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
    rotation_per_vehicle = sorted(
        vehicle.rotations, key=lambda r: r.trips[0].departure_time
    )
    earliest_trip = rotation_per_vehicle[0].trips[0]
    area = (
        session.query(Area)
        .filter(Area.scenario_id == vehicle.scenario_id)
        .filter(Area.vehicle_type_id == vehicle.vehicle_type_id)
        .first()
    )

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
        raise ValueError(
            f"Trips {previous_trip.id} and {next_trip.id} are not consecutive."
        )
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
    ):
        raise ValueError(
            "Opportunity charging was requested even though it is not possible."
        )

    # Identify the break time between trips
    break_time = next_trip.departure_time - previous_trip.arrival_time

    if break_time > terminus_deadtime:
        # How much energy can be charged in this time?
        energy_charged = (
            max([v[1] for v in vehicle.vehicle_type.charging_curve])
            * (break_time.total_seconds() - terminus_deadtime.total_seconds())
            / 3600
        )

        logger.debug(f"Adding opportunity charging event after trip {previous_trip.id}")

        # Calculate the end SoC
        post_charge_soc = min(
            charge_start_soc + energy_charged / vehicle.vehicle_type.battery_capacity,
            1,
        )

        # If the post_charge_soc is 1, calculate when the vehicle was full
        if post_charge_soc == 1:
            # 1. Get the max charging power (kW)
            max_power = max([v[1] for v in vehicle.vehicle_type.charging_curve])

            # 2. Energy needed (kWh) to go from current_soc to 100%
            energy_needed_kWh = (
                1 - charge_start_soc
            ) * vehicle.vehicle_type.battery_capacity

            # 3. Compute how long that takes at max_power (in hours)
            time_needed_hours = energy_needed_kWh / max_power

            # 4. Calculate the point in time the vehicle became full
            #    If charging effectively starts right after terminus_deadtime
            time_full = (
                previous_trip.arrival_time
                + terminus_deadtime / 2
                + timedelta(hours=time_needed_hours)
            )

            # 5. Make sure it is before the time charging must end the latest
            assert time_full <= next_trip.departure_time - (terminus_deadtime / 2)

        else:
            time_full = None

        # Create a simple timeseries for the charging event
        timeseries = {
            "time": [
                previous_trip.arrival_time.isoformat(),
                (previous_trip.arrival_time + terminus_deadtime / 2).isoformat(),
                (next_trip.departure_time - terminus_deadtime / 2).isoformat(),
                next_trip.departure_time.isoformat(),
            ],
            "soc": [
                charge_start_soc,
                charge_start_soc,
                post_charge_soc,
                post_charge_soc,
            ],
        }

        # If time_full is not None, add it to the timeseries in the middle
        if time_full is not None:
            timeseries["time"].insert(2, time_full.isoformat())
            timeseries["soc"].insert(2, 1)

        # Create the charging event
        current_event = Event(
            scenario_id=vehicle.scenario_id,
            vehicle_type_id=vehicle.vehicle_type_id,
            vehicle=vehicle,
            station_id=previous_trip.route.arrival_station_id,
            time_start=previous_trip.arrival_time,
            time_end=next_trip.departure_time,
            soc_start=charge_start_soc,
            soc_end=post_charge_soc,
            event_type=EventType.CHARGING_OPPORTUNITY,
            description=f"Opportunity charging event after trip {previous_trip.id}.",
            timeseries=timeseries,
        )
        session.add(current_event)
        return post_charge_soc

    else:
        logger.debug(
            f"No opportunity charging event added after trip {previous_trip.id}"
        )
        return charge_start_soc
