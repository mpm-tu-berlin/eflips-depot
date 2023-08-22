"""Utilities for converting config data, config names, templates and visu."""
import json
from eflips.depot.layout_opt import packing


def save_json(obj, filename):
    """Write python object *obj* to a json file. Overwrite file if it already
    exists (without comfirmation prompt).
    filename: [str] excluding file extension
    """
    filename = filename + ".json"
    with open(filename, "w") as file:
        json.dump(obj, file, indent=4)


def name_from_config(area_config):
    """Create a name [str] from area_config [dict]."""
    name = ""

    for typename_short, data in area_config.items():
        for capacity, n in data.items():
            name += "_" + str(n) + "x" + str(capacity) + typename_short

    if name:
        name = name[1:]

    return name


def config_from_name(name):
    """Create an area config [dict] from name [str]."""

    def decode(a):
        n = int(a[: a.index("x")])
        c = int(a[a.index("x") + 1 : -1])
        t = a[-1]
        return n, c, t

    ac = {"d": {}, "l": {}}
    for ai in name.split("_"):
        ni, ci, ti = decode(ai)
        ac[ti][ci] = ni

    return ac


def config_from_name_3a(name):
    """Create an area config [dict] from name [str] where types are DDR, DSR, DSR_90
    L.
    """

    def decode(a):
        i_lastdigit = a.rfind(next(si for si in a[::-1] if si.isdigit()))
        n = int(a[: a.index("x")])
        c = int(a[a.index("x") + 1 : i_lastdigit + 1])
        t = a[i_lastdigit + 1 :]
        return n, c, t

    ac = {"DDR": {}, "DSR": {}, "DSR_90": {}, "L": {}}
    for ai in name.split("_"):
        ni, ci, ti = decode(ai)
        ac[ti][ci] = ni

    return ac


def template_from_config(area_config):
    parking_capacity = 0
    for data in area_config.values():
        for capacity, n in data.items():
            for ni in range(n):
                parking_capacity += capacity

    ID_counters = {"ci": 0, "pa": 0}

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

    template["general"]["depotID"] = "KLS"
    template["general"]["dispatch_strategy_name"] = "SMART"

    template["resources"]["workers_service"] = {
        "typename": "DepotResource",
        "capacity": capacity_service,
    }

    template["resource_switches"]["service_switch"] = {
        "resource": "workers_service",
        "breaks": [[25200, 61200]],
        "preempt": True,
        "strength": "full",
    }

    template["processes"]["charge"] = {
        "typename": "Charge",
        "ismandatory": False,
        "vehicle_filter": {"filter_names": ["soc_lower_than"], "soc": 0.9},
        "cancellable_for_dispatch": False,
        "soc_target": 0.9,
        "efficiency": 0.95,
    }

    template["processes"]["serve"] = {
        "typename": "Serve",
        "dur": 600,
        "ismandatory": False,
        "vehicle_filter": {"filter_names": ["in_period"], "period": [57600, 20700]},
        "required_resources": ["workers_service"],
        "cancellable_for_dispatch": False,
    }

    template["processes"]["standby_arr"] = {
        "typename": "Standby",
        "dur": 300,
        "ismandatory": True,
        "vehicle_filter": None,
        "required_resources": [],
        "cancellable_for_dispatch": False,
    }

    template["processes"]["standby_dep"] = {
        "typename": "Standby",
        "dur": 900,
        "ismandatory": True,
        "vehicle_filter": None,
        "required_resources": [],
        "cancellable_for_dispatch": False,
    }

    # Areas
    template["areas"]["Stauflaeche"] = {
        "typename": "DirectArea",
        "capacity": parking_capacity,
        "available_processes": ["standby_arr"],
        "issink": False,
        "entry_filter": None,
    }

    template["areas"]["Serviceflaeche"] = {
        "typename": "DirectArea",
        "capacity": capacity_service,
        "available_processes": ["serve"],
        "issink": False,
        "entry_filter": None,
    }

    # Parking areas
    def add_parking_area_direct(capacity):
        ID_counters["pa"] += 1
        ID = "area_" + str(ID_counters["pa"])
        cis = []
        for ci in range(capacity):
            cis.append("ci_" + str(ID_counters["ci"]))
            ID_counters["ci"] += 1

        template["areas"][ID] = {
            "typename": "DirectArea",
            "amount": 1,
            "capacity": capacity,
            "charging_interfaces": cis,
            "available_processes": ["charge", "standby_dep"],
            "issink": True,
            "entry_filter": None,
        }

    def add_parking_area_line(capacity):
        ID_counters["pa"] += 1
        ID = "area_" + str(ID_counters["pa"])
        cis = []
        for ci in range(capacity):
            cis.append("ci_" + str(ID_counters["ci"]))
            ID_counters["ci"] += 1

        template["areas"][ID] = {
            "typename": "LineArea",
            "amount": 1,
            "capacity": capacity,
            "charging_interfaces": cis,
            "available_processes": ["charge", "standby_dep"],
            "issink": True,
            "entry_filter": None,
            "side_put_default": "front",
            "side_get_default": "back",
        }

    for typename_short, data in area_config.items():
        for capacity, n in data.items():
            for ni in range(n):
                if typename_short == "d":
                    add_parking_area_direct(capacity)
                else:
                    add_parking_area_line(capacity)

    # Charging interfaces
    for i in range(ID_counters["ci"]):
        ID = "ci_" + str(i)
        template["resources"][ID] = {
            "typename": "DepotChargingInterface",
            "max_power": power,
        }

    # Charging switches
    for i in range(ID_counters["ci"]):
        ID = "cs_" + str(i)
        template["resource_switches"][ID] = {
            "resource": "ci_" + str(i),
            "breaks": [[64800, 72000]],
            "preempt": True,
            "strength": "full",
        }

    # Groups
    template["groups"]["parking area group"] = {
        "typename": "ParkingAreaGroup",
        "stores": [key for key, value in template["areas"].items() if value["issink"]],
        "parking_strategy_name": "SMART2",
    }

    # Plan
    template["plans"]["default"] = {
        "typename": "DefaultActivityPlan",
        "locations": ["Stauflaeche", "Serviceflaeche", "parking area group"],
    }

    name = name_from_config(area_config)
    template["templatename_display"] = name

    save_json(template, "templates\\" + name)
    print("Saved ", name)


