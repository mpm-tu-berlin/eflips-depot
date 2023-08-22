# -*- coding: utf-8 -*-
"""
Example plot calls and evaluation after(!) simulation.
For plot details see evaluation.py.
"""

# Plots and results for evaluation of depot layout

ev.vehicle_periods(
    periods={
        "depot general": "darkgray",
        "park": "lightgray",
        "serve_supply_clean_daily": "steelblue",
        "serve_clean_ext": "darkblue",
        "charge_dc": "forestgreen",
        "charge_oc": "forestgreen",
        "precondition": "black",
    },
    save=True,
    show=True,
    formats=(
        "pdf",
        "png",
    ),
    show_total_power=True,
    show_annotates=True,
)

ev.nvehicles_used(save=True, show=True)
ev.departure_delay_vt(save=True, show=True)

ev.sl_all(save=True, show=True)

ev.nvehicles_initstore_end(save=True, show=True)
ev.nvehicles_area("S_1", save=True, show=True)

ev.usage_history("workers_service", save=True, show=True)
ev.usage_history("shunting_service", save=True, show=True)

ev.area_history("SB_DC 1", save=True, show=True)
ev.battery_level("SB_DC 1", save=True, show=True)
ev.vehicle_power("SB_DC 1", save=True, show=True)

ev.idle_time_dist(charge_IDs=("charge_dc", "charge_oc"), bins=60, save=True, show=True)

ev.arrival_soc(
    typs={"SB_DC": "#2ca02c", "AB_OC": "#CD3333"},
    formats=(
        "pdf",
        "png",
    ),
    save=True,
    show=True,
    language="de",
)
ev.departure_soc(
    typs={"SB_DC": "#2ca02c", "AB_OC": "#CD3333"},
    formats=(
        "pdf",
        "png",
    ),
    save=True,
    show=True,
    language="de",
)

ev.congestion(save=True, show=True)
ev.occupancy_rate()

ev.lead_time_match_scatter(save=True, show=True)
ev.lead_time_match_dist(save=True, show=True)

for resource in ev.depot.resources:
    if resource[0:2] == "ci":
        ci_no_str = resource[3:]
        ev.ci_power(cis=[int(ci_no_str), int(ci_no_str)])

for ci_no in range(1, 20):
    ev.ci_power(cis=[ci_no, ci_no])


# Plots and results for evaluation of depot layout with smart charging
ev.vehicle_periods(
    periods={
        "depot general": "darkgray",
        "park": "lightgray",
        "serve_supply": "skyblue",
        "serve_clean_daily": "steelblue",
        "serve_clean_ext": "darkblue",
        "precondition": "black",
    },
    basefilename="vehicle_periods_sc",
    save=True,
    show=False,
    formats=(
        "pdf",
        "png",
    ),
    show_total_power=True,
    smart_charging=smart_charging,
    smart_charging_color="forestgreen",
)

smart_charging.results()
smart_charging.plot_results()
