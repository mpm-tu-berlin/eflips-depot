"""
This file contains the public API of eflips-depot. It is intended to allow the user to
run charging simulations on a given scenario.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Dict, List, Tuple

import simpy
from eflips.model import VehicleType, Rotation, Depot, AreaType, Process

from eflips.depot import SimpleTrip
from eflips.depot.simple_vehicle import (
    VehicleType as EflipsVehicleType,
)
from eflips.depot.standalone import Timetable as EflipsTimeTable


def vehicle_type_to_eflips(vt: VehicleType) -> EflipsVehicleType:
    """Convert a VehicleType object to an eflips-depot VehicleType object."""

    # Create the depot VehicleType object
    eflips_vehicle_type = EflipsVehicleType(
        str(vt.id),
        vt.battery_capacity,
        0.0,
        1.0,
        1.0,
        1.0,
        vt.consumption,
    )
    return eflips_vehicle_type


def vehicle_type_to_global_constants_dict(vt: VehicleType) -> Dict[str, float]:
    """
    This converts the VehicleType object into a dictionary, which is the input
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


class ProcessType(Enum):
    """This class represents the types of a process in eFLIPS-Depot."""

    SERVICE = auto()
    """This process represents a bus service by workers. It does not require a charging_power and has a fixed 
    duration."""
    CHARGING = auto()
    """This process represents a bus charging process. It requires a charging_power and has no fixed duration."""
    STANDBY = auto()
    """This process represents an arriving bus that is waiting for a service. It does not require a charging_power 
    and has no fixed duration."""
    STANDBY_DEPARTURE = auto()
    """This process represents a bus ready for departure. It does not require a charging_power and has no fixed 
    duration."""
    PRECONDITION = auto()
    """This process represents a bus preconditioning process. It requires a charging_power and has a fixed duration."""


def process_type(p: Process) -> ProcessType:
    """
    The type of the process. See :class:`eflips.depot.api.input.ProcessType` for more information. Note that whether
    a process needs a resource or not depends on the type of the process.
    """
    if p.duration is not None and p.electric_power is None:
        return ProcessType.SERVICE
    elif p.duration is None and p.electric_power is not None:
        return ProcessType.CHARGING
    elif p.duration is not None and p.electric_power is not None:
        return ProcessType.PRECONDITION
    elif p.duration is None and p.electric_power is None:
        if p.dispatchable:
            return ProcessType.STANDBY_DEPARTURE
        else:
            return ProcessType.STANDBY
    else:
        raise ValueError("Invalid process type")


