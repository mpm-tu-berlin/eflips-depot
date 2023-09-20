"""Read and pre-process data from database"""
import json
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import Callable, Hashable, Optional, Dict, List, Union, Tuple

import numpy as np
import simpy

import eflips.depot.standalone
from depot import VehicleType
from eflips.depot.standalone import SimpleTrip


@dataclass
class VehicleType:
    id: Hashable
    """A unique identifier for this vehicle type. This identifier will be returned in the output of eFLIPS-Depot."""

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
        elif isinstance(self.charging_curve, float):
            self._const_charging_curve = self.charging_curve
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
        elif isinstance(self.v2g_curve, float):
            self._const_v2g_curve = self.v2g_curve
            self.v2g_curve = lambda x: self._const_v2g_curves
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

    def _to_vehicle_type(self) -> VehicleType:
        """
        This converts the VehicleTypeFromDatabase object into a :class:`depot.VehicleType` object, which is the input
        format of the depot simulation.

        :return: A :class:`depot.VehicleType` object.
        """
        raise NotImplementedError  # TODO


@dataclass
class VehicleSchedule:
    """
    This class represents a vehicle schedule in eFLIPS-Depot. A vehicle schedule presents everything a vehicle does
    between leaving the depot and returning to the depot. In eFLIPS-Depot, we only care about a reduced set of
    information, limited to the interaction with the depot.
    """

    id: Hashable
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
    The battery state of charge (SoC) of the vehicle at the arrival time. It must be in the range [-inf, 1]. This value
    is calculated by a consumption model, e.g. the consumption model of the `ebustoolbox` package. It is a dictionary 
    mapping vehicle types to floats. The dictionary must contain an entry for each vehicle type that is part of the 
    `vehicle_class` of this vehicle schedule.
    """

    minimal_soc: Optional[float]
    """
    The minimal battery state of charge (SoC) of the vehicle during the trip. It must be in the range [-inf, 1]. This
    value is calculated by a consumption model, e.g. the consumption model of the `ebustoolbox` package. It may be
    left `None` if the consumption model does not provide this information.
    """

    opporunity_charging: bool
    """
    Whether the vehicle is opportunity-charged (meaning charging at terminus stations) during the trip.
    """

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

        vehicle_types = (
            self.arrival_soc.keys()
        )  # The vehicle type ids are the keys of the arrival_soc dictionary
        departure = int((self.departure - simulation_start_time).total_seconds())
        arrival = int((self.arrival - simulation_start_time).total_seconds())
        simple_trip = SimpleTrip(
            env,
            self.id,
            None,
            None,
            None,
            vehicle_types,
            departure,
            arrival,
            None,
            self.departure_soc,
            self.arrival_soc,
            self.opporunity_charging,
        )
        return simple_trip

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


class Depot:
    """
    This class represents a depot in eFLIPS-Depot. A depot is a place where vehicles can be charged. It is **WIP**.
    """

    pass
