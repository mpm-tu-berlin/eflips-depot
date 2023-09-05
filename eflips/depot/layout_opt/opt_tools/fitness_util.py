"""Basic utilities for the determination of fitness and feasibility of
individuals.
"""
from operator import attrgetter
from eflips.depot.layout_opt import opt_tools


def memorize(ind, memory):
    """Register and individual in memory. Must be unknown."""
    assert ind.ID not in memory, ind.ID
    memory[ind.ID] = {"results": ind.results, "fitness": ind.fitness.values, "count": 1}


def lookup(ind, memory):
    """Assign results from memory to an individual if it's known already.
    An unkown individual is left unchanged.
    """
    if ind.ID in memory:
        ind.results = memory[ind.ID]["results"]
        ind.fitness.values = memory[ind.ID]["fitness"]
        memory[ind.ID]["count"] += 1


def simulate(ind, timetabledata, print_timestamps=False):
    """Set up a simulation based on *ind* and run.
    Return the SimulationHost if simulated, else None.
    """
    import eflips

    simulation_host = eflips.depot.SimulationHost(
        [eflips.depot.Depotinput(filename_template=None, show_gui=False)],
        run_progressbar=False,
        print_timestamps=print_timestamps,
        tictocname="",
    )
    simulation_host.init_timetable(timetabledata)
    prototype_to_template(ind, simulation_host)
    simulation_host.complete()
    simulation_host.run()

    ind.results["simulated"] = True
    ind.results["simtime"] = simulation_host.tictoc.tlist[-1]

    return simulation_host


def prototype_to_template(dp, simulation_host):
    """Use eflips.depot.DepotConfigurator to turn *dp* into a proper template,
    ready to use for simulation.
    All data is added to *simulation_host*, return None.

    dp: [DepotPrototype]
    """
    import eflips

    GC = eflips.globalConstants
    nvehicles = sum(
        count
        for counts in GC["depot"]["vehicle_count"].values()
        for count in counts.values()
    )
    config = simulation_host.depot_hosts[0].configurator

    config.depot.ID = "KLS"
    config.depot.depot_control.dispatch_strategy_name = "SMART"

    # Resources
    resource, errormsg = config.add_resource(
        "DepotResource", ID="workers_service", capacity=3
    )
    if resource is None:
        raise RuntimeError(errormsg)

    for ri in range(dp.capacity):
        resID = "ci_" + str(ri + 1)
        resource, errormsg = config.add_resource(
            "DepotChargingInterface", ID=resID, max_power=150  # 20
        )
        if resource is None:
            raise RuntimeError(errormsg)

    # Resource switches
    resource_switch, errormsg = config.add_resource_switch(
        ID="service_switch", resource="workers_service", breaks=[(25200, 61200)]
    )
    if resource_switch is None:
        raise RuntimeError(errormsg)

    # for ri in range(dp.capacity):
    #     resID = 'ci_' + str(ri + 1)
    #     resource_switch, errormsg = config.add_resource_switch(
    #         ID='cs_' + resID,
    #         resource=resID,
    #         breaks=[(64800, 72000)]
    #     )
    #     if resource_switch is None:
    #         raise RuntimeError(errormsg)

    # Processes
    process, errormsg = config.add_process(
        "Charge",
        ID="charge",
        ismandatory=False,
        vehicle_filter={"filter_names": ["soc_lower_than"], "soc": 0.90},
        cancellable_for_dispatch=False,
        soc_target=0.90,
        efficiency=0.95,
    )
    if process is None:
        raise RuntimeError(errormsg)

    process, errormsg = config.add_process(
        "Serve",
        ID="serve",
        dur=600,
        ismandatory=False,
        vehicle_filter={"filter_names": ["in_period"], "period": [57600, 20700]},
        required_resources=["workers_service"],
        cancellable_for_dispatch=False,
    )
    if process is None:
        raise RuntimeError(errormsg)

    process, errormsg = config.add_process(
        "Standby",
        ID="standby_arr",
        dur=300,
        ismandatory=True,
        vehicle_filter=None,
        cancellable_for_dispatch=False,
    )
    if process is None:
        raise RuntimeError(errormsg)

    process, errormsg = config.add_process(
        "Standby",
        ID="standby_dep",
        dur=900,
        ismandatory=True,
        vehicle_filter=None,
        cancellable_for_dispatch=False,
    )
    if process is None:
        raise RuntimeError(errormsg)

    # Constant areas
    area, errormsg = config.add_area(
        "DirectArea",
        ID="Stauflaeche",
        capacity=nvehicles,
        available_processes=[],
        issink=False,
        entry_filter=None,
    )
    if area is None:
        raise RuntimeError(errormsg)

    area, errormsg = config.add_area(
        "DirectArea",
        ID="Serviceflaeche",
        capacity=3,
        available_processes=["serve"],
        issink=False,
        entry_filter=None,
    )
    if area is None:
        raise RuntimeError(errormsg)

    # Sort to match the visu
    area_prototypes = sorted(dp.areas, key=attrgetter("capacity"), reverse=True)
    area_prototypes.sort(key=attrgetter("typename"))

    # Areas from prototype
    used_ci = 0
    parking_area_IDs = []
    for ai, area_prototype in enumerate(area_prototypes):
        ID = "area_" + str(ai + 1)
        parking_area_IDs.append(ID)

        charging_interfaces = []
        for cii in range(area_prototype.capacity):
            charging_interfaces.append("ci_" + str(used_ci + 1))
            used_ci += 1

        if isinstance(area_prototype, opt_tools.LinePrototype):
            area, errormsg = config.add_area(
                "LineArea",
                ID=ID,
                capacity=area_prototype.capacity,
                charging_interfaces=charging_interfaces,
                available_processes=["charge", "standby_dep"],
                issink=True,
                entry_filter=None,
                side_put_default="front",
                side_get_default="back",
            )
        else:
            area, errormsg = config.add_area(
                "DirectArea",
                ID=ID,
                capacity=area_prototype.capacity,
                charging_interfaces=charging_interfaces,
                available_processes=["charge", "standby_dep"],
                issink=True,
                entry_filter=None,
            )
        if area is None:
            raise RuntimeError(errormsg)

    # Parking area group
    group, errormsg = config.add_group(
        "ParkingAreaGroup",
        ID="parking_area_group",
        stores=parking_area_IDs,
        parking_strategy_name="SMART2",
    )
    if group is None:
        raise RuntimeError(errormsg)

    # Activity plan
    plan, errormsg = config.add_plan(
        "DefaultActivityPlan",
        ID="default",
        locations=[
            "Stauflaeche",
            "parking_area_group",
        ],  # locations=['Stauflaeche', 'Serviceflaeche', 'parking_area_group']
    )
    if plan is None:
        raise RuntimeError(errormsg)

    success, errormsg = config.complete()
    if not success:
        raise RuntimeError(errormsg)

    simulation_host.complete()
