"""Methods to check and modify eflips settings.

Separate from eflips.settings.py as preparation for making settings
modifications possible through a GUI, similar as with
eflips.depot.configuration.py.
"""
from eflips.depot.simple_vehicle import VehicleType, VehicleTypeGroup
from eflips.settings import globalConstants as gc


def check_gc_validity():
    """Checks of globalConstants that raise an error if not passed."""

    # substitutable_types
    st = gc["depot"]["substitutable_types"]  # create shortcut
    if not isinstance(st, list) or not all(isinstance(group, list) for group in st):
        raise ValueError("Invalid globalConstants substitutable_types.")


def complete_gc():
    """Actions that must take place before simulation start, but may not
    be possible during the configuration phase.

    """
    # Create vehicle types and save in list and dict
    type_data = gc["depot"]["vehicle_types"]
    gc["depot"]["vehicle_types_obj"] = []
    vto = gc["depot"]["vehicle_types_obj"]
    gc["depot"]["vehicle_types_obj_dict"] = {}
    vto_dict = gc["depot"]["vehicle_types_obj_dict"]
    for ID in type_data:
        vt = VehicleType(ID, **type_data[ID])
        vto.append(vt)
        vto_dict[ID] = vt

    # Check vehicle type groups
    st = gc["depot"]["substitutable_types"]  # shortcut
    control = []
    for group_data in st:
        control.extend(group_data)
    if any(control.count(ID) > 1 for ID in control):
        raise ValueError(
            "Vehicle types cannot have multiple appearances in "
            "globalConstants['depot']['substitutable_types']."
        )

    # Create vehicle type groups
    gc["depot"]["vehicle_type_groups"] = []
    for group_data in st:
        types = [next(vt for vt in vto if vt.ID == ID) for ID in group_data]
        group = VehicleTypeGroup(types)
        gc["depot"]["vehicle_type_groups"].append(group)
        for vehicle_type in types:
            vehicle_type.group = group

    # Create a list of str representations of vehicle types and groups
    categories = []
    for vt in gc["depot"]["vehicle_types_obj"]:
        if vt.group is None:
            categories.append(vt.ID)
        else:
            categories.append(", ".join(vt.ID for vt in vt.group.types))
    categories = list(set(categories))
    categories.sort()
    gc["depot"]["vehicle_type_categories"] = categories

    # Precompute maximum battery capacity and save it in globalConstants
    gc["depot"]["max_battery_capacity"] = max(vt.battery_capacity for vt in vto)
