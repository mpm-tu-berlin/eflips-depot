"""This module contains miscellaneous utility functions for the eflips-depot API."""
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta, datetime
from typing import Union, Any, Optional, Tuple, Dict, List

import simpy
import numpy as np
from eflips.model import (
    Scenario,
    VehicleType,
    Rotation,
    Event,
    EventType,
    Trip,
    Depot,
    Temperatures,
)
from sqlalchemy import inspect, create_engine
from sqlalchemy.orm import Session

from eflips.depot import SimpleTrip, Timetable as EflipsTimeTable


@contextmanager
def create_session(
    scenario: Union[Scenario, int, Any], database_url: Optional[str] = None
) -> Tuple[Session, Scenario]:
    """
    Create a valid session from various inputs.

    This method takes a scenario, which can be either a :class:`eflips.model.Scenario` object, an integer specifying
    the ID of a scenario in the database, or any other object that has an attribute `id` that is an integer. It then
    creates a SQLAlchemy session and returns it. If the scenario is a :class:`eflips.model.Scenario` object, the
    session is created and returned. If the scenario is an integer or an object with an `id` attribute, the session
    is created, returned and closed after the context manager is exited.

    :param scenario: Either a :class:`eflips.model.Scenario` object, an integer specifying the ID of a scenario in the
        database, or any other object that has an attribute `id` that is an integer.
    :return: Yield a Tuple of the session and the scenario.
    """
    logger = logging.getLogger(__name__)

    managed_session = False
    engine = None
    session = None
    try:
        if isinstance(scenario, Scenario):
            session = inspect(scenario).session
        elif isinstance(scenario, int) or hasattr(scenario, "id"):
            logger.warning(
                "Scenario passed was not part of an active session. Uncommited changes will be ignored."
            )

            if isinstance(scenario, int):
                scenario_id = scenario
            else:
                scenario_id = scenario.id

            if database_url is None:
                if "DATABASE_URL" in os.environ:
                    database_url = os.environ.get("DATABASE_URL")
                else:
                    raise ValueError("No database URL specified.")

            managed_session = True
            engine = create_engine(database_url)
            session = Session(engine)
            scenario = session.query(Scenario).filter(Scenario.id == scenario_id).one()
        else:
            raise ValueError(
                "The scenario parameter must be either a Scenario object, an integer or object with an 'id' attribute."
            )
        yield session, scenario
    finally:
        if managed_session:
            if session is not None:
                session.commit()
                session.close()
            if engine is not None:
                engine.dispose()


def vehicle_type_to_global_constants_dict(vt: VehicleType) -> Dict[str, float]:
    """
    This converts the VehicleType object into a dictionary, which is the input.

    format of the eflips.globalConstants object.

    :return: A dictionary describing some of the properties of the vehicle type.
    """

    the_dict = {
        "battery_capacity": vt.battery_capacity,
        "soc_min": 0.0,
        "soc_max": 1.0,
        "soc_init": 1.0,
        "soh": 1.0,
    }
    return the_dict


def repeat_vehicle_schedules(
    vehicle_schedules: List["VehicleSchedule"], repetition_period: timedelta
) -> List["VehicleSchedule"]:
    """
    This method repeats the vehicle schedules in the list `vehicle_schedules` by the timedelta `repetition_period`.

    It takes the given vehicle schedules and creates two copies, one `repetition_period` earlier, one
    `repetition_period` later. It then returns the concatenation of the three lists.

    :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    :param repetition_period: A timedelta object specifying the period of the vehicle schedules.
    :return: a list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    """
    # Add the repeated schedules to the forward and backward lists
    schedule_list_backward = []
    schedule_list_forward = []

    for vehicle_schedule in vehicle_schedules:
        schedule_list_backward.append(vehicle_schedule.repeat(-repetition_period))
        schedule_list_backward.append(vehicle_schedule.repeat(-2 * repetition_period))

        schedule_list_forward.append(vehicle_schedule.repeat(repetition_period))

    vehicle_schedules = (
        schedule_list_backward + vehicle_schedules + schedule_list_forward
    )

    return vehicle_schedules


def start_and_end_times(vehicle_schedules) -> Tuple[datetime, int]:
    """
    This method is used to find the start time and duration for simulating a given list of vehicle schedules.

    It finds the times of midnight of the day of the first departure and midnight of the day after the last arrival.

    :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    :return: The datetime of midnight of the day of the first departure and the total duration of the simulation in
        seconds.
    """

    first_departure_time = min(
        [vehicle_schedule.departure for vehicle_schedule in vehicle_schedules]
    )
    midnight_of_first_departure_day = first_departure_time.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_arrival_time = max(
        [vehicle_schedule.arrival for vehicle_schedule in vehicle_schedules]
    )
    midnight_of_last_arrival_day = last_arrival_time.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    midnight_after_last_arrival_day = midnight_of_last_arrival_day + timedelta(days=1)
    total_duration_seconds = int(
        (
            midnight_after_last_arrival_day - midnight_of_first_departure_day
        ).total_seconds()
    )

    return midnight_of_first_departure_day, total_duration_seconds


