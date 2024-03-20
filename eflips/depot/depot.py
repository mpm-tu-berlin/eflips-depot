# -*- coding: utf-8 -*-
"""
Created on Fri Oct 13 11:12:00 2017.

@author: P.Mundt, E.Lauth

Core components of the depot simulation model.
"""

from collections import Counter
import simpy
from simpy.resources.store import StorePut
from simpy.core import BoundClass
from simpy.util import start_delayed
from eflips.evaluation import DataLogger
from eflips.settings import globalConstants
from eflips.helperFunctions import flexprint, SortedList
from eflips.simpy_ext import (
    FilterStoreExt,
    PositionalFilterStore,
    StorePutExt,
    LineFilterStore,
    StoreConnector,
    FilterStoreExtGet,
    LineStorePut,
    LineFilterStoreGet,
    ExclusiveRequest,
)
from eflips.depot.processes import EstimateValue, ChargeAbstract, Precondition
from eflips.depot.resources import DepotChargingInterface
from eflips.depot.filters import VehicleFilter
from eflips.depot.rating import (
    SlotAlternative,
    ParkRating,
    VehicleAlternative,
    DispatchRating,
)
from eflips.depot.evaluation import Departure, ProcessCalled
from abc import ABC, abstractmethod
from warnings import warn


class DepotWorkingData:
    """Data container for communication between vehicle and depot.

    Attribute of a SimpleVehicle object.

    Parameters:
    home_depot: [Depot] which the vehicle was instantiated in

    Attributes:
    plan: [ActivityPlan] List of DepotArea or AreaGroup objects that the
        vehicle will visit during the stay in a specific depot.
    current_area: [BaseArea subclass] where the vehicle is located at inside
        the depot. None outside.
    active_processes: [list] for statistics, contains BaseDepotProcess
        or subclass objects that are currently active and not on hold
    any_active_processes: [bool] True if any BaseDepotProcess is currently
        active. Not the same as bool(active_processes) because
        any_active_processes is also True if processes are on hold, e.g. when
        interrupted and waiting for restart.
    on_hold: [bool] flag used to prevent vehicles from being eligible for
        departure between entering an area and starting processes
    """

    def __init__(self, env, vehicle, home_depot=None):
        self.env = env
        self.vehicle = vehicle
        self.home_depot = home_depot
        self.service_need = False
        self.t_lastServed = 0
        self.repair_need = False
        self.maintenance_need = False
        self.plan = None
        self.current_area = None
        self._current_slot = None
        self.previous_area = None
        self.current_depot = None
        self.active_processes = []
        self.any_active_processes = False
        self.on_hold = False

    @property
    def active_processes_copy(self):
        """Snapshot of self.active_processes for logging."""
        return self.active_processes.copy()

    @property
    def current_slot(self):
        """Return slot number [int] if on an area, else None.

        The lowest slot
        number is 0. Note that slot_no is not the list index.
        """
        if self.current_area is not None:
            return self.current_area.slot_no(self.vehicle)
        else:
            return None

    @property
    def current_charging_interface(self):
        """Return [ChargingInterface] object of current slot on current area,.

        if any, else None.
        """
        if self.current_area is not None and self.current_area.charging_interfaces:
            i = self.current_area.items.index(self.vehicle)
            return self.current_area.charging_interfaces[i]
        else:
            return None

    @property
    def etc_processes(self):
        """Return simulation time estimate [int or EstimateValue] of when all.

        currently active processes will be completed.
        """
        etcs = [process.etc for process in self.active_processes]
        if not etcs:
            # no active processes
            return EstimateValue.COMPLETED
        elif EstimateValue.UNKNOWN in etcs:
            # At least one estimate is unknown, therefore the summarized
            # estimate is unknown as well
            return EstimateValue.UNKNOWN
        elif all(etc is EstimateValue.COMPLETED for etc in etcs):
            # All processes are completed
            return EstimateValue.COMPLETED
        else:
            # tODO figure out if duration must be an int?
            return max(
                [
                    etc
                    for etc in etcs
                    if (isinstance(etc, int) or isinstance(etc, float))
                ]
            )

    @property
    def etc_processes_uncertain(self):
        """Return estimate [int or EstimateValue] of when all currently active.

        processes will be completed. Unlike self.etc_processes, get a rough
        estimate if an etc value is EstimateValue.UNKNOWN.
        """
        etcs = [process.etc for process in self.active_processes]
        if not etcs:
            # no active processes
            return EstimateValue.COMPLETED
        elif EstimateValue.UNKNOWN in etcs:
            for idx, etc in enumerate(etcs):
                if etc is EstimateValue.UNKNOWN:
                    etcs[idx] = self.active_processes[idx].estimate_duration()
            # At least one estimate is unknown, therefore the summarized
            # estimate is unknown as well
            return EstimateValue.UNKNOWN
        elif all(etc is EstimateValue.COMPLETED for etc in etcs):
            # All processes are completed
            return EstimateValue.COMPLETED
        else:
            return max([etc for etc in etcs if isinstance(etc, int)])

    @property
    def isrfd(self):
        """Return True if this vehicle is ready for departure (rfd).

        A vehicle is rfd if it's located at a sink (parking area) and has no
        active processes except Precondition. A blocked vehicle at LineArea may
        be rfd (see LineStore.isunblocked for this filter). Cancellation of
        processes is not considered for the rfd state because this depends on
        trip requirements.
        """
        return (
            self.current_area is not None
            and self.current_area.issink
            and all(isinstance(proc, Precondition) for proc in self.active_processes)
        )


class BackgroundStorePut(StorePut):
    """Interface for customization of put-events."""

    def __init__(self, store, item):
        super(BackgroundStorePut, self).__init__(store, item)

        # Logging
        if (
            hasattr(store, "logger")
            and globalConstants["general"]["LOG_ATTRIBUTES"]
            and globalConstants["general"]["LOG_SPECIFIC_STEPS"]
        ):
            self.callbacks.append(store.logger.steplog)


class BackgroundStoreGet(FilterStoreExtGet):
    """Interface for customization of get-events."""

    def __init__(self, store, filter=lambda item: True, **kwargs):
        super(BackgroundStoreGet, self).__init__(store, filter, **kwargs)

        # Logging
        if (
            hasattr(store, "logger")
            and globalConstants["general"]["LOG_ATTRIBUTES"]
            and globalConstants["general"]["LOG_SPECIFIC_STEPS"]
        ):
            self.callbacks.append(store.logger.steplog)


class BackgroundStore(FilterStoreExt):
    """Virtual area for background tasks that require functionalities such as.

    logging.
    """

    def __init__(self, env, ID, capacity=float("inf")):
        super(BackgroundStore, self).__init__(env, capacity)
        self.env = env
        self.ID = ID

    put = BoundClass(BackgroundStorePut)
    get = BoundClass(BackgroundStoreGet)

    def select(self, ID, print_missing=True):
        """Return the item with *ID* in self.items.

        Return None if the ID is
        not found.
        Relies on *ID* to be unique.

        ID: [str]
        print_missing: [bool] print a message if the item is not found.
        """
        item = next((item for item in self.items if item.ID == ID), None)
        if item is None and print_missing:
            print("Cannot find item with ID '%s'." % ID)
        return item


class UnassignedTrips(SortedList):
    """Subclass of SortedList with specific logging upon modification."""

    def append(self, trip):
        super().append(trip)
        trip.origin.evaluation.log_sl()

    def extend(self, trips):
        super().extend(trips)
        if trips:
            trips[0].origin.evaluation.log_sl()

    def remove(self, trip):
        super().remove(trip)
        trip.origin.evaluation.log_sl()

    def pop(self, index=-1):
        item = super().pop(index)
        item.origin.evaluation.log_sl()
        return item


class Depot:
    """Representation of a depot.

    Owns all related objects such as areas
    and the DepotControl as attributes.
    A depot must be created and configured using a DepotConfigurator and may
    be empty upon instantation. Before simulation start, the validity of the
    configuration must be checked (DepotConfigurator.isvalid) and the depot
    init completed (DepotConfigurator.complete).

    Parameters:
    env: [simpy.Environment] object
    ID: [str] unique name of the depot. Must match the depot name in timetable
        and vehicle data. Examples: 'I', 'M'

    Attributes:
    evaluation: [eflips.depotEvaluation.DepotEvaluation] instance
    timetable: [eflips.depotStandalone.Timetable] that issues trip requests at
        the depot. May be None until simulation start.

    Attributes set by DepotConfigurator:
    resources: [dict] containing all DepotResource objects related to this
        depot with their IDs as key.
    resource_switches: [dict] containing all ResourceSwitch objects related to
        this depot with their IDs as key.
    processes: [dict] containing dicts with data for instantiation of all
        BaseDepotProcess subclasses related to this depot with their IDs as key
    areas: [dict] containing all BaseArea subclass objects (excluding
        BackgroundStore) related to this depot with their IDs as key.
    groups: [dict] containing all AreaGroup and ParkingAreaGroup objects
        related to this depot with their IDs as key.
    default_plan: [ActivityPlan] object that permits all vehicles.
    specific_plans: [dict] of ActivityPlan objects for specific vehicles
        with plan ID as key. Matches precede the default plan.
    parking_area_groups: [list] of ParkingAreaGroup objects from self.groups.
    list_areas: [dict_values] iterable for list-like access. Do not modify
        directly; is updated automatically through self.areas.
    direct_departure_areas: [list] of DirectArea objects from self.areas that
        are also a part of a parking area group, i.e. vehicles can depart from
    list_spec_plans: [dict_values] iterable for list-like access to values of
        specific_plans. Do not modify directly; is updated automatically
        through self.specific_plans.
    capacity: [int] total amount of slots on areas.
    parking_capacity: [int] total amount of slots on areas in parking area
        groups.
    parking_capacity_direct: [int] total amount of slots on Direct areas in
        parking area groups (total buffer capacity)

    Runtime attributes:
    init_store: [BackgroundStore] where vehicles that have this depot as
        home depot are put in by VehicleGenerator before simulation start and
        retrieved during the simulation.
    pending_departures: [list] of SimpleTrip objects for departures that may
        have a vehicle assigned to, but haven't started yet and are not
        supposed to be served by vehicles from self.init_store.
    unassigned_trips: [list] of trips in pending_departures that have no
        scheduled vehicle.
    pending_arrivals: [list] of SimpleTrip objects on which a vehicle is
        currently on it's way to this depot. Sorted by estimated time of
        arrival (atd) as long as atd doesn't change during the trip.
    any_process_cancellable_for_dispatch: [bool] True if at least one process
        in self.processes is cancellable for dispatch.
    """

    def __init__(self, env, ID):
        self.env = env
        self.ID = ID

        self.evaluation = None
        self.timetable = None  # to be set when timetable is created

        self.resources = {}
        self.resource_switches = {}
        self.processes = {}  # no process objects, only prepared data
        self.areas = {}
        self.groups = {}
        self.default_plan = None
        self.specific_plans = {}

        self.parking_area_groups = []
        self.list_areas = self.areas.values()
        self.direct_departure_areas = []
        self.list_spec_plans = self.specific_plans.values()

        self.capacity = 0
        self.parking_capacity = 0
        self.parking_capacity_direct = 0

        self.init_store = BackgroundStore(env, "init")

        # if globalConstants['general']['LOG_ATTRIBUTES']:
        #     self.logger = DataLogger(env, self, 'DEPOT')

        self.depot_control = DepotControl(env, self)

        self.pending_departures = []
        self.unassigned_trips = UnassignedTrips(key="std")
        self.pending_arrivals = SortedList(key="eta")
        self.any_process_cancellable_for_dispatch = False

        self.checkins = 0
        self.checkouts = 0
        self._count = 0
        self._max_count = 0

        self._total_power = 0

    @property
    def total_power(self):
        """Current power of all active charging interfaces in the depot."""
        return self._total_power

    @total_power.setter
    def total_power(self, value):
        self._total_power = value
        self.evaluation.power_logs[self.env.now] = self.total_power

    def update_power(self, value):
        """Add *value* to depot-wide power value and log."""
        self.total_power += value

    @property
    def vacant(self):
        """Return the total sum of slots that are unoccupied in the depot."""
        return sum(area.vacant for area in self.list_areas)

    @property
    def vacant_accessible(self):
        """Return the sum of slots that are unoccupied and accessible.

        (not blocked) from the default entrance side on all areas.
        """
        return sum(area.vacant_accessible for area in self.list_areas)

    @property
    def count(self):
        """Return the amount of vehicles in the depot, i.e. the amount of.

        occupied slots.
        """
        return self._count

    @count.setter
    def count(self, value):
        self._count = value
        if value > self._max_count:
            self._max_count = value

    @property
    def max_count(self):
        """Return the maximum number of vehicles that were in the depot at the.

        same time.
        """
        return self._max_count

    @property
    def maxOccupiedSlots(self):
        """Return the total number of slots that have been occupied up to this.

        point of time. Only functional if the simulation is run with the GUI.
        """
        totalUsedSlots = 0
        for area in self.list_areas:
            totalUsedSlots = totalUsedSlots + area.maxOccupiedSlots
        return totalUsedSlots if totalUsedSlots > -1 else -1

    @property
    def urgent_trips(self):
        """Return a list of trips that are urgent: due or delayed and no.

        vehicle assigned to.
        Relies on self.unassigned_trips to be sorted by std.
        """
        result = []

        for trip in self.unassigned_trips:
            if trip.std <= self.env.now:
                result.append(trip)
            else:
                break

        return result

    @property
    def overdue_trips(self):
        """Return a list of trips that are overdue, i.e. have not started yet.

        although the target departure time has passed. Includes trips that have
        a scheduled vehicle.
        """
        return [
            trip
            for trip in self.pending_departures
            if trip.atd is None and trip.std < self.env.now
        ]

    def checkin(self, vehicle):
        """Redirect to checkin() of the depot's depot_control.

        Don't add code here.
        """
        self.depot_control.checkin(vehicle)

    def checkout(self, vehicle):
        """Redirect to checkout() of the depot's depot_control.

        Don't add code here.
        """
        self.depot_control.checkout(vehicle)

    def request_vehicle(self, trip, filter=lambda item: True):
        """Redirect to request_vehicle of the depot's depot_control."""
        self.depot_control.request_vehicle(trip, filter=filter)