def visu_from_config(area_config, a, b):
    """Only works for config with visu keys listed below."""
    visus = {
        "DDR": packing.VisuDataDirectDoubleRow,
        "DSR": packing.VisuDataDirectSingleRow,
        "DSR_90": packing.VisuDataDirectSingleRow_90,
        "L": packing.VisuDataLine,
    }

    bin = packing.BinWithDistances(a, b)
    for typename, data in area_config.items():
        for c, n in data.items():
            for i in range(n):
                bin.items.append(visus[typename](capacity=c))

    return bin


def ind_from_config(area_config, creator):
    """Create an individual [DepotPrototype] from area_config [dict].
    Requires a the deap creator to be setup. Depot a and b are taken from
    depot_layout_opt.settings.OPT_CONSTANTS (needs to be loaded before calling
    this function)."""
    from eflips.depot.layout_opt.opt_tools.init import (
        DSRPrototype,
        DSR_90Prototype,
        DDRPrototype,
        LinePrototype,
    )

    protos = {
        "DDR": DDRPrototype,
        "DSR": DSRPrototype,
        "DSR_90": DSR_90Prototype,
        "L": LinePrototype,
    }
    ind = creator.Individual()
    for typename, data in area_config.items():
        for c, n in data.items():
            for i in range(n):
                ind.areas.append(protos[typename](capacity=c))
    return ind


def template_from_name(name):
    template_from_config(config_from_name(name))


if __name__ == "__main__":
    power = 150
    capacity_service = 0
    # area_config = {
    #     'd': {
    #         148: 1
    #     },
    #     'l': {
    #
    #     }
    # }

    # template_from_config(area_config, power=150, capacity_service=3)

    # template_from_name('2x10d_2x10l_1x4l')

    # area_config = {
    #     'DDR': {
    #
    #     },
    #     'DSR': {
    #         23: 1
    #     },
    #     'L': {
    #         8: 17
    #     }
    # }

    # import matplotlib.pyplot as plt
    # plt.rcParams.update({
    #         "font.family": "serif",
    #         "font.sans-serif": "Liberation Sans, Arial, Helvetica, DejaVu Sans",
    #         "font.serif": "Times new Roman",
    #         "axes.labelsize": 12,
    #         # "axes.titleweight": "normal",
    #         # "axes.titlesize": 12,
    #         # "legend.fontsize": 12,
    #         # "legend.frameon": True,
    #         # "figure.autolayout": True,
    #         # "figure.dpi": 300
    #     })
    # bin = visu_from_config(area_config, 88, 140)

    area_config = {"DDR": {65: 0}, "DSR": {33: 0}, "DSR_90": {33: 0}, "L": {4: 1}}
    # bin = visu_from_config(area_config, 47, 202)
    # bin.draw()

    from eflips.depot.layout_opt import optimize_c_urfd
    from eflips.depot.layout_opt.opt_tools import fitness_c_urfd

    ind = ind_from_config(area_config, optimize_c_urfd.creator)
    results = fitness_c_urfd.evaluate(ind)
    ind.fitness.values = results[:-1]
    ind.results = results[-1]
    print(fitness_c_urfd.feasible(ind))