def check_depot_validity(depot: Depot) -> None:
    """
    Check if the depot is valid for the eflips-depot simulation.

    Raise an AssertionError if it is not.
    :param depot: a :class:`eflips.model.Depot` object.
    :return: None
    """
    # 1. There must be an area containing no vehicle types
    has_waiting_area = False
    areas = depot.areas
    for area in areas:
        if area.vehicle_type_id is None:
            # TODO might change to len(area.vehicle_types) == 0 after a list of vehicle types is allowed
            has_waiting_area = True

    assert (
        has_waiting_area
    ), "There must be an area containing no vehicle types as the waiting area."

    # 2. There must be only one process with no duration and no electric power, and it must be the last process in the plan
    plan = depot.default_plan
    processes = plan.processes
    last_process = processes[-1]
    assert (
        last_process.duration is None
        and last_process.electric_power is None
        and last_process.dispatchable is True
    ), "The last process must be dispatchable and have no duration and no electric power."

    for process in processes[:-1]:
        assert (
            process.electric_power is not None or process.duration is not None
        ), "All processes except the last one must have electric power."


def temperature_for_trip(trip_id: int, session: Session) -> float:
    """
    Returns the temperature for a trip. Finds the temperature for the mid-point of the trip.

    :param trip_id: The ID of the trip
    :param session: The SQLAlchemy session
    :return: A temperature in °C
    """

    trip = session.query(Trip).filter(Trip.id == trip_id).one()
    temperatures = (
        session.query(Temperatures)
        .filter(Temperatures.scenario_id == trip.scenario_id)
        .one()
    )

    # Find the mid-point of the trip
    mid_time = trip.departure_time + (trip.arrival_time - trip.departure_time) / 2

    if temperatures.use_only_time:
        # The temperatures are only given by time. We change our mid-time to be the date of the temperatures
        mid_time = datetime.combine(temperatures.datetimes[0].date(), mid_time.time())

    mid_time = mid_time.timestamp()

    datetimes = [dt.timestamp() for dt in temperatures.datetimes]
    temperatures = temperatures.data

    temperature = np.interp(mid_time, datetimes, temperatures)
    return float(temperature)