class BaseDispatchStrategy(ABC):
    """Base class for a dispatch strategy for matching trips and vehicles.

    Attributes:
    name: [str] identifier used internally and for configuration
    short_description: [str] for frontend
    tooltip: [str] short explanation for frontend
    """

    name = None
    short_description = None
    tooltip = None

    @staticmethod
    @abstractmethod
    def trigger(*args, **kwargs):
        """Trigger the matching process."""

    @staticmethod
    def next_trips(depot):
        """Return depot.urgent_trips, if not empty.

        Else return a list
        containing only the unassigned trip with the lowest scheduled time of
        departure (std), if any. Otherwise return an empty list.
        Relies on depot.unassigned_trips to be sorted by std.
        """
        if not depot.unassigned_trips:
            return []

        result = depot.urgent_trips

        if not result:
            # Determine first unassigned trip
            result = [depot.unassigned_trips[0]]

        return result

    @staticmethod
    def assign(vehicle, trip, depot):
        """Match *vehicle* and *trip*."""
        env = depot.env
        # flexprint(
        #     'Vehicle %s in assign. Matched with trip %s'
        #     % (vehicle.ID, trip.ID), env=depot.env, switch='objID',
        #     objID=vehicle.ID)
        # flexprint(
        #     'Vehicle %s in assign. Matched with trip %s'
        #     % (vehicle.ID, trip.ID), env=depot.env, switch='objID',
        #     objID=trip.ID)

        # flexprint("Matching vehicle '%s' at area %s and trip '%s' (current vehicle=%s, std=%d, vehicle_types=%s)."
        #           % (vehicle.ID, vehicle.dwd.current_area.ID, trip.ID, trip.vehicle.ID if trip.vehicle is not None else None,
        #              trip.std, [vt.ID for vt in trip.vehicle_types]), env=trip.env)

        if vehicle.dwd.any_active_processes:
            assert trip.std <= env.now
            flexprint(
                "Trip %s got vehicle %s for early departure. soc=%s, procs=%s, any_active_processes=%s, on_hold=%s. isunblocked: %s. std: %d. area: %s area items: %s"
                % (
                    trip.ID,
                    vehicle.ID,
                    vehicle.battery.soc,
                    vehicle.dwd.active_processes,
                    vehicle.dwd.any_active_processes,
                    vehicle.dwd.on_hold,
                    VehicleFilter(filter_names=["isunblocked"])(vehicle),
                    trip.std,
                    vehicle.dwd.current_area,
                    vehicle.dwd.current_area.items,
                ),
                env=env,
                switch="departure_before_fully_charged_3",
            )
            # print(vehicle.dwd.current_area, vehicle.dwd.current_area.items)

            for proc in vehicle.dwd.active_processes:
                # Cancel active processes for early departure
                proc.cancel()

            trip.got_early_vehicle = True
            trip.t_got_early_vehicle = env.now
        else:
            trip.got_early_vehicle = False
            trip.t_got_early_vehicle = None

        vehicle_reassignment = trip.vehicle is not None
        if vehicle_reassignment:
            # trip had a vehicle assigned before
            if trip.std < env.now:
                raise RuntimeError(
                    "reassignment of a new vehicle to a delayed trip is not "
                    "supported atm"
                )

            # flexprint("reassigning trip '%s' with previous vehicle '%s' at area %s to vehicle '%s' at area %s"
            #           % (trip.ID, trip.vehicle.ID, trip.vehicle.dwd.current_area.ID, vehicle.ID, vehicle.dwd.current_area.ID),
            #           env=env, switch='vehicle_reassignment')
            trip.vehicle.trip = None  # previous vehicle

        trip.t_match = env.now
        trip.vehicle = vehicle
        vehicle.trip = trip
        if not vehicle_reassignment:
            depot.unassigned_trips.remove(trip)

        # Find the Precondition process at this area, if any
        preconddata = next(
            (
                depot.processes[proc_ID]
                for proc_ID, procdata in depot.processes.items()
                if proc_ID in vehicle.dwd.current_area.available_processes
                and issubclass(procdata["type"], Precondition)
            ),
            None,
        )

        if preconddata is not None:
            # Bugfix to make sure the name of the processs doesn't matter

            for key, value in depot.processes.items():
                if value["typename"] == "Precondition":
                    proc_obj = value["type"](env=env, **value["kwargs"])
                    break

            if proc_obj.vehicle_filter(vehicle):  # Apply filters
                # Determine starting time. Call process and schedule it in env
                until_std = trip.std - env.now
                if until_std > 0:
                    if until_std > proc_obj.dur:
                        start_delayed(
                            env, proc_obj(vehicle=vehicle), until_std - proc_obj.dur
                        )
                    else:
                        env.process(proc_obj(vehicle=vehicle))
                        if until_std < proc_obj.dur:
                            # Precondition will last until after departure.
                            # Will be cancelled upon departure.
                            # raise ValueError('End of preconditioning is later than the departure time of the trip. This case is not solved yet.')
                            pass
                else:
                    # If until_std is negative it means that the vehicle is
                    # already late by the time the matching of the trip and
                    # the vehicle happend -> no precondition
                    pass

                # flexprint('Scheduled preconditioning for vehicle %s. std=%s, until_std=%s'
                #           % (vehicle.ID, trip.std, until_std),
                #           env=env, switch='dispatch2')

                # flexprint('Scheduled preconditioning for vehicle %s. std=%s. until_std=%s, slot=%s'
                #           % (vehicle.ID, trip.std, until_std, vehicle.dwd.current_slot),
                #           env=env, switch='objID', objID=vehicle.ID)

        # flexprint('About to trigger get for vehicle %s. slot: %s. isunblocked=%s'
        #         % (vehicle.ID, vehicle.dwd.current_slot, VehicleFilter(filter_names=['isunblocked'])(vehicle)),
        #         env=env, switch='objID', objID=vehicle.ID)

        # Trigger open get requests again because they might be successful
        # now
        if vehicle.dwd.current_area is not None:
            vehicle.dwd.current_area.trigger_get(None)

        # flexprint('triggered get for vehicle %s. slot: %s'
        #         % (vehicle.ID, vehicle.dwd.current_slot),
        #         env=env, switch='objID', objID=vehicle.ID)

        flexprint(
            "Assigned vehicle %s with type %s to trip %s with types %s"
            % (vehicle.ID, vehicle.vehicle_type.ID, trip.ID, trip.vehicle_types),
            env=env,
            switch="dispatch2",
        )

    @staticmethod
    def scheduling_delay(env, trip):
        """Return a delay [int] for matching *trip* with a vehicle."""
        return 0

    @staticmethod
    def trigger_until_found(depot, trip, interval=60):
        """Periodically trigger the *depot*'s dispatch until a vehicle is found.

        for *trip*. This triggering should be used for special cases only and
        *interval* [s] should be set with care due to a potentially high
        impact on sim time.
        """
        while trip.vehicle is None:
            yield depot.env.timeout(interval)
            if trip.vehicle is None:  # meanwhile something else might have triggered
                # flexprint('Triggering dispatch for trip %s. vehicle: %s, std: %d'
                #           % (trip.ID, trip.vehicle, trip.std), env=depot.env)
                depot.depot_control.dispatch_strategy.trigger(depot)


