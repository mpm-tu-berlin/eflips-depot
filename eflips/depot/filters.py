# -*- coding: utf-8 -*-
import warnings

from eflips.settings import globalConstants

import eflips


class VehicleFilter:
    """Class to build and apply filters for vehicles. Designed to work with
    simpy FilterStore and subclasses in the depot simulation: Implements
    __call__ to make an instance directly callable and return the all()-result
    of the criteria. Can be recalled. Usable e.g. for simpyExt.FilterStoreExt
    that needs the store item as single positional argument for the filter
    function. Filter criteria are prepared in class methods and can be selected
    and combined before application.

    Parameters:
    filter_names: [list or None] of filter method names (as strings, 'filter_'
        omitted) of this class that should be applied on a vehicle. If left
        None, the VehicleFilter returns True.
    All other parameters that are required by selected filter methods must be
        added as keyword arguments during init (see method descriptions).

    Attributes:
    filters: [list] of one or more VehicleFilter.filter_... methods or other
        valid callables. Filled automatically from *filter_names*. Can be None
        or empty, in which case the VehicleFilter returns True. Entries can be
        added during definition (see filter_names) and afterwards (see
        VehicleFilter.append).

    Usage:
        Instantiate a VehicleFilter object. Pass arguments required for the
        checks, such as filter_names upon instantiation as kwargs or add them
        later.
        Example:
            vf = VehicleFilter(filter_names=['service_need'], env=env)
            trueorfalse = vf(vehicle)
        Static methods may be called without instantiation. Example:
            trueorfalse = VehicleFilter.filter_no_active_processes(vehicle)

    When adding a new filter method:
    - The method name must be preceeded by 'filter_'.
    - 'vehicle' must be the only method parameter. Additional parameters can be
        accessed via the VehicleFilter object attributes. Passing all those
        parameters as keyword arguments during init adds them automatically as
        attribute of the VehicleFilter. Alternatively, attributes might be
        added after init, before calling the filter.

    """

    def __init__(self, filter_names=None, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

        self.filter_names = filter_names
        if self.filter_names is None:
            self.filter_names = []
        elif not isinstance(filter_names, list):
            raise ValueError(
                "VehicleFilter 'filter_names' must be of type "
                "list, not %s" % type(filter_names)
            )

        self.vehicle_types_converted = False

        if hasattr(self, "period"):
            if (
                not isinstance(self.period, tuple)
                and not isinstance(self.period, list)
                or len(self.period) != 2
                or not 0 <= self.period[0] <= 86400
                or not 0 <= self.period[1] <= 86400
                or self.period[0] == self.period[1]
            ):
                raise ValueError(
                    "Invalid VehicleFilter.period. See "
                    "description of method filter_in_period for "
                    "instructions."
                )

        self.filters = []
        self.filters_from_names()

    def __call__(self, vehicle):
        """Method that actually applies filters. Return True if all of
        self.filter return True or self.filters is empty."""
        result = all(filter(vehicle) for filter in self.filters)
        return result

    def any(self, vehicle):
        """Return True if any of the filters in self.filters is True or
        self.filters is empty. Optional method in addition to the standard
        usage with logical all which is implemented in __call__."""
        result = any(filter(vehicle) for filter in self.filters)
        return result

    def filters_from_names(self):
        """Append references to methods specified in filter_names to
        self.filters. Extends the given strings by 'filter_' at the beginning,
        so that part does not have to be passed during instantiation.
        Filters can alternatively be added after instantiation using append().
        """
        for filter_name in self.filter_names:
            filter_name = "filter_" + filter_name
            if not hasattr(self, filter_name):
                raise ValueError(
                    'Method "%s" not found in class VehicleFilter' % filter_name
                )
            filter = getattr(self, filter_name)
            self.append(filter)

    def append(self, filter):
        """Append *filter* to self.filters. Filter must be a callable accepting
        vehicle as argument and returning a boolean value."""
        self.filters.append(filter)

    def filter_vehicle_type(self, vehicle):
        """Return True if the vehicle type matches ANY item in
        self.vehicle_types.

        Attributes required in self:
        vehicle_types: [list] of eflips.depot.simple_vehicle.VehicleType
            objects
        """
        self.get_vt_objects()
        return vehicle.vehicle_type in self.vehicle_types

    def get_vt_objects(self, force=False):
        """Helper for filter methods using self.vehicle_types. Get objects for
        strings in self.vehicle_types.
        If *force* is False and self.vehicle_types_converted is True, the
        conversion is skipped (useful for reusing the filter object with the
        same parameters). If *force* is true, the conversion is done even if
        self.vehicle_types_converted is True (useful for reusing the filter
        object with new parameters).

        Attributes required in self:
        vehicle_types: [list] of vehicle type IDs as strings or
            eflips.depot.simple_vehicle.VehicleType objects
        """
        if not self.vehicle_types_converted or force:
            # Get obj references of vehicle types
            try:
                self.vehicle_types_str = (
                    self.vehicle_types.copy()
                )  # backup the list of strings
            except AttributeError:
                raise AttributeError(
                    "filter_vehicle_type requires the "
                    "attribute vehicle_types to be set before call."
                )
            # print(self.vehicle_types, self.vehicle_types_str)
            for no, ID in enumerate(self.vehicle_types):
                if isinstance(ID, str):
                    try:
                        self.vehicle_types[no] = globalConstants["depot"][
                            "vehicle_types_obj_dict"
                        ][ID]
                    except KeyError:
                        raise KeyError(
                            "Vehicle type %s not found in globalConstants. "
                            "Possibly selected eflips settings and depot template "
                            "don't match." % ID
                        )
            self.vehicle_types_converted = True

    def filter_trip_vehicle_match(self, vehicle):
        """Return True if vehicle is suitable for self.trip (for vehicles that
        have not entered the system yet) or if vehicle and self.trip were
        matched by the dispatching.

        Attributes required in self:
        trip: eflips.depot.standalone.SimpleTrip object
        """
        return (
            not vehicle.system_entry
            and vehicle.vehicle_type in self.trip.vehicle_types
            or vehicle.trip is not None
            and vehicle.trip is self.trip
        )

    @staticmethod
    def filter_no_active_processes(vehicle):
        """Return True if the vehicle has no active processes."""
        return not vehicle.dwd.any_active_processes

    @staticmethod
    def filter_no_active_uncancellable_processes(vehicle):
        """Return True if the vehicle has no active processes or only those
        that can be cancelled for dispatch.
        """
        if not vehicle.dwd.any_active_processes:
            return True
        elif not vehicle.dwd.active_processes:
            # If active_processes is empty but any_active_processes is True, a
            # process might be about to start but is not listed in
            # active_processes yet. The check may return True one second later
            # if the process is cancellable.
            return False
        else:
            return all(
                proc.cancellable_for_dispatch for proc in vehicle.dwd.active_processes
            )

    @staticmethod
    def filter_bat_full(vehicle):
        """Return True if the vehicle's battery is full."""
        return vehicle.battery.soc == 1

    def filter_min_energy(self, vehicle):
        """Return True if the remaining energy of the vehicle's battery is at
        least self.min_energy.

        Attributes required in self:
        min_energy: [int] minimum battery level
        """
        return self.min_energy <= vehicle.battery.energy_remaining

    def filter_sufficient_energy(self, vehicle):
        """Return True if the remaining energy of the vehicle's battery plus a
        reserve is sufficient for the trip.

        Attributes required in self:
        trip: eflips.depot.standalone.SimpleTrip object
        """
        if globalConstants["depot"]["consumption_calc_mode"] == "CR_distance_based":
            energy_reserve = globalConstants["depot"]["energy_reserve"]
            required_energy = (
                vehicle.vehicle_type.CR
                * self.trip.distance
                * (1 + (energy_reserve / 100))
            )
            result = required_energy <= vehicle.battery.energy_remaining

        elif globalConstants["depot"]["consumption_calc_mode"] == "CR_time_based":
            energy_reserve = globalConstants["depot"]["energy_reserve"]
            required_energy = (
                self.trip.duration
                / 3600
                * vehicle.vehicle_type.CR
                * (1 + (energy_reserve / 100))
            )
            result = required_energy <= vehicle.battery.energy_remaining

        elif globalConstants["depot"]["consumption_calc_mode"] == "soc_given":
            if self.trip.charge_on_track:
                result = round(vehicle.battery.soc, 5) >= self.trip.minimal_soc
            else:
                required_energy = (
                    self.trip.start_soc - self.trip.end_soc
                ) * vehicle.battery.energy_real

                # If the vehicle is fully charged and its fully charged energy is still lower than the required energy,
                # dispatch anyway and warn the user
                if (
                    abs(vehicle.battery.soc - 1) < 1e-6
                    and vehicle.battery.energy_real < required_energy
                ):
                    warnings.warn(
                        f"Vehicle {vehicle.ID} is fully charged but the required energy for the trip is higher than the fully charged energy. Dispatching anyway."
                    )
                    return True

                result = required_energy <= vehicle.battery.energy_remaining

        else:
            raise ValueError(
                "Invalid value %s for 'consumption_calc_mode' in globalConstants."
                % globalConstants["depot"]["consumption_calc_mode"]
            )

        # flexprint(
        #     '\tVehicle %s in battery level check. result: %s. Battery energy_remaining: %d (SoC=%.3f). Trip.energy need: %d'
        #     % (vehicle.ID, result, vehicle.battery.energy_remaining, vehicle.battery.soc,
        #        required_energy), switch='departure_before_fully_charged')
        return result

    def filter_soc_lower_than(self, vehicle):
        """Return True if the soc of the vehicle's battery is lower than
        self.soc.

        Attributes required in self:
        soc: [float]
        """
        return vehicle.battery.soc < self.soc

    def filter_service_need(self, vehicle):
        """Return True if the vehicle needs service, i.e. the cumulated trip
        time since last service is greater than self.service_need_td or the
        elapsed time since last service is greater than
        self.service_need_t_elapsed.
        This filter is one option to check the service need. An independent
        alternative is filter_in_period.

        Attributes required in self:
        env: simpy.Environment object
        service_need_td: [int] amount of driving time [seconds] since last
          service from which service_need will be set to True when exceeded.
        service_need_t_elapsed: [int] amount of elapsed time [seconds] since
            last service from which service_need will be set to True when
            exceeded.
        """
        # Skip checks if service_need already is True
        if not vehicle.dwd.service_need:
            t_trips = 0
            # Get cumulated trip time since last service
            for trip in reversed(vehicle.finished_trips):
                if trip.atd > vehicle.dwd.t_lastServed:
                    t_trips += trip.ata - trip.atd
                else:
                    break
            if (
                t_trips > self.service_need_td
                or self.env.now - vehicle.dwd.t_lastServed > self.service_need_t_elapsed
            ):
                vehicle.dwd.service_need = True
        return vehicle.dwd.service_need

    @staticmethod
    def filter_repair_need(vehicle):
        """Return True if the vehicle needs repair."""
        return vehicle.dwd.repair_need

    @staticmethod
    def filter_maintenance_need(vehicle):
        """Return True if the vehicle needs maintenance."""
        return vehicle.dwd.maintenance_need

    def filter_in_period(self, vehicle):
        """Return True if self.now as a daytime is in self.period.

        Attributes required in self:
        env: simpy.Environment object
        period: [tuple or list] daytime period (t0, t1) for
            filter_in_period. t0 and t1 are seconds with 0 < t < 86400. t1 may
            be lower than t0 to specify periods including midnight. t0 and t1
            cannot be equal. (reason for list is enabling direct json input)
        """
        t0 = self.period[0]
        t1 = self.period[1]
        now = self.env.now % 86400  # get daytime in seconds
        if t0 < t1:
            return t0 <= now <= t1
        else:
            return t0 <= now <= 86400 or now <= t1

    def filter_in_period_days(self, vehicle):
        """Return True if the time is in period and it is the third, fourth,
        ... day.

        Attributes required in self:
        env: simpy.Environment object
        after_day: [int] determines in how many days a service has to be
            done. For example every third day.
        """
        id = sum(
            [int(id) for id in vehicle.ID.split() if id.isdigit()]
        )  # transfers the str ID in a numeric id
        day = int(self.env.now / 86400) + 1
        if id % self.after_day == day % self.after_day and self.filter_in_period(
            vehicle
        ):
            return True

    @staticmethod
    def filter_isunblocked(vehicle):
        if isinstance(vehicle.dwd.current_area, eflips.depot.DirectArea):
            result = True
        else:
            result = vehicle.dwd.current_area.isunblocked(vehicle)
        return result

    @staticmethod
    def filter_not_on_hold(vehicle):
        """Return True if *vehicle* is not marked to be on hold."""
        return not vehicle.dwd.on_hold

    def filter_dwd_previous_area(self, vehicle):
        """Check if current area is in none_of_previous_areas.

        Attributes required in self:
        none_of_previous_areas: [list] Skip current area if previous
            area in none_of_previous_areas.
        """
        return vehicle.dwd.current_area.ID not in self.none_of_previous_areas

    @staticmethod
    def filter_false(vehicle):
        """Always return False."""
        return False
