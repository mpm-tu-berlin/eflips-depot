"""Determination of fitness and feasibility of individuals specific for an
optimization scenario with objectives c and urfd.
"""
import eflips
from eflips.depot.layout_opt import opt_tools
from eflips.depot.layout_opt.settings import OPT_CONSTANTS as OC


PENALTY_DELTA = 0


# Load eflips.globalConstants once for all simulation runs
filename_eflips_settings = OC["scenario"]["filename_eflips_settings"]
eflips.load_settings(filename_eflips_settings)
eflips.check_gc_validity()
eflips.complete_gc()
GC = eflips.globalConstants
SIM_TIME = GC["general"]["SIMULATION_TIME"]

# Load data from excel to init a Timetable once for all simulation runs
filename_timetable = OC["scenario"]["filename_timetable"]
timetabledata = eflips.depot.standalone.timetabledata_from_excel(filename_timetable)


def estimate_max_total_delay(ttd):
    """Return an estimate of the maximum total delay that is possible until
    sim time end. SIM_TIME should be a multiple of 86400 (defining full days).

    ttd: [eflips.depot.standalone.ExcelSheetData]
    """
    n_vehicles = 0
    for depot_ID in GC["depot"]["vehicle_count"]:
        for vt_ID in GC["depot"]["vehicle_count"][depot_ID]:
            n_vehicles += GC["depot"]["vehicle_count"][depot_ID][vt_ID]

    index_std = ttd.map_headers()["std [s]"]
    count_skipped = 0
    count = 0
    estimate = 0

    for i in range(SIM_TIME // 86400):
        for tripdata in ttd.data[1:]:  # Skip header row
            if count_skipped > n_vehicles:
                std = tripdata[index_std] * (i + 1)
                estimate += SIM_TIME - std
                count += 1
            else:
                # Skip the first n_vehicles trips because they will be served
                # by vehicles from the init store and therefore cannot be
                # delayed
                count_skipped += 1

    # print('estimate_max_total_delay: %d, max std: %d, count: %d, count_skipped: %d'
    #       % (estimate, std, count, count_skipped))
    return estimate


def estimate_max_total_congestion(ttd):
    """Return an estimate of the maximum total congestion time that is possible
    until sim time end. SIM_TIME should be a multiple of 86400 (defining full
    days).

    ttd: [eflips.depot.standalone.ExcelSheetData]
    """
    index_sta = ttd.map_headers()["sta [s]"]
    count = 0
    estimate = 0

    for i in range(SIM_TIME // 86400):
        for tripdata in ttd.data[1:]:  # Skip header row
            sta = tripdata[index_sta] * (i + 1)
            if sta < SIM_TIME:  # Skip trips with sta after sim time end
                estimate += SIM_TIME - sta
                count += 1

    # print('estimate_max_total_congestion: %d, max sta: %d, count: %d'
    #       % (estimate, sta, count))
    return estimate


# Precompute data for normalizing evaluation results
for key in ["max_capacity_estimate", "max_delay_estimate", "max_congestion_estimate"]:
    if key in OC["scenario"]:
        raise ValueError(
            "Attempted to set key '%s' in OC['scenario'] but it's already used." % key
        )
OC["scenario"]["max_capacity_estimate"] = opt_tools.init.CAPACITY_MAX
OC["scenario"]["max_delay_estimate"] = estimate_max_total_delay(timetabledata)
OC["scenario"]["max_congestion_estimate"] = estimate_max_total_congestion(timetabledata)


def evaluate(ind):
    """Return the fitness tuple capacity, urfd for an individual.
    Caution: Returns other results in addition to fitness to not lose them with
    multiprocessing.
    """
    c = evaluate_capacity(ind)
    urfd = evaluate_urfd(ind)

    evaluate_delay(ind)
    evaluate_congestion(ind)

    if (
        not OC["scenario"]["simulate_below_capacity_min"]
        and ind.capacity < OC["scenario"]["CAPACITY_MIN"]
    ):
        # If the minimum capacity is violated, then skip simulation and assign
        # standard values to delay and congestion (see designated evaluate-
        # functions), to speed up evaluation. CAPACITY_MIN needs to be adapted
        # to scenarios with new depot limits!
        ind.results["simtime"] = 0

    return c, urfd, ind.results


def evaluate_single(ind):
    """Return the capacity fitness as tuple (for single objective)."""
    c = evaluate_capacity(ind)
    return (c,)


def evaluate_capacity(ind):
    """Return the objective function value for capacity."""
    ind.results["capacity"] = ind.capacity
    ind.results["feasible_capacity"] = feasible_capacity(ind)
    if ind.results["feasible_capacity"]:
        c = ind.capacity
    else:
        c = PENALTY_DELTA - distance_capacity(ind)
    return c


def evaluate_urfd(ind):
    """Objective function value for number of unblocked rfd vehicles."""
    if (
        not OC["scenario"]["simulate_below_capacity_min"]
        and ind.capacity < OC["scenario"]["CAPACITY_MIN"]
    ):
        urfd = 0
    else:
        evaluate_simulation(ind)
        urfd = ind.results["rfd_unblocked"]
    return urfd


def evaluate_delay(ind):
    """Evaluate the delay (result in hours!)."""
    if (
        not OC["scenario"]["simulate_below_capacity_min"]
        and ind.capacity < OC["scenario"]["CAPACITY_MIN"]
    ):
        ind.results["delay"] = OC["scenario"]["max_delay_estimate"] / 3600
    else:
        evaluate_simulation(ind)
    ind.results["feasible_delay"] = feasible_delay(ind)


def evaluate_congestion(ind):
    """Evaluate the congestion (result in hours!)."""
    if (
        not OC["scenario"]["simulate_below_capacity_min"]
        and ind.capacity < OC["scenario"]["CAPACITY_MIN"]
    ):
        ind.results["congestion"] = OC["scenario"]["max_congestion_estimate"] / 3600
    else:
        evaluate_simulation(ind)
    ind.results["feasible_congestion"] = feasible_congestion(ind)


def feasible_capacity(ind):
    """Do the packing test and return its result."""
    if not ind.areas:
        # an empty depot is considered infeasible
        return False

    if ind.visu.feasible is None:
        ind.visu.pack()

    return ind.visu.feasible


def feasible_delay(ind):
    """Return True if *ind* is feasible in terms of delay, i.e. there was none."""
    return ind.results["delay"] == 0


def feasible_congestion(ind):
    """Return True if *ind* is feasible in terms of congestion, i.e. there was
    none.
    """
    return ind.results["congestion"] == 0


def distance_capacity(ind):
    """Return an estimate of how far away *ind* is from the feasible region in
    terms of capacity.
    """
    # capacity always 0 if infeasible
    distance = 0

    # If the capacity is lower than the estimated max, but still not feasbile,
    # then return 0
    # distance = max(ind.capacity - OC['scenario']['max_capacity_estimate'], 0)
    return distance


def evaluate_simulation(ind):
    """Simulate and evaluate the results.

    Delay and congestion values are converted to hours.
    """
    if not ind.results["simulated"]:
        simulation_host = opt_tools.simulate(ind, timetabledata)
        ev = simulation_host.depots[0].evaluation

        # Total delay sum
        ev.total_delay()
        ind.results["delay"] = ev.results["total_delay"] / 3600

        # Total congestion sum
        ev.total_parking_congestion()
        ind.results["congestion"] = ev.results["total_parking_congestion"] / 3600

        # Unblocked rfd vehicles
        ev.calc_count_rfd_unblocked_total()
        ind.results["rfd_unblocked"] = ev.results["count_rfd_unblocked_total"]["mean"]
    else:
        # No need to simulate again
        pass


def feasible(ind):
    """Return True if *ind* is feasible. *ind* must be evaluated first."""
    return feasible_fr(ind.results)


def feasible_fr(results):
    """Return True if *ind* is feasible. *results* is an attribute of *ind*.
    *ind* must be evaluated first."""
    result = all(
        [
            results["feasible_capacity"],
            results["feasible_delay"],
            results["feasible_congestion"],
        ]
    )
    return result


def feasible_fr_vec(results):
    """Return a feasibility vector based on the evaluation results of an
    individual.
    """
    feasibility = [
        results["feasible_capacity"],
        results["feasible_delay"],
        results["feasible_congestion"],
    ]
    return tuple(feasibility)