class DSFirst(BaseDispatchStrategy):
    name = "FIRST"
    short_description = "first"
    tooltip = (
        "Match trips and vehicles in the order of creation of the "
        "parking area the vehicles are located at. No particular "
        "priority of vehicles at Direct areas."
    )

    @staticmethod
    def trigger(depot, *args, **kwargs):
        """Trigger the matching process."""
        DSFirst.find_match(depot)

    @staticmethod
    def get_pending_vehicles(depot, trip):
        """Return a dict of pending vehicles.

        A vehicle is pending if it's at an area in a parking area group and has
        no trip assigned to, or its assigned trip's std is later than
        *trip*.std.

        structure of returned dict:
            {parking_area_group : {store : list of vehicles in fifo order}}
        """

        result = {}
        for parking_area_group in depot.parking_area_groups:
            for store in parking_area_group.stores:
                if isinstance(store, LineArea):
                    rg = store.range_from_side(store.side_get_default)
                    vehicles = [
                        store.items[i] for i in rg if store.items[i] is not None
                    ]
                else:
                    vehicles = store.vehicles[::-1]

                if len(vehicles) > 0:
                    pending_vehicles = [
                        v for v in vehicles if v.trip is None or trip.std < v.trip.std
                    ]
                    for v in vehicles:
                        if v.trip is not None and trip.std < v.trip.std:
                            flexprint(
                                "reconsidering vehicle %s: trip %s std %d is later than trip %s std %d"
                                % (v.ID, v.trip.ID, v.trip.std, trip.ID, trip.std),
                                env=depot.env,
                                switch="vehicle_reconsideration",
                            )

                    if len(pending_vehicles) > 0:
                        if parking_area_group not in result:
                            result[parking_area_group] = {}
                        result[parking_area_group][store] = pending_vehicles

        return result

    @staticmethod
    def find_match(depot):
        """Find a matching vehicle for the next trip.

        Is called recursively while DSFirst.try_assign is successful.
        """

        next_trips = DSFirst.next_trips(depot)
        if next_trips:
            # step 2A: assign pending vehicles on line stores
            for next_trip in next_trips:
                pending_vehicles = DSFirst.get_pending_vehicles(depot, next_trip)
                for parking_area_group in pending_vehicles:
                    for store in pending_vehicles[parking_area_group]:
                        vehicle = pending_vehicles[parking_area_group][store][0]
                        if DSFirst.try_assign(vehicle, next_trip, depot):
                            # Recall because the assignment situation has
                            # changed
                            DSFirst.trigger(depot)
                            return

            # step 2B: assign vehicles on direct stores (buffer areas) if
            # available
            if depot.direct_departure_areas:
                for next_trip in next_trips:
                    for direct_area in depot.direct_departure_areas:
                        for vehicle in direct_area.vehicles:
                            if vehicle.trip is None and DSFirst.try_assign(
                                vehicle, next_trip, depot
                            ):
                                # Recall because the assignment situation has
                                # changed
                                DSFirst.trigger(depot)
                                return

            if (
                depot.any_process_cancellable_for_dispatch
                and globalConstants["depot"]["dispatch_retrigger_interval"] is not None
            ):
                for next_trip in next_trips:
                    # No suitable vehicle found, trip will be delayed. If the
                    # option is on, schedule a periodic trigger to allow a
                    # possible departure by cancelling a process
                    urgent = (
                        next_trip.vehicle is None and next_trip.std <= next_trip.env.now
                    )
                    if urgent and not next_trip.periodic_trigger_scheduled:
                        depot.env.process(
                            DSFirst.trigger_until_found(
                                depot,
                                next_trip,
                                globalConstants["depot"]["dispatch_retrigger_interval"],
                            )
                        )
                        next_trip.periodic_trigger_scheduled = True

    @staticmethod
    def try_assign(vehicle, trip, depot):
        """Try to match *vehicle* and *trip*.

        Return True if successful, else
        False.
        """
        urgent = trip.vehicle is None and trip.std <= trip.env.now
        if urgent:
            # Look for a vehicle with sufficient battery level that is not
            # blocked (i.e. could depart immediately)
            vf = VehicleFilter(
                filter_names=[
                    "vehicle_type",
                    "not_on_hold",
                    "no_active_uncancellable_processes",
                    "sufficient_energy",
                    "isunblocked",
                ],
                vehicle_types=trip.vehicle_types,
                trip=trip,
            )

        else:
            # Look for a vehicle that has finished charging and has enough
            # energy for the trip
            vf = VehicleFilter(
                filter_names=[
                    "vehicle_type",
                    "not_on_hold",
                    "no_active_processes",
                    "sufficient_energy",
                ],
                vehicle_types=trip.vehicle_types,
                trip=trip,
            )

        if vf(vehicle):
            DSFirst.assign(vehicle, trip, depot)
            return True

        return False


class DSSmart(BaseDispatchStrategy):
    name = "SMART"
    short_description = "smart"
    tooltip = "Match trips and vehicles using DispatchRating."

    vf_urgent = VehicleFilter(
        filter_names=[
            "vehicle_type",
            "not_on_hold",
            "no_active_uncancellable_processes",
            "sufficient_energy",
            "isunblocked",
        ]
    )
    vf_usual = VehicleFilter(
        filter_names=[
            "vehicle_type",
            "not_on_hold",
            "no_active_processes",
            "sufficient_energy",
        ]
    )

    @staticmethod
    def trigger(depot, *args, **kwargs):
        """Trigger the matching process."""
        DSSmart.find_match(depot)

    @staticmethod
    def get_suitable_vehicles(depot, trip, vf):
        """Return a list of vehicles that are suitable for *trip*.

        A vehicle is suitable if it's at an area in a parking area group and
        has no trip assigned to, or its assigned trip's std is later than
        *trip*.std. Furthermore, a vehicle must pass all criteria of *vf*. For
        Line areas, only the first pending vehicle closest to the exit is
        included.
        """
        vehicles = []

        for parking_area_group in depot.parking_area_groups:
            for area in parking_area_group.stores:
                if isinstance(area, LineArea):
                    # Add one vehicle at max. at Line area
                    rg = area.range_from_side(area.side_get_default)
                    vehicle = next(
                        (
                            area.items[idx]
                            for idx in rg
                            if area.items[idx] is not None
                            and (
                                area.items[idx].trip is None
                                or trip.std < area.items[idx].trip.std
                            )
                        ),
                        None,
                    )

                    if vehicle is not None and vf(vehicle):
                        vehicles.append(vehicle)
                else:
                    # Add vehicles at Direct area
                    for vehicle in area.vehicles:
                        if (vehicle.trip is None or trip.std < vehicle.trip.std) and vf(
                            vehicle
                        ):
                            vehicles.append(vehicle)

        # Temporary checks
        # for v in vehicles:
        #     if v.trip is not None and trip.std < v.trip.std:
        #     # if v.trip is not None and v.vehicle_type in trip.vehicle_types:
        #         flexprint("reconsidering vehicle '%s': assigned trip '%s' std=%d (due in %d) is later than trip '%s' with std=%d (due in %d), delayed_departure=%s, vehicle_types=%s, vehicle=%s. stddiff=%d, greater than 30mins: %s"
        #                   % (v.ID, v.trip.ID, v.trip.std, trip.env.now - v.trip.std, trip.ID, trip.std, trip.env.now - trip.std, trip.delayed_departure, [vt.ID for vt in trip.vehicle_types], trip.vehicle, v.trip.std - trip.std, (v.trip.std - trip.std) > 1800),
        #                   env=depot.env, switch='vehicle_reconsideration')

        return vehicles

    @staticmethod
    def find_match(depot):
        """Find a matching vehicle for the next trip.

        Is called recursively while successful.
        """
        next_trips = DSSmart.next_trips(depot)
        for trip in next_trips:
            urgent = trip.vehicle is None and trip.std <= trip.env.now
            if urgent:
                # Use filter for a vehicle with sufficient battery level that
                # is not blocked (i.e. could depart immediately)
                vf = DSSmart.vf_urgent
            else:
                # Use filter for a vehicle that has finished charging and has
                # sufficient energy for the trip
                vf = DSSmart.vf_usual
            vf.vehicle_types = trip.vehicle_types
            vf.get_vt_objects(force=True)
            vf.trip = trip

            vehicles = DSSmart.get_suitable_vehicles(depot, trip, vf)

            if vehicles:
                rating = DispatchRating(
                    [
                        VehicleAlternative(
                            (
                                vehicle.dwd.current_area,
                                vehicle.dwd.current_area.items.index(vehicle),
                            ),
                            vehicle,
                            depot.parking_area_groups[0].max_power,
                            depot.parking_area_groups[0].max_capacity_line,
                        )
                        for vehicle in vehicles
                    ]
                )

                best_vehicle = vehicles[rating.best_alternative_nos[0][0]]

                # print()
                # flexprint('DispatchRating for trip %s:' % trip.ID, env=depot.env)
                # for alt in rating.alternatives_obj:
                #     print('\t\tVehicle %s (type %s) at area %s (type %s) index %d' % (alt.vehicle.ID, alt.vehicle.vehicle_type.ID, alt.slot[0].ID, alt.slot[0].typename, alt.slot[1]))
                #     print('\t\t\tbuffer: value=%s' % alt.buffer.value)
                #     print('\t\t\ttypestack: value=%s' % alt.typestack.value)
                #     print('\t\t\trfd_diff: value=%s, diff=%s' % (alt.rfd_diff.value, alt.rfd_diff.diff))
                #     print('\t\t\tavailable_power: value=%s' % alt.available_power.value)
                #     print('\t\t\tempty_slots_exit: value=%s' % alt.empty_slots_exit.value)
                #     print('\t\t\t__________')
                # print('\trating result:')
                # print('\t\tweighted: %s' % rating.weighted)
                # print('\t\tsums: %s' % rating.sums)
                # print('\t\tbest_value: %s' % rating.best_value)
                # print('\t\tbest_alternative_nos: ', rating.best_alternative_nos)
                # print('\t\tbest_alternatives: %s' % rating.best_alternatives)
                # print('\t\tbest vehicle: %s' % best_vehicle.ID)

                DSSmart.assign(best_vehicle, trip, depot)

                # Recall because the assignment situation has changed
                DSSmart.trigger(depot)
                break

            elif (
                urgent
                and depot.any_process_cancellable_for_dispatch
                and globalConstants["depot"]["dispatch_retrigger_interval"] is not None
                and not trip.periodic_trigger_scheduled
            ):
                # No suitable vehicle found, trip will be delayed. If the
                # option is on, schedule a periodic trigger to allow a possible
                # departure by cancelling a process
                depot.env.process(
                    DSSmart.trigger_until_found(
                        depot,
                        trip,
                        globalConstants["depot"]["dispatch_retrigger_interval"],
                    )
                )
                trip.periodic_trigger_scheduled = True

    @staticmethod
    def scheduling_delay(env, trip):
        """Return the interval [int] from now until *lead_time_match* before.

        departure time of *trip*. Return 0 if the departure time is in less
        than *lead_time_match*.
        """
        if trip.std < env.now:
            raise RuntimeError(
                "scheduling_delay cannot be determined if the "
                "departure time of a trip has passed."
            )
        return max(trip.std - env.now - globalConstants["depot"]["lead_time_match"], 0)