def depot_to_template(depot: Depot) -> Dict:
    """
    Converts the depot to a template for internal use in the simulation.

    :return: A dict that can be consumed by eFLIPS-Depot.
    """

    # TODO later:
    # if we need to pass datetime for second 0 somewhere for the output
    # if we need to cleanup this function

    # Initialize the template
    template = {
        "templatename_display": "",
        "general": {"depotID": "", "dispatch_strategy_name": ""},
        "resources": {},
        "resource_switches": {},
        "processes": {},
        "areas": {},
        "groups": {},
        "plans": {},
    }

    # Set up the general information
    template["templatename_display"] = depot.name
    template["general"]["depotID"] = "DEFAULT"
    template["general"]["dispatch_strategy_name"] = "SMART"

    # Helper for adding processes to the template
    list_of_processes = []

    # Get dictionary of each area
    for area in depot.areas:
        # area_name = area.name if area.name is not None else str(area.id)
        # TODO keep consistent with name/id for each API class
        area_name = str(area.id)
        template["areas"][area_name] = {
            "typename": (
                "LineArea" if area.area_type == AreaType.LINE else "DirectArea"
            ),
            # "amount": 2,  # TODO Check how multiple areas work. For now leave it as default
            "capacity": area.capacity,
            "available_processes": [
                process.name if process.name is not None else str(process.id)
                for process in area.processes
            ],
            "issink": False,
            "entry_filter": None,
        }

        # Fill in vehicle_filter.
        template["areas"][area_name]["entry_filter"] = {
            "filter_names": ["vehicle_type"],
            "vehicle_types": [str(area.vehicle_type_id)],
        }

        for process in area.processes:
            # Add process into process list
            list_of_processes.append(
                process
            ) if process not in list_of_processes else None

            # Charging interfaces
            if process_type(process) == ProcessType.CHARGING:
                ci_per_area = []
                for i in range(area.capacity):
                    ID = "ci_" + str(len(template["resources"]))
                    template["resources"][ID] = {
                        "typename": "DepotChargingInterface",
                        "max_power": process.electric_power,
                    }
                    ci_per_area.append(ID)

                template["areas"][area_name]["charging_interfaces"] = ci_per_area

            # Set issink to True for departure areas
            if process_type(process) == ProcessType.STANDBY_DEPARTURE:
                template["areas"][area_name][
                    "issink"
                ] = True  # TODO LU: Can a vehicle go on from an area that is a sink?

    for process in list_of_processes:
        process_name = process.name if process.name is not None else str(process.id)
        # Shared template for all processes
        template["processes"][process_name] = {
            "typename": "",  # Placeholder for initialization
            "dur": int(process.duration.total_seconds()) if process.duration else None,
            # True if this process will be executed for all vehicles. False if there are available vehicle filters
            "ismandatory": True,
            "vehicle_filter": {},
            # True if this process can be interrupted by a dispatch. False if it cannot be interrupted
            "cancellable_for_dispatch": False,
        }

        match process_type(process):
            case ProcessType.SERVICE:
                template["processes"][process_name]["typename"] = "Serve"

                # Fill in the worker_service
                service_capacity = sum([x.capacity for x in process.areas])

                template["processes"][process_name]["required_resources"] = [
                    "workers_service"
                ]
                template["resources"]["workers_service"] = {
                    "typename": "DepotResource",
                    "capacity": service_capacity,
                }

                if process.availability is not None:
                    template["resource_switches"]["service_switch"] = {
                        "resource": "workers_service",
                        "breaks": [],
                        "preempt": process.preemptable
                        if process.preemptable is not None
                        else True,
                        # Strength 'full' means all workers can take a break at the same time
                        "strength": "full",
                        # Resume set to True means that the process will continue after the break
                        "resume": True,
                        # Priority -3 means this process has the highest priority
                        "priority": -3,
                    }

                    list_of_breaks = process._generate_break_intervals()
                    list_of_breaks_in_seconds = []

                    # Converting the time intervals into seconds

                    for time_interval in list_of_breaks:
                        start_time = time_interval[0]
                        end_time = time_interval[1]
                        start_time_in_seconds = (
                            start_time.hour * 3600
                            + start_time.minute * 60
                            + start_time.second
                        )

                        end_time_in_seconds = (
                            end_time.hour * 3600
                            + end_time.minute * 60
                            + end_time.second
                        )

                        list_of_breaks_in_seconds.append(
                            (start_time_in_seconds, end_time_in_seconds)
                        )

                    template["resource_switches"]["service_switch"][
                        "breaks"
                    ] = list_of_breaks_in_seconds

            case ProcessType.CHARGING:
                template["processes"][process_name]["typename"] = "Charge"
                del template["processes"][process_name]["dur"]

            case ProcessType.STANDBY | ProcessType.STANDBY_DEPARTURE:
                template["processes"][process_name]["typename"] = "Standby"
                template["processes"][process_name]["dur"] = 0

            case ProcessType.PRECONDITION:
                template["processes"][process_name]["typename"] = "Precondition"
                template["processes"][process_name]["dur"] = int(
                    process.duration.total_seconds()
                )
                template["processes"][process_name]["power"] = process.electric_power
            case _:
                raise ValueError(f"Invalid process type: {process_type(process).name}")

    # Initialize the default plan
    template["plans"]["default"] = {
        "typename": "DefaultActivityPlan",
        "locations": [],
    }
    # Groups
    for process in depot.default_plan.processes:
        group_name = str(process_type(process)) + "_group"
        template["groups"][group_name] = {
            "typename": "AreaGroup",
            "stores": [
                # area.name if area.name is not None else str(area.id)
                str(area.id)
                for area in process.areas
            ],
        }
        if process_type(process) == ProcessType.STANDBY_DEPARTURE:
            template["groups"][group_name]["typename"] = "ParkingAreaGroup"
            template["groups"][group_name]["parking_strategy_name"] = "SMART2"

        # Fill in locations of the plan
        template["plans"]["default"]["locations"].append(group_name)

    return template


