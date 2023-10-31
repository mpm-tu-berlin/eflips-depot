"""Read and pre-process data from database"""
import json
import numbers
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import ceil
from typing import Callable, Hashable, Optional, Dict, List, Union, Tuple, Any

import numpy as np
import seaborn as sns
import simpy
from matplotlib import pyplot as plt
from tqdm.auto import tqdm

import eflips.depot.standalone
from eflips.depot import Depot as EflipsDepot
from eflips.depot import DepotControl, DepotConfigurator
from eflips.depot.simple_vehicle import VehicleType as EflipsVehicleType, SimpleVehicle
from eflips.depot.standalone import SimpleTrip


@dataclass
class VehicleType:
    id: str
    """A unique identifier for this vehicle type. This identifier will be returned in the output of eFLIPS-Depot."""

    vehicle_class: str
    """A unique identifier for the vehicle class which this vehicle type belongs to."""

    battery_capacity_total: float
    """The total battery capacity of the vehicle in kWh. This is the gross capacity, which is actually quite a stupid
    value, but everybody does it that way. The practical capacity is the net capacity, which id returned in :meth:`net_battery_capacity`."""

    charging_curve: Union[
        Callable[[float], float],
        Tuple[List[float], List[float]],
        Dict[float, float],
        float,
    ]
    """
    The charging curve of the vehicle specifies the charging power as a function of the battery state of charge (SoC).
    
    We accept it in four different formats:
    - A function that takes the SoC as a float [0-1] and returns the charging power in kW.
    - A tuple of two lists. The first list contains the SoC values [0-1] and the second list contains the corresponding
    charging power values in kW. The resulting function is a piecewise linear interpolation between the points.
    - A dictionary mapping SoC values [0-1] to charging power values in kW. The resulting function is a piecewise linear
    interpolation between the points.
    - A float. The charging power is constant at this value over the whole SoC range.
    """

    v2g_curve: Optional[
        Union[
            Callable[[float], float],
            Tuple[List[float], List[float]],
            Dict[float, float],
            float,
        ]
    ] = None
    """
    The (optional) vehicle-to-grid (V2G) curve of the vehicle specifies the discharging power as a function of the b
    attery state of charge (SoC).
    
    We take the same formats as for the charging curve.
    """

    soc_max: float = 1.0
    """The maximum battery state of charge (SoC) of the vehicle. It must be in the range [0, 1]."""

    soc_min: float = 0.0
    """The minimum battery state of charge (SoC) of the vehicle. It must be in the range [0, 1] and smaller than `soc_max`."""

    soh: float = 1.0
    """The state of health (SoH) of the vehicle. It must be in the range [0, 1]."""

    def __post_init__(self):
        """
        This method is called after the object is initialized. It converts the charging curve and the V2G curve into
        functions, if they were provided in a different format.
        :return: Nothing
        """

        # Some sanity checks
        assert self.soc_min >= 0
        assert self.soc_max <= 1
        assert self.soc_min < self.soc_max

        assert self.soh >= 0
        assert self.soh <= 1

        # Convert the charging curve to a function
        if isinstance(self.charging_curve, Callable):
            pass
        elif isinstance(self.charging_curve, tuple):
            self._charge_soc_list = self.charging_curve[0]
            self._charge_power_list = self.charging_curve[1]
            self.charging_curve = self._interpolate_charging_curve
        elif isinstance(self.charging_curve, dict):
            self._charge_soc_list = list(self.charging_curve.keys())
            self._charge_power_list = list(self.charging_curve.values())
            self.charging_curve = self._interpolate_charging_curve
        elif isinstance(self.charging_curve, numbers.Number):
            self._const_charging_curve = float(self.charging_curve)
            self.charging_curve = lambda x: self._const_charging_curve
        else:
            raise ValueError("Invalid charging curve format")

        # Convert the V2G curve to a function
        if self.v2g_curve is None:
            pass
        elif isinstance(self.v2g_curve, Callable):
            pass
        elif isinstance(self.v2g_curve, tuple):
            self._v2g_soc_list = self.v2g_curve[0]
            self._v2g_power_list = self.v2g_curve[1]
            self.v2g_curve = self._interpolate_v2g_curve
        elif isinstance(self.v2g_curve, dict):
            self._v2g_soc_list = list(self.v2g_curve.keys())
            self._v2g_power_list = list(self.v2g_curve.values())
            self.v2g_curve = self._interpolate_v2g_curve
        elif isinstance(self.v2g_curve, numbers.Number):
            self._const_v2g_curve = float(self.v2g_curve)
            self.v2g_curve = lambda x: self._const_v2g_curve
        else:
            raise ValueError("Invalid V2G curve format")

    def _interpolate_charging_curve(self, soc: float) -> float:
        """Internal method to calculate the charging power from the charging curve."""
        return float(np.interp(soc, self._charge_soc_list, self._charge_power_list))

    def _interpolate_v2g_curve(self, soc: float) -> float:
        """Internal method to calculate the discharging power from the V2G curve."""
        return float(np.interp(soc, self._v2g_soc_list, self._v2g_power_list))

    @property
    def net_battery_capacity(self) -> float:
        """The net battery capacity of the vehicle in kWh. This is the battery capacity that is actually available for
        use. It is calculated as `battery_capacity_total * soh * (soc_max-soc_min)`."""
        return self.battery_capacity_total * self.soh * (self.soc_max - self.soc_min)

    def _to_eflips_vehicle_type(self) -> EflipsVehicleType:
        """
        This converts the VehicleType object into a :class:`depot.VehicleType` object, which is the input
        format of the depot simulation.

        :return: A :class:`depot.VehicleType` object.
        """

        # Create the depot VehicleType object
        eflips_vehicle_type = EflipsVehicleType(
            str(self.id),
            self.battery_capacity_total,
            self.soc_min,
            self.soc_max,
            1.0,
            self.soh,
            None,
        )

        return eflips_vehicle_type

    def _to_global_constants_dict(self) -> Dict[str, Any]:
        """
        This converts the VehicleType object into a dictionary, which is the input
        format of the eflips.globalConstants object.
        :return: A dictionary describing some of the properties of the vehicle type.
        """

        the_dict = {
            "battery_capacity": self.battery_capacity_total,
            "soc_min": self.soc_min,
            "soc_max": self.soc_max,
            "soc_init": 1.0,
            "soh": self.soh,
        }
        return the_dict