class DepotControl:
    """Control of vehicle movement and actions in the depot.

    Instantiated as attribute of the corresponding depot.

    Attributes:
    departure_areas: [AreaGroup] of all areas where vehicles can leave the
        depot from. Is automatically filled with areas where issink is True in
        DepotConfigurator. Is not in self.depot.groups.
    request_count: [Counter] used for validation when
        globalConstants['depot']['prioritize_init_store'] is True
    """

    dispatch_strategies = {DSFirst.name: DSFirst, DSSmart.name: DSSmart}

    parking_congestion_event_cls = None
    """Event that succeeds if parking congestion occurs because no slot can be.

    found at a parking area upon request.
    """

    def __init__(self, env, depot, dispatch_strategy_name="FIRST"):
        self.depot = depot
        self.env = env

        self._dispatch_strategy_name = None
        self._dispatch_strategy = None
        self.dispatch_strategy_name = dispatch_strategy_name

        self.departure_areas = AreaGroup(self.env, [], "departure_areas")

    def _complete(self):
        """Actions that must take place before simulation start, but may not be.

        possible during the depot configuration phase.
        """
        # Set self.process_request based on prioritize_init_store option. Not
        # possible during init because globalConstants are loaded afterwards.
        if globalConstants["depot"]["prioritize_init_store"]:
            self.process_request = self.process_request_prio_init
            self.request_count = Counter()
        else:
            self.process_request = self.process_request_prio_parking

    @property
    def dispatch_strategy_name(self):
        """'name' attribute of set dispatch strategy."""
        return self._dispatch_strategy_name

    @dispatch_strategy_name.setter
    def dispatch_strategy_name(self, value):
        """Set the dispatch strategy by passing the strategy name in *value*."""
        if value not in self.dispatch_strategies:
            raise ValueError("Invalid dispatch strategy '%s'" % value)
        self._dispatch_strategy_name = value
        self._dispatch_strategy = self.dispatch_strategies[value]

    @property
    def dispatch_strategy(self):
        """[BaseDispatchStrategy] subclass."""
        return self._dispatch_strategy

    @dispatch_strategy.setter
    def dispatch_strategy(self, value):
        """Set the dispatch strategy by passing the strategy class in *value*."""
        self._dispatch_strategy = value
        self._dispatch_strategy_name = next(
            name for name, cls in self.dispatch_strategies.items() if cls is value
        )

    def checkin(self, vehicle):
        """Reset and set several variables upon arrival of a vehicle and.

        trigger follow-up actions.
        """
        # if vehicle.trip_at_departure is not vehicle.trip:
        #     flexprint('Vehicle %s departure trip %s is not the same as arrival trip %s'
        #               % (vehicle.ID, vehicle.trip_at_departure.ID, vehicle.trip.ID),
        #               env=self.env)
        assert vehicle.trip_at_departure is vehicle.trip

        # flexprint('Vehicle %s in checkin'
        #           % vehicle.ID, env=self.env, switch='objID', objID=vehicle.ID)
        self.depot.evaluation.log_arrival(vehicle)

        flexprint(
            "%s checking in at %s with battery level = %f"
            % (vehicle.ID, self.depot.ID, vehicle.battery.energy),
            env=self.env,
            switch="operations",
        )

        vehicle.dwd.current_depot = self.depot
        self.depot.count += 1
        self.assign_plan(vehicle)

        self.depot.pending_arrivals.remove(vehicle.trip)
        vehicle.trip.ata = self.env.now

        vehicle.finished_trips.append(vehicle.trip)
        vehicle.trip = None

        self.proceed(vehicle)
        self.depot.checkins += 1

    def assign_plan(self, vehicle):
        """Check what activity plan is suitable for the vehicle and assign it."""
        vehicle.dwd.plan = None
        # Check if there is a specific plan matching the vehicle
        for seq in self.depot.list_spec_plans:
            if seq.vehicle_filter(vehicle):
                vehicle.dwd.plan = seq.copy()
                break
        # No specific match, therefore assign default
        if vehicle.dwd.plan is None:
            vehicle.dwd.plan = self.depot.default_plan.copy()

    def proceed(self, vehicle):
        """Main function to move a vehicle to the next area.

        Splits into
        proceed_area and proceed_group for further actions.
        """
        # an = vehicle.dwd.current_area.ID if vehicle.dwd.current_area is not None else None
        # flexprint('Vehicle %s in proceed. Current area: %s. plan: %s'
        #           % (vehicle.ID, an, [a.ID for a in vehicle.dwd.plan]),
        #           env=self.env, switch='objID', objID=vehicle.ID)

        current_area = vehicle.dwd.current_area
        # Check if current area is a sink, if there is a current area
        if current_area is None:
            issink = False
        else:
            issink = current_area.issink

        # Check if the vehicle has reached the last depot area in its
        # areaSchedule or issink is True. If not, proceed.
        if vehicle.dwd.plan and not issink:
            # Get and remove the next entry from plan
            next_entry = vehicle.dwd.plan.pop(0)
            if isinstance(next_entry, AreaGroup):
                self.env.process(self.proceed_group(vehicle, current_area, next_entry))
            else:
                self.env.process(self.proceed_area(vehicle, current_area, next_entry))
        else:
            # The vehicle stays idling at the current area, ready for
            # departure
            # flexprint(
            # 'Vehicle %s staying idle in proceed. Active processes: %s'
            # % (vehicle.ID, [p.ID for p in vehicle.dwd.active_processes]),
            # env=self.env, switch='objID', objID=vehicle.ID)
            vehicle.dwd.plan.clear()
            if current_area is not None:
                current_area.trigger_get(None)
            self.trigger_dispatch()

            # This was removed cause it triggered an Exception after fixing #90
            # if globalConstants["general"]["LOG_ATTRIBUTES"]:
            #    current_area.logger.steplog()

    def proceed_area(self, vehicle, current_area, next_area):
        """Proceed with *next_area* as a BaseArea subtype."""
        flexprint(
            "Vehicle %s calling at area %s" % (vehicle.ID, next_area.ID),
            env=self.env,
            switch="operations",
        )

        # Check if the vehicle meets entry condition for the next area.
        permission = next_area.entry_filter(vehicle)
        if permission:
            flexprint(
                "%s entry at area %s permitted. Vehicle type is %s."
                % (vehicle.ID, next_area.ID, vehicle.vehicle_type.ID),
                env=self.env,
                switch="operations",
            )

            # Check if the vehicle needs processes at the DepotArea
            process_IDs = self.get_process_need(vehicle, next_area)
            # flexprint('%s requests %s at area %s'
            #           % (vehicle.ID, [proc.ID for proc in process_IDs],
            #              next_area.ID), env=self.env, switch='operations')
            flexprint(
                "%s requests %s at area %s" % (vehicle.ID, process_IDs, next_area.ID),
                env=self.env,
                switch="operations",
            )
            if globalConstants["depot"]["log_cm_data"]:
                self.depot.evaluation.cm_report.log(ProcessCalled(self.env, vehicle))

            # Initiate movement if any processes are needed
            if process_IDs:
                # Wait for a slot at the next area
                vehicle.dwd.on_hold = True
                yield next_area.put(vehicle)
                flexprint(
                    "vehicle %s was put into area %s." % (vehicle.ID, next_area.ID),
                    env=self.env,
                    switch="operations",
                )

                # Get vehicle from current area
                if current_area is not None:
                    yield current_area.get(lambda item: item.ID == vehicle.ID)

                # Run requested processes
                self.env.process(self.run_processes(vehicle, process_IDs))

            else:
                # The vehicle can skip the DepotArea
                # Start checks for the next one
                self.proceed(vehicle)

        else:
            # The vehicle is not allowed to stay at the DepotArea
            # Start checks for the next one
            flexprint(
                "%s entry at area %s denied because of entry condition. "
                "Vehicle type is %s."
                % (vehicle.ID, next_area.ID, vehicle.vehicle_type.ID),
                env=self.env,
                switch="operations",
            )
            self.proceed(vehicle)

    def proceed_group(self, vehicle, current_area, group):
        """Proceed with next_area as an AreaGroup type."""
        flexprint(
            "Vehicle %s calling at AreaGroup %s" % (vehicle.ID, group.ID),
            env=self.env,
            switch="operations",
        )
        if globalConstants["depot"]["log_cm_data"]:
            self.depot.evaluation.cm_report.log(ProcessCalled(self.env, vehicle))

        # Check if the vehicle meets entry condition for the next area.
        permissions = group.check_entry_filters(vehicle)
        flexprint(
            "%s permissions at areas in AreaGroup %s: %s. Vehicle type is"
            " %s." % (vehicle.ID, group.ID, permissions, vehicle.vehicle_type.ID),
            env=self.env,
            switch="operations",
        )
        if any(permissions):
            # Check if the vehicle needs processes at any DepotArea
            # process_IDs_all = [[] for _ in range(len(permissions))]
            process_IDs_all = [[]] * len(permissions)
            for idx, permission in enumerate(permissions):
                if permission:
                    process_IDs_all[idx] = self.get_process_need(
                        vehicle, group.stores[idx]
                    )
            flexprint(
                "Vehicle %s process_IDs_all: %s" % (vehicle.ID, process_IDs_all),
                env=self.env,
                switch="operations",
            )

            selection = [
                True if perm and pl else False
                for perm, pl in zip(permissions, process_IDs_all)
            ]
            flexprint(
                "Vehicle %s selection: %s" % (vehicle.ID, selection),
                env=self.env,
                switch="operations",
            )
            # Initiate movement if any processes are needed
            if any(selection):
                req_time = self.env.now
                # Wait for a slot any of the areas in the selection
                vehicle.dwd.on_hold = True
                next_area = yield self.env.process(group.put(vehicle, selection))
                waiting_time = self.env.now - req_time
                if waiting_time > 0 and current_area is not None:
                    flexprint(
                        "Delay from %s to %s of %s secs! Vehicle: %s"
                        % (current_area.ID, group.ID, waiting_time, vehicle.ID),
                        env=self.env,
                        switch="parking_full",
                    )
                    if self.parking_congestion_event_cls is not None and isinstance(
                        group, ParkingAreaGroup
                    ):
                        pc_event = self.parking_congestion_event_cls(self.env)
                        pc_event.succeed()

                flexprint(
                    "Vehicle %s was put into area %s through group."
                    % (vehicle.ID, next_area.ID),
                    env=self.env,
                    switch="operations",
                )

                # Get vehicle from current area
                if current_area is not None:
                    yield current_area.get(lambda item: item.ID == vehicle.ID)
                    flexprint(
                        "Got vehicle %s from area %s through group."
                        % (vehicle.ID, current_area.ID),
                        env=self.env,
                        switch="operations",
                    )

                # Get process requests again and run them
                process_IDs = process_IDs_all[group.stores.index(next_area)]
                self.env.process(self.run_processes(vehicle, process_IDs))

            else:
                # The vehicle can skip the DepotArea
                # Start checks for the next one
                self.proceed(vehicle)

        else:
            # The vehicle is not allowed to stay at any of the areas in the
            # group. Call proceed for the next entry
            flexprint(
                "%s entry at AreaGroup %s denied because of entry "
                "conditions. Vehicle type is %s."
                % (vehicle.ID, group.ID, vehicle.vehicle_type.ID),
                env=self.env,
                switch="operations",
            )
            self.proceed(vehicle)

    def get_process_need(self, vehicle, area):
        """Check what processes a vehicle will request at an area.

        Return a list of processes by ID to request, which can be empty.
        """
        flexprint(
            "\tavailable_processes of area %s: %s"
            % (area.ID, area.available_processes),
            env=self.env,
            switch="operations",
        )

        # Append entries where the process is mandatory or both optional and
        # vehicle_filter returns True
        process_IDs = [
            procID
            for procID in area.available_processes
            if self.depot.processes[procID]["type"].request_immediately
            and (
                self.depot.processes[procID]["kwargs"]["ismandatory"]
                or not self.depot.processes[procID]["kwargs"]["ismandatory"]
                and self.depot.processes[procID]["kwargs"]["vehicle_filter"](vehicle)
            )
        ]

        return process_IDs

    def run_processes(self, vehicle, process_IDs):
        """Initialize and run processes marked in process_IDs for a vehicle at.

        an area.
        If more than one process is requested, then all processes will be
        started simultaneously. Call proceed() when all processes have
        finished.
        """
        flexprint(
            "\t%s process list: %s" % (vehicle.ID, process_IDs),
            env=self.env,
            switch="operations",
        )
        vehicle.dwd.any_active_processes = True
        vehicle.dwd.on_hold = False

        # Instantiate process objects
        proc_objs = [
            self.depot.processes[procID]["type"](
                env=self.env, **self.depot.processes[procID]["kwargs"]
            )
            for procID in process_IDs
        ]

        # Call processes and schedule them in env
        calls = [self.env.process(proc(vehicle=vehicle)) for proc in proc_objs]
        flexprint(
            "Vehicle %s starting processes %s"
            % (vehicle.ID, [pr.ID for pr in proc_objs]),
            env=self.env,
            switch="operations",
        )

        yield simpy.AllOf(self.env, calls)

        flexprint(
            "Vehicle %s finished processes %s"
            % (vehicle.ID, [pr.ID for pr in proc_objs]),
            env=self.env,
            switch="operations",
        )

        # flexprint('Vehicle %s in run_processes. Finished all processes at area %s.'
        #           % (vehicle.ID, area.ID), env=self.env, switch='objID',
        #           objID=vehicle.ID)
        # , switch='operations'
        vehicle.dwd.any_active_processes = False

        self.proceed(vehicle)

    def checkout(self, vehicle):
        """Actions that are necessary for a vehicle when leaving the depot."""
        # flexprint('Vehicle %s in checkout'
        #           % vehicle.ID, env=self.env, switch='objID', objID=vehicle.ID)
        if vehicle.dwd.plan is not None:
            vehicle.dwd.plan.clear()

        if not vehicle.trip.reserved_for_init:
            self.depot.pending_departures.remove(vehicle.trip)

        if vehicle.finished_trips:
            assert vehicle.dwd.current_area is not None

        # if vehicle.battery.energy < vehicle.battery.energy_real:
        #     flexprint('Vehicle %s attempting departure with SoC < 1. energy level: %s. energy_real: %s.'
        #               ' scheduled trip: %s' % (vehicle.ID, vehicle.battery.energy, vehicle.battery.energy_real, vehicle.trip), env=self.env)
        #     flexprint(
        #         'Vehicle %s in checkout with battery not full (SoC=%.3f). t_got_early_vehicle: %s '
        #         % (vehicle.ID, vehicle.battery.soc,
        #            vehicle.trip.t_got_early_vehicle) + '<'*70,
        #         env=self.env, switch='departure_before_fully_charged')

        # if vehicle.finished_trips:
        #     flexprint('vehicle %s calling checkout'
        #               % vehicle.ID, env=self.env, switch='dispatch')
        #     flexprint('\tvehicle_types: %s'
        #               % vehicle.trip.vehicle_types_joinedstr,
        #               env=self.env, switch='dispatch')
        #     flexprint('\tvehicle %s procs active: %s'
        #               % (vehicle.ID, vehicle.dwd.any_active_processes),
        #               env=self.env, switch='dispatch')
        #     flexprint('\tvehicle %s battery energy level = %d; energy_real = %d'
        #               % (vehicle.ID, vehicle.battery.energy,
        #                  vehicle.battery.energy_real), env=self.env,
        #               switch='dispatch')

        # departure event
        if globalConstants["depot"]["log_cm_data"]:
            self.depot.evaluation.cm_report.log(Departure(self.env, vehicle))

        if vehicle.finished_trips:
            # vehicle departs from real area because it has finished at least
            # one trip
            vehicle.trip.vehicle_from = vehicle.dwd.current_area.ID
            vehicle.dwd.previous_area = vehicle.dwd.current_area
            vehicle.dwd.current_area = None
            self.depot.count -= 1
            self.depot.checkouts += 1
        else:
            # vehicle departs from init store
            vehicle.trip.vehicle_from = self.depot.init_store.ID

        vehicle.dwd.current_depot = None

        assert vehicle.trip.atd is None
        vehicle.trip.atd = self.env.now
        vehicle.trip.eta = self.env.now + vehicle.trip.sta - vehicle.trip.std
        vehicle.trip.destination.pending_arrivals.append(vehicle.trip)

        if globalConstants["general"]["LOG_SPECIFIC_STEPS"]:
            vehicle.logger.steplog()

        self.env.process(vehicle.drive())

        self.env.process(assert_after_checkout(self.env, vehicle))

        self.trigger_dispatch()

    def trigger_dispatch(self):
        """Trigger the matching of trips and vehicles of.

        self.dispatch_strategy.
        """
        self.dispatch_strategy.trigger(self.depot)

    def request_vehicle(self, trip, filter=lambda item: True):
        """Schedule processes for the vehicle request for *trip*."""
        flexprint(
            "Received request for trip %s with std = %d" % (trip.ID, trip.std),
            env=self.env,
            switch="timetable",
        )
        self.env.process(self.process_request(trip, filter=filter))

    def register_for_dispatch(self, trip):
        """Prepare processing the vehicle request for *trip* by the regular.

        dispatch.
        """
        self.depot.pending_departures.append(trip)
        self.env.process(self.schedule_for_matching(trip))

        # Schedule triggers for dispatch
        self.env.process(
            trip.notify_due_departure(
                trip.std - globalConstants["depot"]["lead_time_match"]
            )
        )
        self.env.process(trip.notify_due_departure(trip.std))

    def schedule_for_matching(self, trip):
        """Wait *delay* and then schedule *trip* for matching with a vehicle."""
        delay = self.dispatch_strategy.scheduling_delay(self.env, trip)
        yield self.env.timeout(delay)
        self.depot.unassigned_trips.append(trip)

    def process_request_prio_parking(self, trip, filter=lambda item: True):
        """Return a vehicle from the depot for which *filter* returns True.

        If there is no matching vehicle available immediately at
        self.departure_areas, try to get a vehicle from depot.init_store.
        If there is no matching vehicle either, issue requests at
        self.departure_areas that succeed as soon as a match is available.
        """
        self.register_for_dispatch(trip)
        # Wait until departure time
        yield self.env.timeout(trip.std - self.env.now)
        yield self.env.timeout(0)  # step back in event queue

        # Check if a vehicle matching filter is available immediately in
        # self.departure_areas. Trigger dispatch before to consider early
        # departures with cancellable processes
        self.trigger_dispatch()
        vehicle, requests = yield self.env.process(self.departure_areas.get_imm(filter))
        flexprint("vehicle after get_imm: %s" % (vehicle), switch="dispatch")

        if vehicle is None:
            # No suitable vehicle available immediately in
            # self.departure_areas, therefore check if a suitable vehicle is
            # available in depot.init_store
            match = self.depot.init_store.find("any", filter)
            flexprint(match, switch="dispatch")
            if match:
                # Vehicle is available in depot.init_store. Cancel requests to
                # self.departure_areas because they are not needed
                self.departure_areas.cancel_requests(requests)
                # Remove the trip from dispatch
                self.depot.unassigned_trips.remove(trip)
                # Get vehicle from depot.init_store
                vehicle = yield self.depot.init_store.get(filter)
                # assert trip.vehicle is None
                vehicle.system_entry = True

                flexprint("match found in depot init", env=self.env, switch="dispatch")
                flexprint(
                    "vehicles left in init: %d" % (self.depot.init_store.count),
                    switch="dispatch",
                )
            else:
                # No vehicle available in depot.init_store, wait for the
                # success of a request in self.departure_areas
                flexprint("no immediate match in init_store", switch="dispatch")
                vehicle = yield self.env.process(
                    self.departure_areas.get_wait(requests)
                )
                flexprint("match found with get_wait()", switch="dispatch")
        else:
            # Vehicle was found immediately at departure areas
            flexprint("IMMEDIATE MATCH FOUND", switch="dispatch")

        flexprint(
            "Got vehicle %s for trip %s. t_target_dep: %d; t_target Arr: %d"
            % (vehicle.ID, trip.ID, trip.std, trip.sta)
            + "; current_area: %s"
            % (
                vehicle.dwd.current_area.ID
                if vehicle.dwd.current_area is not None
                else None
            ),
            env=self.env,
            switch="objID",
            objID=vehicle.ID,
        )

        # Initiate departure
        vehicle.trip = trip
        trip.vehicle = vehicle
        vehicle.trip_at_departure = trip
        self.depot.checkout(vehicle)

    def process_request_prio_init(self, trip, filter=lambda item: True):
        """Return a vehicle from the depot for which *filter* returns True.

        Try to get a vehicle from depot.init_store first. If there is no
        match available, issue requests at self.departure_areas.
        """
        if not trip.reserved_for_init:
            self.register_for_dispatch(trip)
        # Wait until departure time
        yield self.env.timeout(trip.std - self.env.now)

        if trip.reserved_for_init:
            # Maintain request counter for stats
            self.request_count[trip.vehicle_types_joinedstr] += 1

            # Get vehicle from init store
            vehicle = yield self.depot.init_store.get(filter)
            assert self.env.now == trip.std
            vehicle.system_entry = True

        else:
            # Get vehicle from departure areas, wait if neccessary
            vehicle = yield self.env.process(self.departure_areas.get(filter=filter))

        # Initiate departure
        vehicle.trip = trip
        trip.vehicle = vehicle
        vehicle.trip_at_departure = trip
        self.depot.checkout(vehicle)


