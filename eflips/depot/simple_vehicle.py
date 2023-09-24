# -*- coding: utf-8 -*-
"""
Created on Fri Oct 13 11:16:58 2017

@author: P.Mundt

Vehicle components for the depot simulation.

"""
from eflips.helperFunctions import flexprint
from eflips.settings import globalConstants
from eflips.evaluation import DataLogger
from eflips.depot.depot import DepotWorkingData
from eflips.depot.evaluation import BatteryLog


class VehicleType:
    """Definition of a vehicle type. Instantiated once per type. Vehicles of
    the same type hold references to the same VehicleType instance.

    Parameters:
    ID: [str]
    battery_capacity: [int or float] energy_nominal of battery.
        See SimpleBattery for info about other battery attributes.
    CR: [int or float] consumption rate of energy from the battery. kWh/km if
        distance-based or kW if time-based. eflips.settings.globalConstants[
        'depot']['consumption_calc_mode'] must be set accordingly. May be
        omitted if consumption calculation is not CR-based.

    Attributes:
    group: [VehicleTypeGroup or None] that the vehicle type is part of, if any.
    count: {dict} key: depot, value: number of vehicles with this type by
        depot
    share: {dict} key: depot, value: share of vehicles with this type by
        depot

    """

    def __init__(self, ID, battery_capacity, soc_min, soc_max, soc_init, soh, CR=None):
        self.ID = ID
        self.battery_capacity = battery_capacity
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.soc_init = soc_init
        self.soh = soh
        self.CR = CR
        self.group = None

        self.count = {}
        self.share = {}

    def __repr__(self):
        return "{%s} %s" % (type(self).__name__, self.ID)


class VehicleTypeGroup:
    """Hashable identifier for a list of vehicle types that are mutually
    substitutable on trips.

    Parameters:
    types: [list] of VehicleType objects. Can be empty upon instantiation.

    Attributes:
    count: {dict} key: depot, value: number of vehicles with this group by
        depot
    share: {dict} key: depot, value: share of vehicles with this group by
        depot

    """

    def __init__(self, types=None):
        self.types = types if types else []

        self.count = {}
        self.share = {}

    @property
    def vehicle_types_joinedstr(self):
        """Return the IDs of self.types comma-separated in a single string.
        Example returns:
        ['EN'] -> 'EN'
        ['EN', 'DL'] -> 'EN, DL'
        """
        return ", ".join(vt.ID for vt in self.types)


class SimpleVehicle:
    """Vehicle for the depot simulation.

    Parameters:
    env: [simpy.Environment]
    ID: [str] unique identifier such as vehicle number
    vehicle_type: [VehicleType]

    Attributes:
    trip: [SimpleTrip] current trip of the vehicle. None inside the depot.
    system_entry: [bool] False as long as the vehicle has not been used. True
        after.

    """

    def __init__(self, env, ID, vehicle_type, home_depot=None):
        self.env = env
        self.ID = ID
        self.vehicle_type = vehicle_type
        self.battery = SimpleBattery(
            env,
            vehicle_type.battery_capacity,
            vehicle_type.soc_min,
            vehicle_type.soc_max,
            vehicle_type.soc_init,
            vehicle_type.soh,
        )
        self.mileage = 0
        self.dwd = DepotWorkingData(env, self, home_depot)
        self.trip = None
        self.trip_at_departure = None
        self.finished_trips = []
        self.system_entry = False

        if globalConstants["general"]["LOG_ATTRIBUTES"]:
            self.logger = DataLogger(env, self, "VEHICLE")
            self.logger.loggedData["area_waiting_time"] = {}
            self.logger.loggedData["canceled_precondition"] = {}

        self.battery_logs = []  # Container for BatteryLog objects, temporary
        self.power_logs = {0: 0}  # Container for power logs

    def __repr__(self):
        return "{%s} %s" % (type(self).__name__, self.ID)

    def drive(self):
        """Process one trip.
        Simplifies everything that happens outside of the depot to consuming
        energy and letting time pass if the depot simulations runs on its own.
        Ends by calling checkin when arriving at the depot at the end of a
        trip.
        """
        flexprint(
            "t = %d: %s starting to drive with driving time = %d (= %f h)"
            % (
                self.env.now,
                self.ID,
                self.trip.duration,
                self.trip.duration / 3600,
            ),
            switch="operations",
        )

        self.battery_logs.append(BatteryLog(self.env.now, self, "consume_start"))
        yield self.env.timeout(self.trip.duration)

        if globalConstants["depot"]["consumption_calc_mode"] == "CR_distance_based":
            amount = self.trip.distance * self.vehicle_type.CR
            self.battery.get(amount)
        elif globalConstants["depot"]["consumption_calc_mode"] == "CR_time_based":
            amount = self.trip.duration / 3600 * self.vehicle_type.CR
            self.battery.get(amount)
        elif globalConstants["depot"]["consumption_calc_mode"] == "soc_given":
            if self.trip.charge_on_track:
                self.battery.energy = self.trip.end_soc * self.battery.energy_real
            else:
                used_energy = (
                    self.trip.start_soc - self.trip.end_soc
                ) * self.battery.energy_real
                self.battery.energy -= used_energy
        else:
            raise ValueError(
                "Invalid value %s for 'consumption_calc_mode' in globalConstants."
                % globalConstants["depot"]["consumption_calc_mode"]
            )

        self.battery_logs.append(BatteryLog(self.env.now, self, "consume_end"))

        # Driving time is over, now check in at the depot
        self.mileage += self.trip.distance if self.trip.distance is not None else 0
        self.trip.destination.checkin(self)