@dataclass
class VehicleSchedule:
    """
    This class represents a vehicle schedule in eFLIPS-Depot.

    A vehicle schedule presents everything a vehicle does
    between leaving the depot and returning to the depot. In eFLIPS-Depot, we only care about a reduced set of
    information, limited to the interaction with the depot.
    """

    id: str
    """Unique ID of this vehicle schedule.

    This identifier will be returned in the output of eFLIPS-Depot.
    """

    vehicle_type: str
    """The vehicle type of this vehicle schedule.

    This is the ID of a vehicle type in the database.
    """

    departure: datetime
    """
    The departure time of the vehicle from the depot.

    It *must* include the timezone information.
    """

    arrival: datetime
    """
    The arrival time of the vehicle at the depot.

    It *must* include the timezone information.
    """

    departure_soc: float
    """
    The battery state of charge (SoC) of the vehicle at the departure time. It must be in the range [0, 1].

    Note that
    this SoC may not be ctually reached, e.g. if the vehicle is not fully charged when it leaves the depot. The depot
    simulation should always be run multiple times until the `departure_soc` stabilizes.
    """

    arrival_soc: float
    """
    The battery state of charge (SoC) of the vehicles at the arrival time. It must be in the range [-inf, 1].

    This value
    is calculated by a consumption model, e.g. the consumption model of the `ebustoolbox` package. It is a dictionary
    mapping vehicle types to floats. The dictionary must contain an entry for each vehicle type that is part of the
    `vehicle_class` of this vehicle schedule.

    **NOTE**: For the current API version, we only support a single vehicle type per vehicle schedule. This means that
    the dictionary must contain exactly one entry.
    """

    minimal_soc: float
    """
    The minimal battery state of charge (SoC) of the vehicle during the trip.

    It must be in the range [-inf, 1]. This
    value is calculated by a consumption model, e.g. the consumption model of the `ebustoolbox` package.
    """

    opportunity_charging: bool
    """Whether the vehicle is opportunity-charged (meaning charging at terminus stations) during the trip."""

    start_depot_id: str
    """The ID of the depot where the vehicle starts its trip."""

    end_depot_id: str
    """The ID of the depot where the vehicle ends its trip."""

    _is_copy: bool = False
    """
    Whether this vehicle schedule is a copy of another vehicle schedule.

    It should not be set manually, but only by
    calling the :meth:`repeat` method.
    """

    @classmethod
    def from_rotation(
        self,
        rot: Rotation,
        scenario,
        session,
    ):
        """
        This constructor creates a VehicleSchedule object from a Rotation object.

        It is intended to be used by the
        eflips-depot API.

        :param rot: The Rotation object from which the VehicleSchedule is created.
        :param scenario: The Scenario object to which the Rotation belongs.
        :param session: The database session object.
        :param use_builtin_consumption_model: Whether to use the built-in consumption model of eflips-depot. If set
            to `True`, the `VehicleType.consumption` field is used. If set to `False`, consumption is calculated
            from the `Event` table in the database. This (and an external consumption model) is the recommended way.
        """

        rotation_id = str(rot.id)
        departure = rot.trips[0].departure_time
        arrival = rot.trips[-1].arrival_time

        # Find the event for each trip
        events = (
            session.query(Event)
            .filter(Event.event_type == EventType.DRIVING)
            .join(Trip)
            .join(Rotation)
            .filter(Rotation.id == rot.id)
            .order_by(Event.time_start)
            .all()
        )
        trips = (
            session.query(Trip)
            .filter(Trip.rotation_id == rot.id)
            .order_by(Trip.departure_time)
            .all()
        )
        if len(events) != len(trips):
            raise ValueError(
                f"Rotation {rot.id} has {len(trips)} trips but {len(events)} events."
            )
        if set([event.trip_id for event in events]) != set([trip.id for trip in trips]):
            raise ValueError(f"The events of rotation {rot.id} do not match the trips.")

        departure_soc = events[0].soc_start
        arrival_soc = events[-1].soc_end
        minimal_soc = min([event.soc_end for event in events])

        opportunity_charging = rot.allow_opportunity_charging

        # Find the depot at the start and end of the rotation
        start_depot = (
            session.query(Depot)
            .filter(Depot.station_id == trips[0].route.departure_station_id)
            .one_or_none()
        )
        end_depot = (
            session.query(Depot)
            .filter(Depot.station_id == trips[-1].route.arrival_station_id)
            .one_or_none()
        )

        if start_depot is None or end_depot is None:
            raise ValueError(f"Rotation {rot.id} has no depot at the start or end.")

        return VehicleSchedule(
            id=rotation_id,
            start_depot_id=str(start_depot.id),
            end_depot_id=str(end_depot.id),
            vehicle_type=str(rot.vehicle_type.id),
            departure=departure,
            arrival=arrival,
            departure_soc=departure_soc,
            arrival_soc=arrival_soc,
            minimal_soc=minimal_soc,
            opportunity_charging=opportunity_charging,
        )

    def _to_simple_trip(
        self, simulation_start_time: datetime, env: simpy.Environment
    ) -> SimpleTrip:
        """
        This converts the vehicle schedule into a :class:`eflips.depot.standalone.SimpleTrip` object, which is the.

        input format of the depot simulation.

        :param simulation_start_time: The time that serves as "zero" for the simulation. It must be before the
            `departure` time of the first of all vehicle schedules, probably midnight of the first day.
        :param env: The simulation environment object. It should be the `env` of the SimulationHost object.

        :return: A :class:`eflips.depot.standalone.SimpleTrip` object.
        """

        vehicle_types = [self.vehicle_type]
        departure = int((self.departure - simulation_start_time).total_seconds())
        arrival = int((self.arrival - simulation_start_time).total_seconds())
        simple_trip = SimpleTrip(
            env=env,
            ID=self.id,
            line_name=None,
            origin=self.start_depot_id,
            destination=self.end_depot_id,
            vehicle_types=vehicle_types,
            std=departure,
            sta=arrival,
            distance=None,
            start_soc=self.departure_soc,
            end_soc=self.arrival_soc,
            minimal_soc=self.minimal_soc,
            charge_on_track=self.opportunity_charging,
            is_copy=self._is_copy,
        )
        return simple_trip

    def repeat(self, interval: timedelta) -> "VehicleSchedule":
        """
        Repeats a given VehicleSchedule.

        Returns a new vehicle schdule offset by a given timedelta that has the
        _copy_of field filled

        :return: A VehicleSchedule object
        """
        sched = VehicleSchedule(
            self.id,
            self.vehicle_type,
            self.departure + interval,
            self.arrival + interval,
            self.departure_soc,
            self.arrival_soc,
            self.minimal_soc,
            self.opportunity_charging,
            start_depot_id=self.start_depot_id,
            end_depot_id=self.end_depot_id,
        )
        sched._is_copy = True
        return sched

    @staticmethod
    def _to_timetable(
        vehicle_schedules: List["VehicleSchedule"],
        env: simpy.Environment,
        start_of_simulation: datetime,
    ) -> EflipsTimeTable:
        """
        This converts a list of VehicleSchedule objects into a :class:`eflips.depot.standalone.Timetable` object, which.

        is the input format of the depot simulation. This Timetable object is part of the "black box" not covered by
        the API documentation.

        :param: vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
        :param: env: The simulation environment object. It should be the `env` of the SimulationHost object.
        :param: start_of_simulation The datetime that will be used as "zero" for the simulation. It should be before the
            `departure` time of the first of all vehicle schedules, probably midnight of the first day.

        :return: A :class:`eflips.depot.standalone.Timetable` object.
        """

        # Sort the vehicle schedules by departure time
        vehicle_schedules = sorted(vehicle_schedules, key=lambda x: x.departure)

        # Convert the vehicle schedules into SimpleTrip objects
        simple_trips = []
        for vehicle_schedule in vehicle_schedules:
            simple_trip = vehicle_schedule._to_simple_trip(start_of_simulation, env)
            simple_trips.append(simple_trip)

        timetable = EflipsTimeTable(env, simple_trips)

        return timetable