def assert_after_checkout(env, vehicle):
    yield env.timeout(0)  # step back in event queue

    # Cancel any open Precondition process and log it.
    for process in vehicle.dwd.active_processes:
        if isinstance(process, Precondition):
            process.cancel()
            yield env.timeout(0)
            if globalConstants["general"]["LOG_ATTRIBUTES"]:
                vehicle.logger.loggedData["canceled_precondition"][env.now] = {
                    "process": process,
                    "time_canceld": env.now,
                }

    assert not vehicle.dwd.any_active_processes
    assert not vehicle.dwd.active_processes

    if vehicle.trip.t_got_early_vehicle is not None:
        assert vehicle.trip.t_got_early_vehicle == env.now


def update_relations(put_event):
    """Updater to be called as callback of a successful put event for an area.

    Relies on a corresponding get event to succeed in the same simulation
    time step.
    """
    vehicle = put_event.item
    vehicle.dwd.previous_area = vehicle.dwd.current_area
    vehicle.dwd.current_area = put_event.resource
    if (
        hasattr(vehicle, "logger")
        and globalConstants["general"]["LOG_ATTRIBUTES"]
        and globalConstants["general"]["LOG_SPECIFIC_STEPS"]
    ):
        vehicle.logger.steplog()


class BaseArea(ABC):
    """Abstract base class for an area in a depot.

    Subclasses must inherit from
    a SimPy Store subclass that has attributes 'capacity', 'items' and
    'vacant'.

    Slots in the area are homogeneous, meaning that all vehicles that pass
    *entry_filter* can be put on any slot, not considering e.g. vehicle
    dimensions. Also, resources are not bound to a specific slots, but
    available on all. Instantiate different depot area objects if heterogeneous
    slots are needed.

    Parameters:
    ID: [str] unique title describing the main purpose of the area such as
        "service", "parking"
    charging_interfaces: [list] of DepotChargingInterface objects
    available_processes: [list] of depot process IDs [str] that are offered at
        the area
    issink: [bool] Defines if the area is a sink for vehicles in the depot,
        meaning that there is no next area to move to after finishing processes
        at this area. Vehicles are then available for leaving the depot.
        Enables declaring one or multiple areas as sink (usually parking
        areas). Set to True for a sink, or False for a non- terminating area.
    entry_filter: [VehicleFilter object or None] that determines if a vehicle
        is permitted to enter the area. If entry_filter returns True for a
        vehicle, it's preliminarily allowed to enter (other checks for
        processes need may follow). If entry_filter returns False, checks will
        move on to the next area. See class VehicleFilter for more
        documentation. If left None, a vehicle filter permitting all vehicles
        is created.
    process_priority: NOT IMPLEMENTED YET
        [list] specifies the order in which a Vehicle has to request
        processes at an area. Request processes consecutively or
        simultaneously.
    slot_orientation: [str] orientation of slots. Used for display purposes
        in the GUI only, has no functional purpose. See
        gui.area_view.DepotAreaView for documentation.
    """

    @abstractmethod
    def __init__(
        self,
        env,
        ID,
        available_processes,
        issink,
        entry_filter=None,
        charging_interfaces=None,
        slot_orientation="HORIZONTAL",
        *args,
        **kwargs
    ):
        super(BaseArea, self).__init__(env, *args, **kwargs)
        self.env = env
        self.ID = ID

        self.charging_interfaces = charging_interfaces
        if charging_interfaces is not None:
            if (
                not isinstance(charging_interfaces, list)
                or (charging_interfaces and len(charging_interfaces) != self.capacity)
                or not all(
                    isinstance(i, DepotChargingInterface) for i in charging_interfaces
                )
            ):
                raise ValueError(
                    "Depot area charging_interfaces must be None or an empty "
                    "list or a list with length equal to capacity, containing "
                    "DepotChargingInterface objects."
                )
            if not charging_interfaces:
                self.charging_interfaces = None

        self.available_processes = available_processes
        self.issink = issink
        self.entry_filter = (
            entry_filter if entry_filter is not None else VehicleFilter()
        )

        self.parking_area_group = None  # is set by group
        self.depot = None  # to be set later

        self.slot_orientation = slot_orientation

    def __repr__(self):
        return "{%s} %s" % (type(self).__name__, self.ID)

    @property
    def vacant_accessible(self):
        """Helper function to distinguish between direct and line areas for the.

        determination of vacant accessible slots.
        """
        if isinstance(self, LineArea):
            return self.vacant_entrance
        else:
            return self.vacant

    @property
    def vehicles(self):
        return [item for item in self.items if item is not None]

    @property
    def charge_proc(self):
        """Return the type of the Charge or subclass process available at this.

        area. Return None if there is None. Relies on the assumption that there
        is only one charging process at an area at max.
        """
        for pID in self.available_processes:
            if issubclass(self.depot.processes[pID]["type"], ChargeAbstract):
                return self.depot.processes[pID]["type"]
        return None

    @property
    def maxOccupiedSlots(self):
        if hasattr(self, "view"):
            return len(
                [
                    slotId
                    for slotId in self.view.slotViews
                    if self.view.slotViews[slotId].hasBeenUsed
                ]
            )
        return -1

    @property
    def scheduledVehicles(self):
        """Returns list of scheduled vehicles (trips assigned)."""
        return [vehicle for vehicle in self.vehicle if vehicle.trip is not None]

    @property
    def count_rfd(self):
        """Return the number of vehicles that are ready for departure at this.

        area.
        """
        return sum(item.dwd.isrfd for item in self.vehicles)

    @property
    @abstractmethod
    def count_rfd_unblocked(self):
        """Return the number of vehicles that are ready for departure and not.

        blocked from departure at this area.
        """

    def istypestack(self, substitution=True):
        """Return a tuple (istypestack [bool or None], vehicle_type.

        [VehicleType or None]).

        istypestack is True if all vehicles are of the same type. vehicle_type
        then is the type of the first vehicle from the back of the store. If
        *substitution* is True, also return True if all vehicles are mutually
        substitutable. istypestack is None if the area is empty. Otherwise
        False. In these cases vehicle_type is None.
        """
        # Check typestack for first vehicle only (works as long as substitution
        # is always mutual).
        first = next((v for v in self.items if v is not None), None)
        if first:
            result = self.istypestack_with(first, substitution)
            if result:
                return True, first.vehicle_type
            else:
                return False, None
        else:
            return None, None

    def istypestack_with(self, vehicle, substitution=True):
        """Return True if current vehicles would be a typestack together with.

        *vehicle*. Extenuate the comparison with subsitutable types if
        *substitution* is True. Return None if the area is empty. Otherwise
        return False.
        """
        if not self.vehicles:
            return None
        if substitution and vehicle.vehicle_type.group:
            return all(
                vi.vehicle_type in vehicle.vehicle_type.group.types
                for vi in self.vehicles
            )
        else:
            return all(vi.vehicle_type is vehicle.vehicle_type for vi in self.vehicles)

    def select(self, ID, print_missing=True):
        """Return the item with *ID* in self.items.

        Return None if the ID is
        not found.
        Relies on *ID* to be unique.

        ID: [str]
        print_missing: [bool] print a message if the item is not found.
        """
        item = next((item for item in self.vehicles if item.ID == ID), None)
        if item is None and print_missing:
            print("Cannot find item with ID '%s'." % ID)
        return item