class SimpleBattery:
    """Battery for a vehicle.

    Parameters:
    energy_nominal = [float or int: kWh] nominal energy capacity. constant
    soc_min, soc_max: [float] usable energy range. 0...1
    soc_init: [float] initial state of charge within usage energy range
        (energy_real). 0...1
    soh: [float] state of health. 0...1

    Attributes:
    n_charges: number of times the battery has been charged
    active_processes: [list] of BaseDepotProcess or subclass objects that are
        currently modifying energy. These objects must implement and update
        method that modifies energy early.
    last_update: [int] sim time log to prevent recursion when retrieving energy
        and unnecessary updates.

    """

    def __init__(self, env, energy_nominal, soc_min, soc_max, soc_init, soh):
        if energy_nominal <= 0:
            raise ValueError("energy_nominal cannot be <= 0")
        if (
            not soc_input_valid(soc_min)
            or not soc_input_valid(soc_min)
            or not soc_input_valid(soc_min)
            or not soc_input_valid(soc_min)
        ):
            raise ValueError("soc values must be between 0 and 1")

        self.env = env
        self.energy_nominal = energy_nominal
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.soh = soh

        self._energy = soc_init * self.energy_real

        self.n_charges = 0
        self.active_processes = []
        self.updating = False
        self.last_update = 0

    @property
    def energy(self):
        """Return current energy level. Ask active processes for updates first."""
        if self.active_processes and self.last_update != self.env.now:
            self.last_update = self.env.now
            for process in self.active_processes:
                process.update_battery("update")

        return self._energy

    @energy.setter
    def energy(self, value):
        self._energy = value

    @property
    def energy_real(self):
        """Return current real energy capacity."""
        return self.energy_nominal * self.soh

    @property
    def soc(self):
        """Return current state of charge with regard to energy_real."""
        return self.energy / self.energy_real

    @property
    def energy_max(self):
        """Return maximum usable energy."""
        return self.energy_real * self.soc_max

    @property
    def energy_min(self):
        """Return minimum usable energy."""
        return self.energy_real * self.soc_min

    @property
    def energy_remaining(self):
        """Effective amount of energy usable by vehicle at the current state
        of charge."""
        return self.energy - self.energy_min

    def get(self, amount):
        """Subtract *amount* from self.energy.
        If energy would become negative and negativity is not allowed, set it
        to a given positive percentage of self.energy_real. Simple imitation of
        opportunity charging.
        """
        self._energy -= amount

        if self._energy < 0 and not globalConstants["depot"]["allow_negative_soc"]:
            self._energy = (
                globalConstants["depot"]["reset_negative_soc_to"] * self.energy_real
            )

    def put(self, amount):
        """Add *amount* to self.energy."""
        self._energy += amount


def soc_input_valid(soc):
    return 0 <= soc <= 1