@dataclass
class VehicleSchedule:
    """
    This class represents a vehicle schedule in eFLIPS-Depot. A vehicle schedule presents everything a vehicle does
    between leaving the depot and returning to the depot. In eFLIPS-Depot, we only care about a reduced set of
    information, limited to the interaction with the depot.
    """

    id: str
    """Unique ID of this vehicle schedule. This identifier will be returned in the output of eFLIPS-Depot."""

    vehicle_class: Hashable
    """
    The vehicle class of this vehicle schedule. This should match the `vehicle_class` of the corresponding
    :class:`eflips.depot.api.input.VehicleType` objects.
    """

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

    arrival_soc: Dict[Hashable, float]
    """
    The battery state of charge (SoC) of the vehicles at the arrival time. It must be in the range [-inf, 1]. This value
    is calculated by a consumption model, e.g. the consumption model of the `ebustoolbox` package. It is a dictionary 
    mapping vehicle types to floats. The dictionary must contain an entry for each vehicle type that is part of the 
    `vehicle_class` of this vehicle schedule. 
    
    **NOTE**: For the current API version, we only support a single vehicle type per vehicle schedule. This means that
    the dictionary must contain exactly one entry.
    """

    minimal_soc: Dict[Hashable, float]
    """
    The minimal battery state of charge (SoC) of the vehicle during the trip. It must be in the range [-inf, 1]. This
    value is calculated by a consumption model, e.g. the consumption model of the `ebustoolbox` package. It may be
    left `None` if the consumption model does not provide this information.
    
    **NOTE**: For the current API version, we only support a single vehicle type per vehicle schedule. This means that
    the dictionary must contain exactly one entry.
    """

    opportunity_charging: bool
    """
    Whether the vehicle is opportunity-charged (meaning charging at terminus stations) during the trip.
    """

    def __post_init__(self):
        """
        The post-initialization method. It makes sure that the arrival SoC and minimal SoC dictionaries contain exactly
        one entry.
        :return: Nothing
        """

        assert len(self.arrival_soc) == 1
        assert len(self.minimal_soc) == 1

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

        vehicle_types = list(
            (self.arrival_soc.keys())
        )  # The vehicle type ids are the keys of the arrival_soc dictionary
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
            self.arrival_soc[vehicle_types[0]],
            self.opportunity_charging,
        )
        return simple_trip

    @staticmethod
    def visualize(vehicle_schedules: List["VehicleSchedule"]):
        """
        Helper method to visualize the vehicle schedules.
        :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
        :return: Nothing for now. May return the plot or offer to save it.
        """

        # Get a list of all vehicle classes
        vehicle_classes = set(
            [schedule.vehicle_class for schedule in vehicle_schedules]
        )
        vehicle_classes = sorted(list(vehicle_classes))
        palette = sns.color_palette("husl", len(vehicle_classes))
        colors_for_vehicle_classes = dict(zip(vehicle_classes, palette))

        # In order to plot the vehicle schedules in an appealing way, we need to solve a scheduling problem.
        # We want to minimize the number of rows in the plot, while making sure that no two vehicle schedules overlap.
        # We create a list for each row and then choose the next vehicle schedule to be in the row with the earliest
        # departure time that does not overlap with any other vehicle schedule in that row.
        vehicle_schedules = sorted(vehicle_schedules, key=lambda x: x.departure)
        progress = tqdm(total=len(vehicle_schedules))
        rows = []
        while len(vehicle_schedules) > 0:
            row = []
            row.append(vehicle_schedules.pop(0))
            schedule_copy = vehicle_schedules.copy()
            while len(schedule_copy) > 0:
                schedule = schedule_copy.pop(0)
                if schedule.departure >= row[-1].arrival:
                    row.append(schedule)
                    vehicle_schedules.remove(schedule)
                    progress.update(1)
            rows.append(row)

        plot_data = []
        for row in rows:
            plot_data.append([])
            for entry in row:
                plot_data[-1].append(
                    {
                        "start": entry.departure,
                        "end": entry.arrival,
                        "color": colors_for_vehicle_classes[entry.vehicle_class],
                    }
                )

        # Create the plot
        fig, ax = plt.subplots(figsize=(10, 5))
        for row in plot_data:
            for entry in row:
                ax.broken_barh(
                    [(entry["start"], entry["end"] - entry["start"])],
                    (plot_data.index(row), 1),
                    facecolors=entry["color"],
                )
        plt.show()
        plt.close()

    @staticmethod
    def _to_timetable(
        vehicle_schedules: List["VehicleSchedule"], env: simpy.Environment
    ) -> eflips.depot.standalone.Timetable:
        """
        This converts a list of VehicleSchedule objects into a :class:`eflips.depot.standalone.Timetable` object, which
        is the input format of the depot simulation. This Timetable object is part of the "black box" not covered by
        the API documentation.

        :param vehicle_schedules: A list of :class:`eflips.depot.api.input.VehicleSchedule` objects.
        :param env: The simulation environment object. It should be the `env` of the SimulationHost object.
        :return:
        """

        # Sort the vehicle schedules by departure time
        vehicle_schedules = sorted(vehicle_schedules, key=lambda x: x.departure)

        # Find the first departure time
        first_departure = vehicle_schedules[0].departure
        start_of_simulation = first_departure.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Convert the vehicle schedules into SimpleTrip objects
        simple_trips = []
        for vehicle_schedule in vehicle_schedules:
            simple_trip = vehicle_schedule._to_simple_trip(start_of_simulation, env)
            simple_trips.append(simple_trip)

        # Create the Timetable object
        total_interval = (
            vehicle_schedules[-1].arrival - vehicle_schedules[0].departure
        ).total_seconds()
        days_ahead = (
            ceil(total_interval / 86400) + 1
        )  # Apparently, that's what the TimeTable documentation wants
        timetable = eflips.depot.standalone.Timetable(env, simple_trips, days_ahead)

        return timetable