class BaseAreaPut(ABC):
    """Base put event for areas regardless of area type."""

    def __init__(self, store, *args, **kwargs):
        # Declare self.callbacks to avoid inspection warnings; is overwritten
        # by super class
        self.callbacks = []

        super(BaseAreaPut, self).__init__(store=store, *args, **kwargs)
        self.callbacks.append(update_relations)

        # Logging
        if (
            hasattr(store, "logger")
            and globalConstants["general"]["LOG_ATTRIBUTES"]
            and globalConstants["general"]["LOG_SPECIFIC_STEPS"]
        ):
            self.callbacks.append(store.logger.steplog)
        if store.issink:
            self.callbacks.append(store.depot.evaluation.log_sl)
        if globalConstants["depot"]["log_cm_data"]:
            self.callbacks.append(store.depot.evaluation.cm_report.log)

        # Trigger GUI update
        if hasattr(store, "view"):
            self.callbacks.append(store.view.parkVehicle)

        self.init_time = store.env.now

        if globalConstants["general"]["LOG_ATTRIBUTES"]:

            def calc_waiting_time(request):
                """Log the waiting time from init until success of *request*.

                in the vehicle's data logger.
                """
                waiting_time = request.item.env.now - request.init_time
                request.item.logger.loggedData["area_waiting_time"][
                    request.item.env.now
                ] = {
                    "waiting_time": waiting_time,
                    "vehicle": request.item.ID,
                    "area": request.item.dwd.current_area.ID,
                }

            self.callbacks.append(calc_waiting_time)

        def print_after_put(request):
            flexprint(
                "Vehicle %s moved to area %s"
                % (request.item.ID, request.item.dwd.current_area),
                env=request.resource.env,
                switch="operations",
            )

        self.callbacks.append(print_after_put)


class BaseAreaGet(ABC):
    """Base get event for areas regardless of area type."""

    def __init__(self, store, *args, **kwargs):
        # Declare self.callbacks to avoid inspection warnings; is overwritten
        # by super class
        self.callbacks = []

        super(BaseAreaGet, self).__init__(store=store, *args, **kwargs)

        # Logging
        if (
            hasattr(store, "logger")
            and globalConstants["general"]["LOG_ATTRIBUTES"]
            and globalConstants["general"]["LOG_SPECIFIC_STEPS"]
        ):
            self.callbacks.append(store.logger.steplog)
        if store.issink:
            self.callbacks.append(store.depot.evaluation.log_sl)
        if globalConstants["depot"]["log_cm_data"]:
            self.callbacks.append(store.depot.evaluation.cm_report.log)

        # Trigger GUI update
        if hasattr(store, "view"):
            self.callbacks.append(store.view.unparkVehicle)


class DirectAreaPut(BaseAreaPut, ExclusiveRequest, StorePutExt):
    """Request to put *item* into the *store*.

    The request is triggered once
    there is space for the item in the store.

    Callbacks by BaseAreaPut are added last.
    """

    def __init__(self, store, item, other_requests=None):
        super(DirectAreaPut, self).__init__(
            store=store, item=item, other_requests=other_requests
        )


class DirectAreaGet(BaseAreaGet, ExclusiveRequest, FilterStoreExtGet):
    """Request to get an *item* from the *store* matching the *filter*.

    The
    request is triggered once there is such an item available in the store.

    *filter* is a function receiving one item. It should return ``True`` for
    items matching the filter criterion. The default function returns ``True``
    for all items, which makes the request to behave exactly like
    :class:`StoreGet`.

    Callbacks by BaseAreaGet are added last.
    """

    def __init__(self, store, filter=lambda item: True, other_requests=None, **kwargs):
        super(DirectAreaGet, self).__init__(
            store=store, filter=filter, other_requests=other_requests, **kwargs
        )


class DirectArea(BaseArea, PositionalFilterStore):
    """Depot area where vehicles don't block each other and therefore direct.

    access on all stored vehicles is possible.

    See parent class descriptions for more details.
    """

    def __init__(
        self,
        env,
        ID,
        capacity,
        available_processes,
        issink,
        entry_filter,
        charging_interfaces=None,
        slot_orientation="HORIZONTAL",
    ):
        super(DirectArea, self).__init__(
            env=env,
            ID=ID,
            capacity=capacity,
            available_processes=available_processes,
            issink=issink,
            entry_filter=entry_filter,
            charging_interfaces=charging_interfaces,
            slot_orientation=slot_orientation,
        )

        if globalConstants["general"]["LOG_ATTRIBUTES"]:
            self.logger = DataLogger(env, self, "DIRECTAREA")

    put = BoundClass(DirectAreaPut)
    get = BoundClass(DirectAreaGet)

    @property
    def pendingVehicles(self):
        """Returns list of pending vehicles (potentially blocking vehicles)."""
        return []

    @property
    def count_rfd_unblocked(self):
        """Return the number of vehicles that are ready for departure and not.

        blocked from departure at this area.
        """
        return sum(vehicle.dwd.isrfd for vehicle in self.vehicles)

    def slot_no(self, item):
        """Return the slot number of *item*.

        Return None if item is not in
        self.items.
        Unlike list indexing, slot number counting starts at 1. Therefore only
        meant for display purposes, otherwise use items.index(vehicle).
        """
        try:
            return self.items.index(item) + 1
        except ValueError:
            return None

    @staticmethod
    def index2slot_no(index):
        """Convert *index* (correct value for indexing list self.items) to slot.

        number (for display purposes).
        """
        return index + 1


