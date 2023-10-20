# -*- coding: utf-8 -*-
"""
Created on Thu Sep  7 09:31:54 2017

@author: e.lauth, p.mundt

Components for the configuration of a depot.

"""
import json
import os
from os.path import basename
from typing import Dict

import eflips
from eflips.depot.depot import Depot, LineArea, ParkingAreaGroup, SpecificActivityPlan
from eflips.depot.filters import VehicleFilter
from eflips.depot.resources import DepotResource, DepotChargingInterface, ResourceSwitch
from eflips.depot.processes import ChargeSteps
from eflips.evaluation import DataLogger
from eflips.helperFunctions import load_json, save_json


class DepotConfigurator:
    """Utilities to:

    - create a depot
    - add/remove resources, resource switches, processes, areas, groups and plans to/from a depot
    - load and save a json depot template before simulation start.

    Before env.run is called, complete() must be called.

    Configuration-related errors are not raised. Instead, the current action is
    cancelled and a prepared error message is returned to enable the sender to
    decide on further actions (e.g. show an info popup in GUI).

    To add/remove areas to/from groups, use AreaGroup or ParkingAreaGroup
    methods.

    Attributes:

    :param filename_loaded: [None or str] filename of the imported template. None
        until loading. Stays the same even if templatename changes.
    :param templatename: [str] filename of the imported template, excluding the path.
        May be changed manually. Used to create export filenames.
    :param templatename_display: [str] "pretty" version of templatename.
    :param multiplied_areas: Map for connecting area IDs added with 'amount' with
        their resulting areas.

    """

    basemsg_invalid = "Invalid depot configuration."

    def __init__(self, env):
        self.env = env
        self.depot = Depot(self.env, "New depot")

        self.filename_loaded = None
        self.templatename = "New depot"
        self.templatename_display = ""

        self.multiplied_areas = {}

        self.completed = False

    @property
    def isvalid(self):
        """Check if the current depot configuration is valid.
        Return (True, None) if valid, otherwise (False, errormsg).
        """
        # Check if there is at least one area
        if not self.depot.areas:
            errormsg = (
                self.basemsg_invalid + "Cannot start simulation with empty depot. At "
                "least one Area must be specified."
            )
            return False, errormsg

        # Check if there is at least one parking area group
        if not self.depot.parking_area_groups:
            errormsg = (
                self.basemsg_invalid
                + "At least one parking area group must be specified."
            )
            return False, errormsg

        # Check if there are empty groups (invalid)
        for group in self.depot.groups.values():
            if not group.stores:
                errormsg = (
                    self.basemsg_invalid
                    + 'Group "%s" must contain at least one Area.' % group.ID
                )
                return False, errormsg

        # Check if there is a default plan
        if self.depot.default_plan is None:
            errormsg = self.basemsg_invalid + "A DefaultActivityPlan must be specified."
            return False, errormsg

        # Check if there are empty plans (invalid)
        if not self.depot.default_plan:
            errormsg = (
                self.basemsg_invalid
                + "DefaultActivityPlan %s cannot be empty." % self.depot.default_plan.ID
            )
            return False, errormsg
        for seq in self.depot.specific_plans:
            if not seq:
                errormsg = (
                    self.basemsg_invalid
                    + "SpecificActivityPlan %s cannot be empty." % seq.ID
                )
                return False, errormsg

        # Check if resource_switches have a resource and breaks
        for sw in self.depot.resource_switches.values():
            if sw.resource is None:
                errormsg = "No resource set for ResourceSwitch %s." % sw.ID
                return False, errormsg
            if not sw.breaks:
                errormsg = "ResourceSwitch %s breaks cannot be empty." % sw.ID
                return False, errormsg

        # Check if Precondition uses higher power than any charging interface
        # can provide
        for process in self.depot.processes.values():
            if process["typename"] == "Precondition":
                power_precond = process["kwargs"]["power"]
                for resource in self.depot.resources.values():
                    if isinstance(resource, DepotChargingInterface):
                        if resource.max_power < power_precond:
                            errormsg = (
                                "Charging interface %s max_power is lower than Precondition power."
                                % resource.ID
                            )
                            return False, errormsg
                duration_precond = process["kwargs"]["dur"]
                if (
                    duration_precond
                    > eflips.globalConstants["depot"]["lead_time_match"]
                ):
                    errormsg = "A Precondition time is higher then the lead_time_match (settings)"
                    return False, errormsg

        # Check if ChargeSteps uses higher power than the charging interfaces
        # at areas can provide
        for process in self.depot.processes.values():
            if process["typename"] == "ChargeSteps":
                max_power_charge = max(i[1] for i in process["kwargs"]["steps"])
                for area in self.depot.areas.values():
                    if process["kwargs"]["ID"] in area.available_processes:
                        for chint in area.charging_interfaces:
                            if chint.max_power < max_power_charge:
                                errormsg = (
                                    "Charging interface %s max_power is lower than ChargeSteps %s max power."
                                    % (chint.ID, process["kwargs"]["ID"])
                                )
                                return False, errormsg

        return True, None

    def reset(self):
        """Reset all depot configuration variables.
        Guarantees stable references for dicts and lists, e.g. a dict is
        cleared instead of being overwritten with an empty one.

        [untested, perhaps incomplete]
        """
        self.filename_loaded = None
        self.templatename = "New depot"
        self.templatename_display = ""

        self.multiplied_areas.clear()

        self.depot.ID = "New depot"
        self.depot.resources.clear()
        self.depot.resource_switches.clear()
        self.depot.processes.clear()
        self.depot.areas.clear()
        self.depot.groups.clear()
        self.depot.default_plan = None
        self.depot.specific_plans.clear()

        self.depot.direct_departure_areas.clear()
        self.depot.parking_area_groups.clear()

        self.depot.capacity = 0
        self.depot.parking_capacity = 0
        self.depot.parking_capacity_direct = 0

        self.depot.depot_control.departure_areas.clear()
        self.depot.depot_control.dispatch_strategy_name = "FIRST"

    def add_resource(self, typename, **kwargs):
        """Instantiate a DepotResource object and add it to self.depot. All
        required parameters have to be passed as keyword arguments (see class
        definition for documentation).
        Return (resource object, None) if successful, otherwise (None,
        errormsg).
        """
        ID = kwargs["ID"]
        # Check if ID is unique
        if ID in self.depot.resources:
            errormsg = 'Invalid ID "%s". IDs must be unique among resources.' % ID
            return None, errormsg

        # Instantiate DepotResource
        cls = getattr(eflips.depot.resources, typename)
        resource = cls(env=self.env, depot=self.depot, **kwargs)
        self.depot.resources[ID] = resource
        return resource, None

    def remove_resource(self, ID):
        """Remove resource with *ID*.
        Return removed_from_special [list], containing processes that the
        resource was removed from.
        """
        removed_from_special = []
        resource = self.depot.resources[ID]

        # From processes
        for process in self.depot.processes.values():
            if resource in process.required_resources:
                process.required_resources.remove(resource)
                removed_from_special.append(process)

        # From resource switches
        for switch in self.depot.resource_switches.values():
            if resource is switch.resource:
                switch.resource = None

        del self.depot.resources[ID]
        return removed_from_special

    @staticmethod
    def export_resource(resource):
        """Return a dict that represents the configuration of *resource*."""
        data = {"typename": type(resource).__name__}
        if isinstance(resource, DepotResource):
            data["capacity"] = resource.capacity
        if isinstance(resource, DepotChargingInterface):
            data["max_power"] = resource.max_power
        return data

    def add_resource_switch(self, **kwargs):
        """Instantiate a ResourceSwitch object and add it to self.depot. All
        required parameters have to be passed as keyword arguments (see class
        definition for documentation).
        Parameter "resource" must be a string. The reference is resolved here.
        Return (resource_switch object, None) if successful, otherwise (None,
        errormsg).
        """
        ID = kwargs["ID"]
        # Check if ID is unique
        if ID in self.depot.resource_switches:
            errormsg = (
                'Invalid ID "%s". IDs must be unique among ' "resource_switches." % ID
            )
            return None, errormsg

        # Check if resource exists
        if kwargs["resource"] not in self.depot.resources:
            errormsg = (
                'Resource "%s" not found, but used for Resource '
                'Switch "%s".' % (kwargs["resource"], ID)
            )
            return None, errormsg

        kwargs["resource"] = self.depot.resources[kwargs["resource"]]

        # Instantiate ResourceSwitch
        resource_switch = ResourceSwitch(env=self.env, **kwargs)
        self.depot.resource_switches[ID] = resource_switch

        return resource_switch, None

    def remove_resource_switch(self, ID):
        """Remove resource_switch with *ID*. A related resource is not deleted."""
        del self.depot.resource_switches[ID]
        return []

    @staticmethod
    def export_resource_switch(resource_switch):
        """Return a dict that represents the configuration of *resource_switch*."""
        return {
            "resource": resource_switch.resource.ID,
            "breaks": resource_switch.breaks,
            "preempt": resource_switch.preempt,
            "strength": resource_switch.strength_input,
        }

    def add_process(self, typename, **kwargs):
        """Prepare process data for instantiation, which happens during
        simulation.
        *typename* must be a key in eflips.depot.processes.
        Return (process data dict, None) if successful, otherwise (None,
        errormsg).
        """
        ID = kwargs["ID"]
        # Check if ID is unique
        if ID in self.depot.processes:
            errormsg = 'Invalid ID "%s". IDs must be unique among processes.' % ID
            return None, errormsg

        self.depot.processes[ID] = {}
        self.depot.processes[ID]["kwargs"] = kwargs

        # Convert typename from str to an actual class reference. The name is
        # preserved
        cls = getattr(eflips.depot.processes, typename)
        self.depot.processes[ID]["type"] = cls
        self.depot.processes[ID]["typename"] = typename

        if cls is ChargeSteps:
            ChargeSteps.check_steps(kwargs["steps"])

        # Instantiate VehicleFilter, if any
        if "vehicle_filter" in kwargs and kwargs["vehicle_filter"] is not None:
            vf = kwargs["vehicle_filter"]
            kwargs["vehicle_filter"] = VehicleFilter(env=self.env, **vf)

        # Convert required_resources from list of str to list of references
        if "required_resources" in kwargs and kwargs["required_resources"]:
            reqRes = []
            for resID in kwargs["required_resources"]:
                if resID in self.depot.resources:
                    reqRes.append(self.depot.resources[resID])
                else:
                    errormsg = (
                        'Resource "%s" not found, but used for '
                        'Process "%s".' % (resID, ID)
                    )
                    return None, errormsg
            kwargs["required_resources"] = reqRes

        return self.depot.processes[ID], None

    def remove_process(self, ID):
        """Remove process with *ID*. A related resource is not deleted."""
        removed_from_special = []
        # From areas
        for area in self.depot.areas.values():
            if ID in area.available_processes:
                area.available_processes.remove(ID)
                removed_from_special.append(area)

        del self.depot.processes[ID]
        return removed_from_special

    def export_process(self, procdata):
        """Return procdata in an export format."""
        # Create new dict where entries of subdict 'kwargs' are on first level
        procdata_export = {}
        procdata_export["typename"] = procdata["typename"]
        procdata_export.update(procdata["kwargs"])
        # Convert VehicleFilter object to dict representation
        procdata_export["vehicle_filter"] = self.export_vehicle_filter(
            procdata["kwargs"]["vehicle_filter"]
        )
        # Convert required_resources from list of references to list of str
        if "required_resources" in procdata["kwargs"]:
            procdata_export["required_resources"] = [
                res.ID for res in procdata["kwargs"]["required_resources"]
            ]
        del procdata_export["ID"]

        return procdata_export

    def add_area(self, typename, **kwargs):
        """Call self._add_area once if kwargs['amount'] is 1 or missing.
        If amount > 1, create sub-IDs and call self._add_area amount number of
        times. amount is removed from kwargs.
        Return (added_areas, None) if successful, otherwise (None, errormsg).
        added_areas is a list that contains all added areas from this method
        call.
        """
        origID = kwargs["ID"]
        # Check if ID is unique
        if (
            origID in self.depot.areas
            or origID in self.depot.groups
            or origID in self.multiplied_areas
        ):
            errormsg = (
                'Invalid area ID "%s". IDs must be unique among '
                "areas and groups." % origID
            )
            return None, errormsg

        # Check how many areas should be added (1 if amount is not given)
        amount = kwargs.pop("amount", 1)

        if amount > 1:
            raise ValueError(
                "Instantiation of areas with amount > 1 is disabled due to "
                "the direct assignment of charging interfaces."
            )
            # Add amount number of areas
            self.multiplied_areas[origID] = []

            for i in range(amount):
                ID_i = origID + "_" + str(i + 1)

                # Check if ID_i is unique
                if ID_i in self.depot.areas or ID_i in self.depot.groups:
                    errormsg = (
                        'Invalid area ID "%s". IDs must be '
                        "unique among areas and groups." % origID
                    )
                    return None, errormsg

                kwargs["ID"] = ID_i
                area, errormsg = self._add_area(typename, **kwargs)
                if area is None:
                    return None, errormsg
                else:
                    self.multiplied_areas[origID].append(area)

            return self.multiplied_areas[origID], None

        else:
            # Add one area
            area, errormsg = area, errormsg = self._add_area(typename, **kwargs)
            if area is None:
                return None, errormsg
            else:
                return [area], errormsg

    def _add_area(self, typename, **kwargs):
        """Instantiate a DirectArea or LineArea object and add it to
        self.depot. All required parameters have to be passed as keyword
        arguments (see class definition for documentation).
        Parameter 'available_processes' must be a list of str.
        Return (area, None) if successful, otherwise (None, errormsg).

        Should only be called through self.add_area.
        """
        ID = kwargs["ID"]

        # Instantiate VehicleFilter, if any
        vf = kwargs["entry_filter"]
        if vf is not None:
            kwargs["entry_filter"] = VehicleFilter(env=self.env, **vf)

        # Check for process validity. Entries stay str
        available_processes = kwargs["available_processes"]
        for procID in available_processes:
            if procID not in self.depot.processes:
                errormsg = 'Process "%s" not found, but used for Area ' '"%s".' % (
                    procID,
                    ID,
                )
                return None, errormsg

        # Add charging interfaces, if any
        if "charging_interfaces" in kwargs and kwargs["charging_interfaces"]:
            charging_interfaces = []
            for ciID in kwargs["charging_interfaces"]:
                if ciID in self.depot.resources:
                    charging_interfaces.append(self.depot.resources[ciID])
                else:
                    errormsg = 'Resource "%s" not found, but used for ' 'Area "%s".' % (
                        ciID,
                        ID,
                    )
                    return None, errormsg
            kwargs["charging_interfaces"] = charging_interfaces

        # Instantiate area
        cls = getattr(eflips.depot.depot, typename)
        area = cls(env=self.env, **kwargs)
        self.depot.areas[ID] = area
        area.depot = self.depot

        # Add area to departure_areas if issink
        if area.issink:
            self.depot.depot_control.departure_areas.add_store(area)

        return area, None

    def remove_area(self, ID):
        """Remove area with *ID*.
        Return removed_from_special [list], containing groups and plans
        the area was removed from (excluding departure_areas and
        multiplied_areas because they are background activities).
        Related processes are not deleted.
        """
        removed_from_special = []
        area = self.depot.areas[ID]

        # From departure_areas (included if issink)
        if area in self.depot.depot_control.departure_areas.stores:
            self.depot.depot_control.departure_areas.remove_store(area)

        # From groups including parking area groups
        for group in self.depot.groups.values():
            if area in group.stores:
                group.remove_store(area)
                removed_from_special.append(group)

        # From plans
        if self.depot.default_plan is not None and area in self.depot.default_plan:
            self.depot.default_plan.remove(area)
            removed_from_special.append(self.depot.default_plan)
        for seq in self.depot.specific_plans:
            if area in seq:
                seq.remove(area)
                removed_from_special.append(seq)

        # From self.multiplied_areas
        for areas in self.multiplied_areas.values():
            if area in areas:
                areas.remove(area)

        del self.depot.areas[ID]
        return removed_from_special

    def export_area(self, area):
        """Return a dict that represents the configuration of *area*."""
        data = {
            "typename": type(area).__name__,
            "capacity": area.capacity,
            "charging_interfaces": [ci.ID for ci in area.charging_interfaces]
            if area.charging_interfaces is not None
            else [],
            "available_processes": area.available_processes,
            "issink": area.issink,
            "entry_filter": self.export_vehicle_filter(area.entry_filter),
        }
        if isinstance(area, LineArea):
            data["side_put_default"] = area.side_put_default
            data["side_get_default"] = area.side_get_default
        return data

    def add_group(self, typename, **kwargs):
        """Instantiate an AreaGroup object and add it to self.depot. All
        required parameters have to be passed as keyword arguments (see class
        definition for documentation).
        Parameter stores must be a list of str.
        Return (group object, None) if successful, otherwise (None, errormsg).
        """
        ID = kwargs["ID"]
        # Check if ID is unique
        if (
            ID in self.depot.areas
            or ID in self.depot.groups
            or ID in self.multiplied_areas
        ):
            errormsg = (
                'Invalid group ID "%s". IDs must be unique among '
                "areas and groups." % ID
            )
            return None, errormsg

        # Prepare stores, a list that may be composed of regular and
        # multiplied areas and handed over at instantiation of the group
        stores = []
        for areaID in kwargs["stores"]:
            if areaID in self.depot.areas:
                # Entry is a single area
                stores.append(self.depot.areas[areaID])

            elif areaID in self.multiplied_areas:
                # Area was multiplied. Add its resulting areas.
                stores.extend(self.multiplied_areas[areaID].copy())
            else:
                errormsg = 'Area "%s" not found, but used for Group ' '"%s".' % (
                    areaID,
                    ID,
                )
                return None, errormsg

        # Get class reference
        cls = getattr(eflips.depot.depot, typename)

        # Do checks on area(s) specific for parking area groups
        if cls is ParkingAreaGroup:
            for area in stores:
                check, errormsg = self.check_area_for_group(area, ID)
                if not check:
                    return None, errormsg

        kwargs["stores"] = stores
        # Instantiate group
        group = cls(env=self.env, **kwargs)
        self.depot.groups[ID] = group

        group.depot = self.depot

        # Specific actions for parking area groups
        if cls is ParkingAreaGroup:
            self.depot.parking_area_groups.append(group)
            for area in group.stores:
                area.parking_area_group = group

        return group, None

    @staticmethod
    def check_area_for_group(area, groupID):
        """Do checks on the validity of areas in parking area groups. Helper
        function for self.add_group.
        Return (True, None) if successful, otherwise (False, errormsg).
        """
        # Check if issink is True for the area (mandatory in ParkingAreaGroup)
        if not area.issink:
            errormsg = (
                "Area %s cannot be added to parking area group %s. "
                "Parking area groups may only contain areas where "
                "issink is True." % (area.ID, groupID)
            )
            return False, errormsg

        # Check if area already belongs to another parking area group
        # (conflict)
        if area.parking_area_group is not None:
            errormsg = (
                'Area "%s" cannot be added to assignmentgroup "%s" '
                "because it's still a member of parking area group "
                '"%s"' % (area.ID, groupID, area.parking_area_group.ID)
            )
            return False, errormsg

        return True, None

    def remove_group(self, ID):
        """Remove group with *ID*.
        Return removed_from_special [list], containing plans the group
        was removed from. Related areas are not deleted.
        """
        removed_from_special = []
        group = self.depot.groups[ID]

        # Remove and areas from group and release if type is ParkingAreaGroup
        group.clear()

        # From depot.parking_area_groups
        if isinstance(group, ParkingAreaGroup):
            self.depot.parking_area_groups.remove(group)

        # From plans
        if self.depot.default_plan is not None and group in self.depot.default_plan:
            self.depot.default_plan.remove(group)
            removed_from_special.append(self.depot.default_plan)
        for seq in self.depot.specific_plans:
            if group in seq:
                seq.remove(group)
                removed_from_special.append(seq)

        del self.depot.groups[ID]
        return removed_from_special

    @staticmethod
    def export_group(group):
        """Return a dict that represents the configuration of *group*."""
        data = {
            "typename": type(group).__name__,
            "stores": [store.ID for store in group.stores],
        }
        if isinstance(group, ParkingAreaGroup):
            data["parking_strategy_name"] = group.parking_strategy_name
        return data

    def add_plan(self, typename, **kwargs):
        """Instantiate an AreaGroup (or subclass) object and add it to
        self.depot. All required parameters have to be passed as keyword
        arguments (see class definition for documentation).
        Return (plan object, None) if successful, otherwise (None, errormsg).
        """
        ID = kwargs["ID"]
        # Check if ID is unique
        if (
            self.depot.default_plan is not None
            and self.depot.default_plan.ID == ID
            or ID in self.depot.specific_plans
        ):
            errormsg = 'Invalid ID "%s". IDs must be unique among plans.' % ID
            return None, errormsg

        else:
            # Get list of areas and groups
            locations, errormsg = self.get_locations(kwargs["locations"])
            if locations is None:
                return None, errormsg
            kwargs["locations"] = locations

            # Instantiate VehicleFilter for specific plans
            if "vehicle_filter" in kwargs and kwargs["vehicle_filter"] is not None:
                if typename == "DefaultActivityPlan":
                    errormsg = (
                        "ActivityPlans of type Default cannot have " "a vehicle_filter."
                    )
                    return None, errormsg
                else:
                    vf = kwargs["vehicle_filter"]
                    kwargs["vehicle_filter"] = VehicleFilter(env=self.env, **vf)

            # Instantiate plan
            cls = getattr(eflips.depot.depot, typename)
            if typename == "DefaultActivityPlan":
                del kwargs["ID"]
                plan = cls(**kwargs)
                self.depot.default_plan = plan
            else:
                plan = cls(**kwargs)
                self.depot.specific_plans[ID] = plan
            return plan, None

    def get_locations(self, IDs):
        """Compose a list of areas and groups based on IDs. Helper function for
        self.add_plan.
        """
        locations = []
        for ID in IDs:
            if ID in self.depot.areas:
                locations.append(self.depot.areas[ID])
            elif ID in self.depot.groups:
                locations.append(self.depot.groups[ID])
            else:
                errormsg = 'Area or Group "%s" not found. ' % ID
                return None, errormsg
        return locations, None

    def remove_plan(self, ID):
        """Remove plan with *ID* from default or specific plans.
        Related areas and groups are not deleted.
        """
        if self.depot.default_plan is not None and self.depot.default_plan.ID == ID:
            self.depot.default_plan = None

        elif ID in self.depot.specific_plans:
            del self.depot.specific_plans[ID]

        return []

    def export_plan(self, plan):
        """Return a dict that represents the configuration of *plan*."""
        data = {
            "typename": type(plan).__name__,
            "locations": [element.ID for element in plan],
        }
        if isinstance(plan, SpecificActivityPlan):
            data["vehicle_filter"] = self.export_vehicle_filter(plan.vehicle_filter)
        return data

    @staticmethod
    def export_vehicle_filter(vf):
        """Return a dict that represents the configuration of *vf*. Return None
        if all vehicles are permitted.
        """
        if vf is None or not vf.filter_names:
            return None
        else:
            data = vars(vf)
            del data["filters"]
            if "vehicle_types" in data:
                data["vehicle_types"] = data["vehicle_types_str"]
                del data["vehicle_types_str"]
            return data

    def load(self, template: Dict | str):
        """
        Load a depot configuration from a template the template may either be a
        - dict containing the configuration
        - a string containing a path without extension to a json file

        :param template: dict or str
        """
        self.reset()

        if isinstance(template, str):
            loaded_data = load_json(template)
            self.filename_loaded = template
            self.templatename = template.split("\\")[-1]
        elif isinstance(template, dict):
            loaded_data = template
            self.filename_loaded = "No filename"
            self.templatename = "Not template name"
        else:
            raise TypeError(
                "template must be a path-like object defining a JSON file or a dict."
            )

        self.templatename_display = loaded_data["templatename_display"]
        self.depot.ID = loaded_data["general"]["depotID"]
        self.depot.depot_control.dispatch_strategy_name = loaded_data["general"][
            "dispatch_strategy_name"
        ]

        # Import resources
        for k in loaded_data["resources"]:
            data = loaded_data["resources"][k]
            resource, errormsg = self.add_resource(data.pop("typename"), ID=k, **data)
            if resource is None:
                return False, errormsg

        # Import resource_switches
        for k in loaded_data["resource_switches"]:
            data = loaded_data["resource_switches"][k]
            resource_switch, errormsg = self.add_resource_switch(ID=k, **data)
            if resource_switch is None:
                return False, errormsg

        # Import processes
        for k in loaded_data["processes"]:
            data = loaded_data["processes"][k]
            process, errormsg = self.add_process(data.pop("typename"), ID=k, **data)
            if process is None:
                return False, errormsg

        # Import areas
        for k in loaded_data["areas"]:
            data = loaded_data["areas"][k]
            area, errormsg = self.add_area(data.pop("typename"), ID=k, **data)
            if area is None:
                return False, errormsg

        # Import groups
        for k in loaded_data["groups"]:
            data = loaded_data["groups"][k]
            group, errormsg = self.add_group(data.pop("typename"), ID=k, **data)
            if group is None:
                return False, errormsg

        # Import activity plans
        for k in loaded_data["plans"]:
            data = loaded_data["plans"][k]
            plan, errormsg = self.add_plan(data.pop("typename"), ID=k, **data)
            if plan is None:
                return False, errormsg

        return True, None

    def save(self, filename):
        """Save current configuration as a json template. The configuration
        must be valid.
        *filename* must be suitable for eflips.settings.save_json.
        Return (True, None) if successful, otherwise (False, errormsg).
        """
        success, errormsg = self.isvalid
        if not success:
            return success, errormsg

        configuration = dict()
        configuration["templatename_display"] = self.templatename
        configuration["general"] = {
            "depotID": self.depot.ID,
            "dispatch_strategy_name": self.depot.depot_control.dispatch_strategy_name,
        }

        # Export resources
        configuration["resources"] = {
            k: self.export_resource(v) for k, v in self.depot.resources.items()
        }
        # Export resource_switches
        configuration["resource_switches"] = {
            k: self.export_resource_switch(v)
            for k, v in self.depot.resource_switches.items()
        }
        # Export processes
        configuration["processes"] = {
            k: self.export_process(v) for k, v in self.depot.processes.items()
        }
        # Export areas
        configuration["areas"] = {
            k: self.export_area(v) for k, v in self.depot.areas.items()
        }
        # Export groups
        configuration["groups"] = {
            k: self.export_group(v) for k, v in self.depot.groups.items()
        }
        # Export specific plans
        configuration["plans"] = {
            k: self.export_plan(v) for k, v in self.depot.specific_plans.items()
        }
        # Export default plan
        configuration["plans"][self.depot.default_plan.ID] = self.export_plan(
            self.depot.default_plan
        )

        save_json(configuration, filename)
        return True, None

    def complete(self):
        """Actions that must take place before the simulation starts, but may
        not be possible upon initial creation of the depot since the
        possibility to create an empty depot is required.
        Return (True, None) if successful, otherwise (False, errormsg).
        May be called only once before simulation start.
        """
        if self.completed:
            errormsg = "Method DepotConfigurator.complete can be called " "only once."
            return False, errormsg

        # Final check for validity
        success, errormsg = self.isvalid
        if not success:
            return success, errormsg

        self.depot.depot_control._complete()

        if eflips.globalConstants["general"]["LOG_ATTRIBUTES"]:
            self.depot.init_store.logger = DataLogger(
                self.env, self.depot.init_store, "BACKGROUNDSTORE"
            )

        # Create a list of direct areas that are in a parking area group
        # (performance tweak)
        for group in self.depot.parking_area_groups:
            self.depot.direct_departure_areas.extend(group.direct_areas)

        # Run resource_switches
        for sw in self.depot.resource_switches.values():
            if sw.breaks:
                self.env.process(sw.run_break_cycle())

        self.depot.capacity = sum(area.capacity for area in self.depot.list_areas)
        self.depot.parking_capacity = sum(
            pag.capacity for pag in self.depot.parking_area_groups
        )
        self.depot.parking_capacity_direct = sum(
            pag.capacity_direct for pag in self.depot.parking_area_groups
        )

        self.depot.any_process_cancellable_for_dispatch = any(
            procdata["kwargs"]["cancellable_for_dispatch"]
            for procdata in self.depot.processes.values()
        )

        self.completed = True
        return True, None
