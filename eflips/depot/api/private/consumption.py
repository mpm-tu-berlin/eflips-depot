import logging
import warnings
from dataclasses import dataclass
from datetime import timedelta, datetime
from math import ceil
from typing import Tuple, List
from zoneinfo import ZoneInfo
import scipy

import numpy as np
import sqlalchemy.orm
from eflips.model import (
    Event,
    EventType,
    Rotation,
    Vehicle,
    VehicleType,
    VehicleClass,
    Trip,
    Station,
    ChargeType,
    ConsistencyWarning,
    ConsumptionLut,
    Scenario,
)
from sqlalchemy.orm import joinedload

from eflips.depot.api.private.util import temperature_for_trip, create_session


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
class ConsumptionInformation:
    """
    A dataclass to hold the information needed for the consumption simulation.

    :param trip_id:
        The ID of the trip for which the consumption is calculated.
    :param consumption_lut:
        The ConsumptionLut object for the vehicle class. This is used to calculate the
        consumption based on the trip parameters.
    :param average_speed:
        The average speed of the trip in km/h. This is used to calculate the consumption.
    :param distance:
        The distance of the trip in km. This is used to calculate the total consumption.
    :param temperature:
        The ambient temperature in °C. This is used to calculate the consumption.
    :param level_of_loading:
        The level of loading of the vehicle as a fraction of its maximum payload.
    :param incline:
        The incline of the trip as a fraction (0.0-1.0). This is used to calculate the consumption.
    :param consumption:
        The total consumption of the trip in kWh. This is calculated based on the LUT and trip parameters.
    :param consumption_per_km:
        The consumption per km in kWh. This is calculated based on the LUT and trip parameters.
    """

    trip_id: int
    consumption_lut: ConsumptionLut | None  # the LUT for the vehicle class
    average_speed: float  # the average speed of the trip in km/h
    distance: float  # the distance of the trip in km
    temperature: float  # The ambient temperature in °C
    level_of_loading: float
    incline: float = 0.0  # The incline of the trip in 0.0-1.0
    consumption: float = None  # The consumption of the trip in kWh
    consumption_per_km: float = None  # The consumption per km in kWh

    def calculate(self):
        """
        Calculates the consumption for the trip. Returns a float in kWh.

        :return: The energy consumption in kWh. This is already the consumption for the whole trip.
        """

        # Make sure the consumption lut has 4 dimensions and the columns are in the correct order
        if self.consumption_lut.columns != [
            "incline",
            "t_amb",
            "level_of_loading",
            "mean_speed_kmh",
        ]:
            raise ValueError(
                "The consumption LUT must have the columns 'incline', 't_amb', 'level_of_loading', 'mean_speed_kmh'"
            )

        # Recover the scales along each of the four axes from the datapoints
        incline_scale = sorted(set([x[0] for x in self.consumption_lut.data_points]))
        temperature_scale = sorted(
            set([x[1] for x in self.consumption_lut.data_points])
        )
        level_of_loading_scale = sorted(
            set([x[2] for x in self.consumption_lut.data_points])
        )
        speed_scale = sorted(set([x[3] for x in self.consumption_lut.data_points]))

        # Create the 4d array
        consumption_lut = np.zeros(
            (
                len(incline_scale),
                len(temperature_scale),
                len(level_of_loading_scale),
                len(speed_scale),
            )
        )

        # Fill it with NaNs
        consumption_lut.fill(np.nan)

        for i, (incline, temperature, level_of_loading, speed) in enumerate(
            self.consumption_lut.data_points
        ):
            consumption_lut[
                incline_scale.index(incline),
                temperature_scale.index(temperature),
                level_of_loading_scale.index(level_of_loading),
                speed_scale.index(speed),
            ] = self.consumption_lut.values[i]

        # Interpolate the consumption
        interpolator = scipy.interpolate.RegularGridInterpolator(
            (incline_scale, temperature_scale, level_of_loading_scale, speed_scale),
            consumption_lut,
            bounds_error=False,
            fill_value=None,
            method="linear",
        )
        consumption_per_km = interpolator(
            [self.incline, self.temperature, self.level_of_loading, self.average_speed]
        )[0]

        # This is a temporary workaround to handle cases where the LUT does not contain
        if consumption_per_km is None or np.isnan(consumption_per_km):
            # Add a warning if we had to use nearest neighbor interpolation
            warnings.warn(
                f"Consumption LUT for trip {self.trip_id} with parameters: "
                f"incline={self.incline}, temperature={self.temperature}, "
                f"level_of_loading={self.level_of_loading}, average_speed={self.average_speed} "
                f"returned NaN. Using nearest neighbor interpolation instead. The result may be less accurate.",
                ConsistencyWarning,
            )

            interpolator_nn = scipy.interpolate.RegularGridInterpolator(
                (incline_scale, temperature_scale, level_of_loading_scale, speed_scale),
                consumption_lut,
                bounds_error=False,
                fill_value=None,  # Fill NaN with 0.0
                method="nearest",
            )
            consumption_per_km = interpolator_nn(
                [
                    self.incline,
                    self.temperature,
                    self.level_of_loading,
                    self.average_speed,
                ]
            )[0]

            # Add a warning if we had to use nearest neighbor interpolation

        if consumption_per_km is None or np.isnan(consumption_per_km):
            raise ValueError(
                f"Could not calculate consumption for trip {self.trip_id} with parameters: "
                f"incline={self.incline}, temperature={self.temperature}, "
                f"level_of_loading={self.level_of_loading}, average_speed={self.average_speed}. "
                f"Possible reason: data points missing in the LUT."
            )

        self.consumption = consumption_per_km * self.distance
        self.consumption_per_km = consumption_per_km
        self.consumption_lut = None  # To save memory

    def generate_consumption_result(self, battery_capacity) -> ConsumptionResult:
        """
        Generates a ConsumptionResult object from the current instance.

        :param battery_capacity: The battery capacity in kWh.
        :return: A ConsumptionResult object containing the total change in SoC and optional timeseries.
        """
        if self.consumption is None:
            raise ValueError(
                "Consumption must be calculated before generating a result."
            )

        # TODO implement a timeseries of timestamps and delta_soc
        consumption_result = ConsumptionResult(
            delta_soc_total=-float(self.consumption) / battery_capacity,
            timestamps=None,
            delta_soc=None,
        )
        return consumption_result