class LineAreaPut(BaseAreaPut, ExclusiveRequest, LineStorePut):
    """Request to put *item* onto deepest accessible slot from *side* in.

    *store*. The request is triggered once there is accessible space for the
    item in the store.

    side: [str] Same as in LineStorePut.

    Callbacks by BaseAreaPut are added last.
    """

    def __init__(self, store, item, side="default", other_requests=None):
        super(LineAreaPut, self).__init__(
            store=store, item=item, side=side, other_requests=other_requests
        )


class LineAreaGet(BaseAreaGet, ExclusiveRequest, LineFilterStoreGet):
    """Request to get the first accessible *item* from *side* in *store*.

    matching *filter*(item). The request is triggered once there is an
    accessible item available in the store.

    side: [str] Same as in LineFilterStoreGet.
    filter: Same as in LineFilterStoreGet. Function receiving one item. It must
        return True for items matching the filter criterion. The default
        function returns True for all items.

    Subclass of ExclusiveRequest and simpyExt.LineFilterStoreGet, provides an
    interface for customization.

    Callbacks by BaseAreaGet are added last.
    """

    def __init__(
        self, store, filter=lambda item: True, side="default", other_requests=None
    ):
        super(LineAreaGet, self).__init__(
            store=store, filter=filter, side=side, other_requests=other_requests
        )


class LineArea(BaseArea, LineFilterStore):
    """Depot area where vehicles are arranged in a line and can block each.

    other (feature of LineFilterStore). Therefore not all stored vehicles may
    be directly accessible.
    See parent class descriptions for more details.

    At the moment, only areas where issink is True (e.g. typically False for a
    service area, True for parking areas) can reliably be of this area type.
    """

    def __init__(
        self,
        env,
        ID,
        capacity,
        available_processes,
        issink,
        entry_filter,
        charging_interfaces=None,
        slot_orientation="VERTICAL",
        side_put_default="back",
        side_get_default="front",
    ):
        super(LineArea, self).__init__(
            env=env,
            ID=ID,
            capacity=capacity,
            available_processes=available_processes,
            issink=issink,
            entry_filter=entry_filter,
            charging_interfaces=charging_interfaces,
            slot_orientation=slot_orientation,
            side_put_default=side_put_default,
            side_get_default=side_get_default,
        )
        if not issink:
            warn(
                "WARNING: LineArea should only be used as sinks, "
                "because the depot control expects that a get request to "
                "an area that is not a sink is immediately successful."
            )

        if globalConstants["general"]["LOG_ATTRIBUTES"]:
            self.logger = DataLogger(env, self, "LINEAREA")

    put = BoundClass(LineAreaPut)
    get = BoundClass(LineAreaGet)

    @property
    def pendingVehicles(self):
        """Returns list of pending vehicles (potentially blocking vehicles)."""
        return [item for item in self.items if item is not None and item.trip is None]

    @property
    def count_rfd_unblocked(self):
        """Return the number of vehicles that are ready for departure and not.

        blocked from departure at this area.
        """
        return sum(
            vehicle.dwd.isrfd and self.isunblocked(vehicle) for vehicle in self.vehicles
        )

    def slot_no(self, item):
        """Return the slot number of *item*.

        Return None if item is not in
        self.items.
        The slot closest to the front has the lowest number, which is the
        reverse of items.index. Unlike list indexing, slot number counting
        starts at 1. Therefore only meant for display purposes, otherwise use
        items.index(vehicle).
        """
        try:
            return self.capacity - self.items.index(item)
        except ValueError:
            return None

    def index2slot_no(self, index):
        """Convert *index* (correct value for indexing self.items) to slot.

        number (for display purposes).
        """
        return self.capacity - index


class BaseParkingStrategy(ABC):
    """Base class for a parking strategy used with a parking area group.

    Attributes:
    name: [str] identifier used internally and for configuration
    short_description: [str] for frontend
    tooltip: [str] short explanation for frontend
    """

    name = ""
    short_description = ""
    tooltip = ""

    @staticmethod
    @abstractmethod
    def determine_store(*args, **kwargs):
        """Return the most suitable area for parking a vehicle.

        A put request
        to this area must be immediately successful.
        """


class PSFirst(BaseParkingStrategy):
    name = "FIRST"
    short_description = "first available (default)"
    tooltip = (
        "This strategy will try to park a vehicle on the first "
        "available\nslot, filling up areas inside a group one by one."
    )

    @staticmethod
    def determine_store(preselected_stores, *args, **kwargs):
        for store in preselected_stores:
            if store.vacant_accessible:
                return store
        return None


class PSEven(BaseParkingStrategy):
    name = "EVEN"
    short_description = "even"
    tooltip = (
        "This strategy will try to even out area usage inside a group,"
        "\nincreasing vehicle availability."
    )

    @staticmethod
    def determine_store(preselected_stores, *args, **kwargs):
        target_store = None

        for store in preselected_stores:
            count = store.count
            if target_store is None or count < target_store.count:
                target_store = store
                if count == 0:
                    # count cannot decrease -> park here
                    break

        return target_store


class PSMixed(BaseParkingStrategy):
    name = "MIXED"
    short_description = "mixed"
    tooltip = (
        "This strategy combines the FIRST and MIXED strategies, "
        "starting to access the next\narea after the previous one is "
        "filled up to 25% until the last one is at\n25%, then going "
        "back to the first area until 50% and so on."
    )

    @staticmethod
    def determine_store(preselected_stores, *args, **kwargs):
        current_percentage = 0
        while current_percentage < 100:
            current_percentage = current_percentage + 25
            for store in preselected_stores:
                if store.count * 100 / store.capacity < current_percentage:
                    return store
        return None


class PSSmart(BaseParkingStrategy):
    """
    This strategy consists of two steps:

    1)  parking vehicles on stores depending on their accessibility
            -> rate_stores()
    2)  assign vehicles to trips and vice versa
            -> find_match()

    The ideal depot layout for this strategy has several non-typed (no
    entry_filter) LineStores, optionally followed by a DirectStore for
    buffering vehicles which cannot initially be assigned.

    Note: This strategy only makes sense with stores that don't restrict by
    vehicle type, since it wont have any effect on typed stores.
    """

    name = "SMART"
    short_description = "smart"
    tooltip = (
        "This strategy will try to utilize the areas inside a group "
        "according to\nupcoming departure requests, optimizing "
        "vehicle availability\n\nNote: Potential improvements only "
        "affect non-vehicle-typed parking lots."
    )

    @staticmethod
    def determine_store(preselected_stores, parking_area_group, item, *args, **kwargs):
        # rate stores
        rated_stores = PSSmart.rate_stores(preselected_stores, item)
        best_rating = min(rated_stores, key=int) if len(rated_stores) > 0 else -1

        # step 1A: try to use available slot on line store
        target_store = rated_stores[best_rating][0] if best_rating > -1 else None

        # step 1B: try to make use of direct areas (buffer areas) if available
        if target_store is None or best_rating > 0:
            available_direct_stores = [
                direct_area
                for direct_area in parking_area_group.direct_areas
                if direct_area in preselected_stores and direct_area.vacant
            ]
            if len(available_direct_stores) > 0:
                target_store = available_direct_stores[0]

        return target_store

    @staticmethod
    def rate_stores(preselected_stores, vehicle):
        """
        Rate [preselected_stores] by accessibility for [vehicle] and.

        return a dict (key: result, value: list of stores having this result).

        Called by DSFirst.find_match.
        """
        result = {}
        for store in preselected_stores:
            # for store in [lineStore for lineStore in preselected_stores]:
            # for store in [lineStore for lineStore in preselected_stores if type(lineStore) == LineArea]:
            store_result = PSSmart.rate_store(store, vehicle)
            if store_result is not None:
                if store_result not in result:
                    result[store_result] = []
                result[store_result].append(store)

        return result

    @staticmethod
    def rate_store(store, vehicle):
        """
        Rate [store] by accessibility for [vehicle]: 0 means directly.

        accessible, higher values mean lower accessibility (lower => better).

        Called by DSFirst.rate_stores.

        Might be expanded in the future.
        """
        if not store.vacant_accessible:
            return None
        else:
            result = 0
            pending_vehicles = store.pendingVehicles[::-1]

            if len(pending_vehicles) > 0:
                # Check if store would be a typestack (e.g. GN, GN, GN ...),
                # only considering pending vehicles
                typestack = True

                if vehicle.vehicle_type.group is not None:
                    for otherVehicle in pending_vehicles:
                        if (
                            otherVehicle.vehicle_type is not vehicle.vehicle_type
                            and otherVehicle.vehicle_type
                            not in vehicle.vehicle_type.group.types
                        ):
                            typestack = False
                            break

                else:
                    for otherVehicle in pending_vehicles:
                        if otherVehicle.vehicle_type is not vehicle.vehicle_type:
                            typestack = False
                            break

                if typestack:
                    # ... if yes: count vehicles which have less battery than
                    # the current vehicle to park
                    while (
                        result < len(pending_vehicles)
                        and pending_vehicles[result].battery.energy
                        < vehicle.battery.energy
                    ):
                        result = result + 1
                else:
                    # ... if not: count all vehicles
                    result = result + len(pending_vehicles)

            return result


class PSSmart2(BaseParkingStrategy):
    """"""

    name = "SMART2"
    short_description = "smart2"
    tooltip = "Select a slot using ParkRating."

    @staticmethod
    def determine_store(preselected_stores, parking_area_group, item, *args, **kwargs):
        # Remove stores without an accessible slot from preselected_stores
        preselected_stores = [
            store for store in preselected_stores if store.vacant_accessible
        ]
        if preselected_stores:
            rating = ParkRating(
                [
                    SlotAlternative(
                        (
                            store,
                            store.index_put()
                            if isinstance(store, LineArea)
                            else store.items.index(None),
                        ),
                        item,
                        parking_area_group.max_power,
                        parking_area_group.max_capacity_line,
                    )
                    for store in preselected_stores
                ]
            )

            # print()
            # flexprint('ParkRating for vehicle %s:' % item.ID, env=item.dwd.env)
            # for alt in rating.alternatives_obj:
            #     print('\t\tArea %s (type %s) index %d' % (alt.slot[0].ID, alt.slot[0].typename, alt.slot[1]))
            #     print('\t\t\tbuffer: value=%s' % alt.buffer.value)
            #     print('\t\t\ttypestack: value=%s' % alt.typestack.value)
            #     print('\t\t\trfd_diff: value=%s, diff=%s' % (alt.rfd_diff.value, alt.rfd_diff.diff))
            #     print('\t\t\trfd_diff_pos: value=%s' % alt.rfd_diff_pos)
            #     print('\t\t\trfd_diff_neg: value=%s' % alt.rfd_diff_neg)
            #     print('\t\t\tavailable_power: value=%s' % alt.available_power.value)
            #     print('\t\t\tempty_slots_exit: value=%s' % alt.empty_slots_exit.value)
            #     print('\t\t\t__________')
            # print('\trating result:')
            # print('\t\tweighted: %s' % rating.weighted)
            # print('\t\tsums: %s' % rating.sums)
            # print('\t\tbest_value: %s' % rating.best_value)
            # print('\t\tbest_alternative_nos: ', rating.best_alternative_nos)
            # print('\t\tbest_alternatives: %s' % rating.best_alternatives)
            # print('\t\tbest area: %s' % preselected_stores[rating.best_alternative_nos[0][0]].ID)

            PSSmart2.log_best(parking_area_group, rating)
            return preselected_stores[rating.best_alternative_nos[0][0]]

        else:
            PSSmart2.log_best(parking_area_group, None)
            return None

    @staticmethod
    def log_best(parking_area_group, rating):
        """Add the best value of a rating to parking_area_group.pssmart2_logs.

        rating: [ParkRating]
        """
        if globalConstants["general"]["LOG_ATTRIBUTES"]:
            env = parking_area_group.env
            if env.now not in parking_area_group.pssmart2_logs:
                parking_area_group.pssmart2_logs[env.now] = []

            if rating is None:
                parking_area_group.pssmart2_logs[env.now].append(None)
            else:
                parking_area_group.pssmart2_logs[env.now].append(rating.best_value)