@dataclass
class Depot:
    """This class represent a depot in eFLIPS-Depot. A vehicle arrives at a depot, is processed there and leaves the
    depot again. In eFLIPS-Depot the processes within a depot is simulated"""

    id: Hashable
    """A unique identifier of this depot."""

    plan: "Plan"
    """The default plan for this depot, representing a series of processes that are executed for all the 
    vehicles at the depot, if there are no requirements of specific plans."""

    areas: List["Area"]
    """This list represents the areas in this depot, where vehicles can take part in processes."""

    name: str | None = None
    """The name of this depot. It is a human-readable string that will be returned in the output of eFLIPS-Depot."""

    plan_override: Optional[Callable[[DepotControl, SimpleVehicle], "Plan"]] = None
    """A function that takes a :class:`eflips.depot.api.input.DepotControl` object and a
    :class:`eflips.depot.standalone.SimpleVehicle` object and returns a :class:`eflips.depot.api.input.Plan` object. It
    will be called at the assign_plan when the vehicle arrives at the depot. It can be used to dynamically assign a
    plan to a vehicle. **Note**: This function takes the "black box" :class:`eflips.depot.api.input.DepotControl` and 
    :class:`eflips.depot.standalone.SimpleVehicle` objects as input, as such it is not covered by the API documentation.
    """

    def _to_eflips(self) -> Union[EflipsDepot, DepotConfigurator]:
        """Placeholder method to convert the depot into a :class:`eflips.depot.api.input.Depot` object, which is the
        input format of the depot simulation. We may convert to a depotConfigurator instead or use the depotConfigurator.
        """
        raise NotImplementedError

    def _to_template(self) -> Dict:
        """Convert Depot to a dictionary of the format in eFLIPS.Depot"""
        # convert depot to the dict format corresponding to the original JSON template
        # validate
        # use DepotConfigurator.load() to load it into eFLIPS-Depot

        template = {
            "templatename_display": "Depot KLS Sept19 all direct",
            "general": {"depotID": "", "dispatch_strategy_name": ""},
            "resources": {},
            "resource_switches": {},
            "processes": {},
            "areas": {},
            "groups": {},
            "plans": {},
        }

        # Placeholder

        template["general"]["depotID"] = "DEFAULT"

        # TODO: do we need a Strategy in Depot later?
        template["general"]["dispatch_strategy_name"] = "SMART"

        # Helper for adding processes to the template
        list_of_processes = []

        # Get dictionary of each area
        for area in self.areas:
            template["areas"][area.name] = {
                "typename": (
                    "LineArea" if area.type == AreaType.LINE else "DirectArea"
                ),
                # "amount": 2,  # TODO Check how multiple areas work. For now leave it as default
                "capacity": area.capacity,
                "available_processes": [
                    process.name for process in area.available_processes
                ],
                "issink": False,
                "entry_filter": None,
            }

            # Fill in vehicle_filter. Now this is only a placeholder
            if (
                area.vehicle_classes is not None
            ):  # TODO Lu: The Vehicle class thing still needs some rework. Here, we directly depend on the database
                # in ebustoolbox, which i don't really want
                template["areas"][area.name]["entry_filter"] = {
                    "filter_names": ["vehicle_type"],
                    "vehicle_types": ["SB_DC"],  # TODO: get correct types via database
                }

            for process in area.available_processes:
                # Add process into process list
                list_of_processes.append(
                    process
                ) if process not in list_of_processes else None

                # Charging interfaces
                if process.type == ProcessType.CHARGING:
                    ci_per_area = []
                    for i in range(area.capacity):
                        ID = "ci_" + str(len(template["resources"]))
                        template["resources"][ID] = {
                            "typename": "DepotChargingInterface",
                            "max_power": process.electric_power,
                        }
                        ci_per_area.append(ID)

                    template["areas"][area.name]["charging_interfaces"] = ci_per_area

                # Set issink to True for departure areas
                if process.type == ProcessType.STANDBY_DEPARTURE:
                    template["areas"][area.name][
                        "issink"
                    ] = True  # TODO LU: Can a vehicle go on from an area that is a sink?

        for process in list_of_processes:
            # Shared template for all processes
            template["processes"][process.name] = {
                "typename": "",  # Placeholder for initialization
                "dur": process.duration,
                # True if this process will be executed for all vehicles. False if there are available vehicle filters
                "ismandatory": True,
                "vehicle_filter": {},
                # True if this process can be interrupted by a dispatch. False if it cannot be interrupted
                "cancellable_for_dispatch": False,
            }

            match process.type:
                case ProcessType.SERVICE:
                    template["processes"][process.name]["typename"] = "Serve"
                    if process.availability is not None:
                        template["processes"][process.name]["vehicle_filter"] = {
                            "filter_names": ["in_period"],
                            "period": process.availability,
                        }

                    # Fill in workers_service of resources
                    service_capacity = sum([x.capacity for x in process.areas])

                    # Fill in the worker_service
                    template["resources"]["workers_service"] = {
                        "typename": "DepotResource",
                        "capacity": service_capacity,
                    }

                case ProcessType.CHARGING:
                    template["processes"][process.name]["typename"] = "Charge"
                    del template["processes"][process.name]["dur"]

                case ProcessType.STANDBY | ProcessType.STANDBY_DEPARTURE:
                    template["processes"][process.name]["typename"] = "Standby"
                    template["processes"][process.name]["dur"] = 0
                    # template["processes"][process.name]["ismandatory"] = True

                case ProcessType.PRECONDITION:
                    template["processes"][process.name]["typename"] = "Precondition"
                    template["processes"][process.name]["dur"] = process.duration
                    template["processes"][process.name][
                        "power"
                    ] = process.electric_power
                case _:
                    raise ValueError(f"Invalid process type: {process.type.name}")

        # Initialize the default plan
        template["plans"]["default"] = {
            "typename": "DefaultActivityPlan",
            "locations": [],
        }
        # Groups
        for process in self.plan.processes:
            group_name = str(process.type) + "_group"
            template["groups"][group_name] = {
                "typename": "AreaGroup",
                "stores": [area.name for area in process.areas],
            }
            if process.type == ProcessType.STANDBY_DEPARTURE:
                template["groups"][group_name]["typename"] = "ParkingAreaGroup"
                template["groups"][group_name]["parking_strategy_name"] = "SMART2"

            # Fill in locations of the plan
            template["plans"]["default"]["locations"].append(group_name)

        # TODO dump to json for test purposes. will be removed later

        file_path = os.path.dirname(__file__)
        tmp_output_path = os.path.join(file_path, "tmp_output.json")
        with open(tmp_output_path, "w") as f:
            json.dump(template, f, indent=4)

        return template

    def validate(self):
        """
        This method cehcks for validity of the depot. Specifically
        - it makes sure that all processes in the plan are available in at least one area (*note: plan_override is not checked*)
        - it makes sure that all areas have at least one process available
        - it makes sure that (at least) the last process in the plan is dispatchable
        - it makes sure that the bidirectional links between e.g. areas and processes are set up correctly

        :return: Nothing. Raises an AssertionError if the depot is invalid.
        """
        for process in self.plan.processes:
            assert any(
                process in area.available_processes for area in self.areas
            ), "All processes in the plan must be available in at least one area."

        for area in self.areas:
            assert (
                len(area.available_processes) > 0
            ), "All areas must have at least one process available."

        assert self.plan.processes[
            -1
        ].dispatchable, "The last process in the plan must be dispatchable."

        for area in self.areas:
            assert area.depot == self, "The depot of an area must be set correctly."
            for process in area.available_processes:
                assert (
                    process in self.plan.processes
                ), "All processes in an area must be part of the plan."
                assert (
                    area in process.areas
                ), "All areas in an area must be part of the area."