@dataclass
class VehicleSchedule:
    """
    This class represents a vehicle schedule in eFLIPS-Depot. A vehicle schedule presents everything a vehicle does
    between leaving the depot and returning to the depot. In eFLIPS-Depot, we only care about a reduced set of
    information, limited to the interaction with the depot.
    """

    id: str
    """Unique ID of this vehicle schedule. This identifier will be returned in the output of eFLIPS-Depot."""

    vehicle_type: str
    """The vehicle type of this vehicle schedule. This is the ID of a vehicle type in the database."""

    departure: datetime
    """
    The departure time of the vehicle from the depot. It *must* include the timezone information.
    """

    arrival: datetime
    """
    The arrival time of the vehicle at the depot. It *must* include the timezone information.
    """

    departure_soc: float
    """
    The battery state of charge (SoC) of the vehicle at the departure time. It must be in the range [0, 1]. Note that
    this SoC may not be ctually reached, e.g. if the vehicle is not fully charged when it leaves the depot. The depot
    simulation should always be run multiple times until the `departure_soc` stabilizes.
    """

    arrival_soc: float
    """
    The battery state of charge (SoC) of the vehicles at the arrival time. It must be in the range [-inf, 1]. This value
    is calculated by a consumption model, e.g. the consumption model of the `ebustoolbox` package. It is a dictionary 
    mapping vehicle types to floats. The dictionary must contain an entry for each vehicle type that is part of the 
    `vehicle_class` of this vehicle schedule. 

    **NOTE**: For the current API version, we only support a single vehicle type per vehicle schedule. This means that
    the dictionary must contain exactly one entry.
    """

    minimal_soc: float
    """
    The minimal battery state of charge (SoC) of the vehicle during the trip. It must be in the range [-inf, 1]. This
    value is calculated by a consumption model, e.g. the consumption model of the `ebustoolbox` package.
    """

    opportunity_charging: bool
    """
    Whether the vehicle is opportunity-charged (meaning charging at terminus stations) during the trip.
    """

    _is_copy: bool = False
    """
    Whether this vehicle schedule is a copy of another vehicle schedule. It should not be set manually, but only by
    calling the :meth:`repeat` method.
    """

    @classmethod
    def from_rotation(self, rot: Rotation, use_builtin_consumption_model: bool = False):
        """
        This constructor creates a VehicleSchedule object from a Rotation object. It is intended to be used by the
        eflips-depot API.

        :param rot: The Rotation object from which the VehicleSchedule is created.
        :param use_builtin_consumption_model: Whether to use the built-in consumption model of eflips-depot. If set
            to `True`, the `VehicleType.consumption` field is used. If set to `False`, consumption is calculated
            from the `Event` table in the database. This (and an external consumption model) is the recommended way.
        """

        id = str(rot.id)
        departure = rot.trips[0].departure_time
        arrival = rot.trips[-1].arrival_time

        if use_builtin_consumption_model:
            arrival_soc, departure_soc, minimal_soc = self.calculate_socs(rot)

        else:
            # Find the event for each trip
            events = []
            for trip in rot.trips:
                if len(trip.events) == 0:
                    raise ValueError(
                        f"Trip {trip.id} has no events. Has the energy consumption simulation been run?"
                    )
                elif len(trip.events) > 1:
                    raise ValueError(
                        f"Trip {trip.id} has more than one event. This is not supported."
                    )
                events.append(trip.events[0])

            departure_soc = events[0].soc_start
            arrival_soc = events[-1].soc_end
            minimal_soc = min([event.soc_end for event in events])

        opportunity_charging = rot.allow_opportunity_charging

        return VehicleSchedule(
            id=id,
            vehicle_type=str(rot.vehicle_type.id),
            departure=departure,
            arrival=arrival,
            departure_soc=departure_soc,
            arrival_soc=arrival_soc,
            minimal_soc=minimal_soc,
            opportunity_charging=opportunity_charging,
        )

    @classmethod
    def calculate_socs(cls, rot):
        if rot.vehicle_type.consumption is None:
            raise ValueError(
                f"Vehicle type {rot.vehicle_type.id} has no consumption value."
            )
        total_distance = 0
        for trip in rot.trips:
            total_distance += trip.route.distance
        energy = (total_distance / 1000) * rot.vehicle_type.consumption  # kWh
        delta_soc = energy / rot.vehicle_type.battery_capacity
        departure_soc = 1.0
        arrival_soc = departure_soc - delta_soc
        minimal_soc = arrival_soc
        return arrival_soc, departure_soc, minimal_soc

    def _to_simple_trip(
        self, simulation_start_time: datetime, env: simpy.Environment
    ) -> SimpleTrip:
        """
        This converts the vehicle schedule into a :class:`eflips.depot.standalone.SimpleTrip` object, which is the
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
            env,
            self.id,
            None,
            "DEFAULT",
            "DEFAULT",
            vehicle_types,
            departure,
            arrival,
            None,
            self.departure_soc,
            self.arrival_soc,
            self.opportunity_charging,
            is_copy=self._is_copy,
        )
        return simple_trip

    def repeat(self, interval: timedelta) -> "VehicleSchedule":
        """
        Repeats a given VehicleSchedule. Returns a new vehicle schdule offset by a given timedelta that has the
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
        This converts a list of VehicleSchedule objects into a :class:`eflips.depot.standalone.Timetable` object, which
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


def repeat_vehicle_schedules(
    vehicle_schedules: List[VehicleSchedule], repetition_period: timedelta
) -> List[VehicleSchedule]:
    """
    This method repeats the vehicle schedules in the list `vehicle_schedules` by the timedelta `repetition_period`.

    It takes the given vehicle schedules and creates two copies, one `repetition_period` earlier, one `repetition_period`
    later. It then returns the concatenation of the three lists.

    :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    :param repetition_period: A timedelta object specifying the period of the vehicle schedules.
    :return: a list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
    """
    # Add the repeated schedules to the forward and backward lists
    schedule_list_backward = []
    schedule_list_forward = []

    for vehicle_schedule in vehicle_schedules:
        schedule_list_backward.append(vehicle_schedule.repeat(-repetition_period))
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