class AreaGroup(StoreConnector):
    """Class for grouping depot areas.

    Parameters:
    stores: [list] of BaseArea subclass objects

    Attributes:
    stores_by_vehicle_type: [dict]
    direct_areas: [list] of areas of type DirectArea in self.stores
    line_areas: [list] of areas of type LineArea in self.stores
    max_capacity_line: [int] maximum capacity among Line areas in this group
    capacity_direct: [int] total cacpacity of Direct areas in this group
    capacity_line: [int] total cacpacity of Line areas in this group
    """

    def __init__(self, env, stores, ID):
        self.stores_by_vehicle_type = {}
        self.direct_areas = []
        self.line_areas = []
        self.max_capacity_line = None  # set in self.update_defaults
        self.capacity_direct = 0
        self.capacity_line = 0

        super(AreaGroup, self).__init__(env, stores)
        self.ID = ID

    def clear(self):
        """Remove all entries from self.stores and update.

        self.depot is
        unaffected.
        """
        super().clear()

    def check_entry_filters(self, vehicle):
        """Return a list of booleans that are the results of entry permission.

        checks at areas in self.stores. Has the same length as
        self.stores.
        """
        permissions = [area.entry_filter(vehicle) for area in self.stores]
        return permissions

    def update_defaults(self):
        """Update attributes after relevant changes such as the amount of areas.

        in the group.
        """
        self.default_selection_put = [True] * len(self.stores)
        self.default_selection_get = [True] * len(self.stores)

        self.stores_by_vehicle_type = {}
        self.direct_areas = []
        self.line_areas = []
        max_capacity_line = float("-inf")

        for store in self._stores:
            if hasattr(store.entry_filter, "vehicle_types"):
                vehicle_types = store.entry_filter.vehicle_types.copy()
            else:
                vehicle_types = []
            if not vehicle_types:
                vehicle_types.append(None)
            for vehicle_type in vehicle_types:
                if vehicle_type not in self.stores_by_vehicle_type:
                    self.stores_by_vehicle_type[vehicle_type] = []
                self.stores_by_vehicle_type[vehicle_type].append(store)

            if isinstance(store, LineArea):
                self.line_areas.append(store)
                self.capacity_line += store.capacity
                if store.capacity > max_capacity_line:
                    max_capacity_line = store.capacity
            else:
                self.capacity_direct += store.capacity
                self.direct_areas.append(store)

            self.capacity += store.capacity

        if max_capacity_line > float("-inf"):
            self.max_capacity_line = max_capacity_line
        else:
            self.max_capacity_line = None


class ParkingAreaGroup(AreaGroup):
    """Group specifically for parking areas.

    Provides various algorithms to
    decide in which area to put a vehicle.

    Parameters:
    parking_strategy_name: [str] name as in parking strategy class attributes.

    Attributes:
    parking_strategy: [BaseParkingStrategy] subclass
    max_power, min_power: [int or float or None] min and max of max_power of
        all charging interfaces at areas in this group. Constant, set before
        simulation start. None if there are no charging interfaces.
    put_queue: [dict] Container for keeping count and time of pending put
        requests to the group. Provisional, may be changed.
    """

    parking_strategies = {
        PSFirst.name: PSFirst,
        PSEven.name: PSEven,
        PSMixed.name: PSMixed,
        PSSmart.name: PSSmart,
        PSSmart2.name: PSSmart2,
    }

    def __init__(self, env, stores, ID, parking_strategy_name="FIRST"):
        self.max_power = None  # set in self.update_power_extrema
        self.min_power = None  # set in self.update_power_extrema

        super(ParkingAreaGroup, self).__init__(env, stores, ID)

        self._parking_strategy_name = None
        self._parking_strategy = None
        self.parking_strategy_name = parking_strategy_name

        self.pssmart2_logs = {}
        self.put_queue = {}

    @property
    def stores(self):
        return self._stores

    @stores.setter
    def stores(self, value):
        self._stores = value
        for store in value:
            if not store.issink:
                raise ValueError(
                    "issink must be True for all areas in a " "parking area group."
                )
            store.parking_area_group = self
        self.update_defaults()
        self.update_power_extrema()

    @property
    def parking_strategy_name(self):
        """'name' attribute of set parking strategy."""
        return self._parking_strategy_name

    @parking_strategy_name.setter
    def parking_strategy_name(self, value):
        if value not in self.parking_strategies:
            raise ValueError("Invalid parking strategy '%s'" % value)
        self._parking_strategy_name = value
        self._parking_strategy = self.parking_strategies[value]

    @property
    def parking_strategy(self):
        """[BaseParkingStrategy] subclass."""
        return self._parking_strategy

    @parking_strategy.setter
    def parking_strategy(self, value):
        self._parking_strategy = value
        self._parking_strategy_name = next(
            name for name, cls in self.parking_strategies.items() if cls is value
        )

    def add_store(self, store):
        """Add an area to this group."""
        if not store.issink:
            raise ValueError(
                "issink must be True for all areas in a " "parking area group."
            )
        if store not in self.stores:
            self.stores.append(store)
            self.update_defaults()
            self.update_power_extrema()
            store.parking_area_group = self

    def remove_store(self, store):
        """Remove an area from this group."""
        if store in self.stores:
            self.stores.remove(store)
            self.update_defaults()
            self.update_power_extrema()
            store.parking_area_group = None

    def clear(self):
        """Remove all entries from self.stores and update.

        self.depot,
        self.depot.parking_area_groups and self.parking_strategy are
        unaffected.
        """
        # Release areas
        for area in self.stores:
            area.parking_area_group = None

        self.stores.clear()
        self.update_defaults()
        self.update_power_extrema()

    def update_power_extrema(self):
        """Setter for self.max_power and self.min_power."""
        min_power = float("inf")
        max_power = float("-inf")

        # Added compatibility for areas without charging interfaces
        for store in self.stores:
            if store.charging_interfaces is not None:
                for ci in store.charging_interfaces:
                    if ci.max_power < min_power:
                        min_power = ci.max_power
                    if ci.max_power > max_power:
                        max_power = ci.max_power

                if min_power < float("inf"):
                    self.min_power = min_power
                else:
                    self.min_power = None
                if max_power > float("-inf"):
                    self.max_power = max_power
                else:
                    self.max_power = None

            else:
                min_power = None
                max_power = None

    def put(self, item, selection=None):
        """Summarize put_imm and put_wait.

        A parking strategy is applied here.

        In this method strategies can be determined before calling the actual
        put() methods, e.g. by modifying the *selection* parameter.

        selection: [list] of booleans matching the length of self.stores.
            Specifies which areas are excluded from consideration even before
            applying a parking strategy.
        """
        # Log unique object to log in self.put_queue
        req_tracker = object()
        self.put_queue[req_tracker] = self.env.now

        if selection is None:
            selection = self.default_selection_put

        preselected_stores = [st for st, flag in zip(self.stores, selection) if flag]

        # workaround due to an error (wip)
        for store in preselected_stores:
            store._trigger_put(None)
        for store in preselected_stores:
            store._trigger_put(None)

        target_store = self.parking_strategy.determine_store(
            preselected_stores=preselected_stores, parking_area_group=self, item=item
        )

        if target_store is not None:
            # Parking strategy found a slot
            vacant_accessible_before = target_store.vacant_accessible
            req = target_store.put(item)
            # assert req.triggered, (target_store, target_store.items, target_store.capacity, target_store.vacant_accessible, vacant_accessible_before)    # strategy must return an immediately accessible slot
            yield req

            del self.put_queue[req_tracker]
            return target_store

        else:
            # No suitable slot was immediately available. Look for another slot
            # with selection as single requirement.
            flexprint(
                "Calling put_imm for vehicle %s" % item.ID,
                env=self.env,
                switch="objID",
                objID=item.ID,
            )
            store, requests = yield self.env.process(self.put_imm(item, selection))
            flexprint(
                "\tput_imm result: %s. Len requests: %d"
                % (None if store is None else store.ID, len(requests)),
                switch="objID",
                objID=item.ID,
            )
            if store is None:
                store = yield self.env.process(self.put_wait(requests))

            del self.put_queue[req_tracker]
            return store


class BaseActivityPlan(ABC):
    """Base class for the guidance of a vehicle inside a depot.

    A copied instance is assigned to a vehicle at check-in.

    Parameters:
    locations: [list] of DepotArea or AreaGroup objects
    """

    def __init__(self, ID, locations=None, vehicle_filter=None):
        self.ID = ID
        self.locations = locations if locations is not None else []
        self.vehicle_filter = (
            vehicle_filter if vehicle_filter is not None else VehicleFilter()
        )

    def __len__(self):
        return len(self.locations)

    def __getitem__(self, x):
        return self.locations[x]

    def __setitem__(self, i, x):
        self.locations[i] = x

    def __delitem__(self, i):
        del self.locations[i]

    def __iter__(self):
        return iter(self.locations)

    def __reversed__(self):
        return reversed(self.locations)

    def __str__(self):
        return str(self.locations)

    def append(self, x):
        self.locations.append(x)

    def extend(self, l):
        self.locations.extend(l)

    def remove(self, x):
        self.locations.remove(x)

    def pop(self, i=-1):
        return self.locations.pop(i)

    def clear(self):
        self.locations.clear()

    def copy(self):
        """Return a new object with the same locations, ID and vehicle_filter."""
        return type(self)(self.ID, self.locations[:], self.vehicle_filter)


class DefaultActivityPlan(BaseActivityPlan):
    """Default plan applicable for every vehicle.

    One plan is mandatory for a
    depot configuration.
    """

    def __init__(self, locations=None):
        super(DefaultActivityPlan, self).__init__("default", locations)

    def copy(self):
        """Return a new object with the same locations, ID and vehicle_filter."""
        return DefaultActivityPlan(self.locations[:])


class SpecificActivityPlan(BaseActivityPlan):
    """Plan that is applicable to vehicles passing vehicle_filter only.

    A depot
    configuration may include zero or more specific plans.
    """

    def __init__(self, ID, locations=None, vehicle_filter=None):
        super(SpecificActivityPlan, self).__init__(ID, locations, vehicle_filter)