class AreaType(Enum):
    """This class represents the type of an area in eFLIPS-Depot"""

    DIRECT_ONESIDE = 1
    """A direct area where vehicles drive in form one side only."""

    DIRECT_TWOSIDE = 2
    """A direct area where vehicles drive in form both sides. Also called a "herringbone" configuration."""

    LINE = 3
    """A line area where vehicles are parked in a line. There might be one or more rows in the area."""


@dataclass
class Area:
    """This class represents an area in eFLIPS-Depot, where a vehicle can be processed."""

    id: Hashable
    """A unique identifier of this area."""

    type: AreaType
    """The type of the area. See :class:`eflips.depot.api.input.AreaType` for more information."""

    depot: Depot
    """The depot this area belongs to."""

    capacity: int
    """The maximum number of vehicles that can be processed in this area at the same time.
    - For a LINE area, it must be evenly divisible by the row_count
    - For a DIRECT_ONESIDE area it can be freely chosen
    - For a DIRECT_TWOSIDE area it must be a multiple of two.
    """

    available_processes: List["Process"]
    """This list represents the processes that can be executed in this area."""

    vehicle_classes: List["VehicleClass"] | None = None
    """
    This list represents the vehicle classes that can be allowed to enter this area. This will also be used
    for size calculation. The sizing of the area will make sure it fits the largest vehicle type in all vehicle classes.
    If this is `None`, then all vehicle classes are allowed. The sizing will be done based on the largest vehicle type in
    the database.
    """

    name: str | None = None
    """The name of this area. It is a human-readable string that will be returned in the output of eFLIPS-Depot."""

    row_count: Optional[int] = None
    """For a line area, this is the number of rows in the area. It must be an integer greater than 0 that evenly
    divides the capacity of the area."""

    def __post_init__(self):
        """
        This method is called after the object is initialized. It makes sure that the capacity is valid for the given
        area type
        :return: Nothing
        """

        match self.type:
            case AreaType.DIRECT_ONESIDE:
                assert self.capacity > 0
            case AreaType.DIRECT_TWOSIDE:
                assert self.capacity > 0 and self.capacity % 2 == 0
            case AreaType.LINE:
                assert (
                    (self.capacity > 0 and self.row_count is not None)
                    and (self.row_count > 0)
                    and (self.capacity % self.row_count == 0)
                )