def extract_trip_information(
    trip_id: int,
    scenario: Scenario,
    passenger_mass=68,
    passenger_count=17.6,
) -> ConsumptionInformation:
    """
    Extracts the information needed for the consumption simulation from a trip.
    """

    with create_session(scenario) as (session, scenario):
        # Load the trip with its route and rotation, including vehicle type and consumption LUT
        # We use joinedload to avoid N+1 queries

        trip = (
            session.query(Trip)
            .filter(Trip.id == trip_id)
            .options(joinedload(Trip.route))
            .options(
                joinedload(Trip.rotation)
                .joinedload(Rotation.vehicle_type)
                .joinedload(VehicleType.vehicle_classes)
                .joinedload(VehicleClass.consumption_lut)
            )
            .one()
        )
        # Check exactly one of the vehicle classes has a consumption LUT
        all_consumption_luts = [
            vehicle_class.consumption_lut
            for vehicle_class in trip.rotation.vehicle_type.vehicle_classes
        ]
        all_consumption_luts = [x for x in all_consumption_luts if x is not None]
        if len(all_consumption_luts) != 1:
            raise ValueError(
                f"Expected exactly one consumption LUT, got {len(all_consumption_luts)}"
            )
        consumption_lut = all_consumption_luts[0]
        # Disconnect the consumption LUT from the session to avoid loading the whole table

        del all_consumption_luts

        total_distance = trip.route.distance / 1000  # km
        total_duration = (
            trip.arrival_time - trip.departure_time
        ).total_seconds() / 3600
        average_speed = total_distance / total_duration  # km/h

        temperature = temperature_for_trip(trip_id, session)

        payload_mass = passenger_mass * passenger_count
        full_payload = (
            trip.rotation.vehicle_type.allowed_mass
            - trip.rotation.vehicle_type.empty_mass
        )
        level_of_loading = payload_mass / full_payload

        info = ConsumptionInformation(
            trip_id=trip.id,
            consumption_lut=consumption_lut,
            average_speed=average_speed,
            distance=total_distance,
            temperature=temperature,
            level_of_loading=level_of_loading,
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