class ProcessType(Enum):
    """This class represents the types of a process in eFLIPS-Depot."""

    SERVICE = 1
    """This process represents a bus service by workers. It does not require a charging_power and has a fixed 
    duration."""
    CHARGING = 2
    """This process represents a bus charging process. It requires a charging_power and has no fixed duration."""
    STANDBY = 3
    """This process represents an arriving bus that is waiting for a service. It does not require a charging_power 
    and has no fixed duration."""
    STANDBY_DEPARTURE = 4
    """This process represents a bus ready for departure. It does not require a charging_power and has no fixed 
    duration."""
    PRECONDITION = 5
    """This process represents a bus preconditioning process. It requires a charging_power and has a fixed duration."""


@dataclass
class Process:
    """This class represents a process in eFLIPS-Depot, which is the possible actions for a vehicle in a depot."""

    id: Hashable
    """The unique identifier of this process."""

    dispatchable: bool
    """
    Whether the bus can be dispatched (assigned to a line or a service) during this process. If it is not, then the 
    process must be completed before the vehicle can be dispatched. At least the last process in a plan must be 
    dispatchable.
    """

    areas: List[Area]
    """This list represents the areas where this process can be executed."""

    name: str | None = None
    """The name of this process. It is a human-readable string that will be returned in the output of eFLIPS-Depot."""

    electric_power: Optional[float] = None
    """If this process requires power, this is the power in kW that is required. It must be a positive float."""

    duration: Optional[int] = None
    """If this process has a fixed duration, this is the duration in seconds. It must be a positive integer."""

    availability: Optional[Tuple[int, int]] = None
    """If this process is only available during a certain time period, this is the time period in seconds. It must be a tuple of two integers."""

    def __post_init__(self):
        """
        After initializing a class, we need to check that the duration and charging power are valid and
        correspond to the process type.

        :return: Nothing
        """

        if self.electric_power is not None:
            assert isinstance(self.electric_power, float) and self.electric_power > 0.0

        if self.duration is not None:
            assert isinstance(self.duration, int) and self.duration >= 0

    @property
    def type(self) -> ProcessType:
        """
        The type of the process. See :class:`eflips.depot.api.input.ProcessType` for more information. Note that whether
        a process needs a resource or not depends on the type of the process.
        """
        if self.duration is not None and self.electric_power is None:
            return ProcessType.SERVICE
        elif self.duration is None and self.electric_power is not None:
            return ProcessType.CHARGING
        elif self.duration is not None and self.electric_power is not None:
            return ProcessType.PRECONDITION
        elif self.duration is None and self.electric_power is None:
            if self.dispatchable:
                return ProcessType.STANDBY_DEPARTURE
            else:
                return ProcessType.STANDBY
        else:
            raise ValueError("Invalid process type")

    def _validate(self):
        # TODO see if we need to add a validate method
        pass


@dataclass
class Plan:
    """This class represents a plan in eFLIPS-Depot. A plan is a series of processes, where a vehicle must be
    processed in this exact order."""

    id: Hashable
    """A unique identifier of this plan."""

    processes: List[Process]
    """This list represents the processes that are executed in order for the vehicles belonging to this plan."""
