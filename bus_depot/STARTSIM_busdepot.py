# -*- coding: utf-8 -*-
"""
Created on Tue Oct 13 10:55:00 2020

@author: E.Lauth

Execute this script to run the depot simulation.

"""

import eflips

# Switch for simulation with GUI
simulate_with_gui = False

# Switch for simulation with smart charging
simulate_with_smart_charging = False

######## SETTINGS, SCHEDULE AND TEMPLATE FOR DEPOT LAYOUT #####
# DEFAULT = EXAMPLE DISSERTATION E.LAUTH, https://depositonce.tu-berlin.de/items/f47662f7-c9ae-4fbf-9e9c-bcd307b73aa7)

filename_eflips_settings = '..\\bus_depot\\eflips_settings\\kls_diss_settings_210219'
filename_schedule = '..\\bus_depot\\schedules\\schedule_kls_diss_scenario1_SB_DC_AB_OC_210203'
filename_template = '..\\bus_depot\\templates\\diss_kls_6xS, 94x150kW_SB, 147x75kW_AB, shunting+precond+chargeequationsteps'

if __name__ == "__main__":

    if simulate_with_gui:
        # GUI creates and runs a SimulationHost
        from eflips.depot.gui.depot_view import main_view
        if main_view is None:
            eflips.depot.gui.depot_view.start(
                filename_eflips_settings=filename_eflips_settings,
                filename_timetable=filename_schedule)
            print('Closing main thread...')

    else:
        simulation_host = eflips.depot.SimulationHost(
            [
                eflips.depot.Depotinput(
                    filename_template=filename_template,
                    show_gui=False)
            ],
            run_progressbar=True,
            print_timestamps=True,
            tictocname=''
        )
        simulation_host.standard_setup(filename_eflips_settings,
                                       filename_schedule)
        simulation_host.run()

        depot = simulation_host.depots[0]
        ev = simulation_host.depot_hosts[0].evaluation

        if simulation_host.gc['depot']['log_cm_data']:
            # Export data for charging management if data was logged
            ev.cm_report.export_logs(ev.cm_report.defaultname)

        if simulate_with_smart_charging:

            start_date = simulation_host.gc['depot']['smart_charging']['start_date']
            power_limit_grid = simulation_host.gc['depot']['smart_charging']['power_limit_grid']
            accuracy = simulation_host.gc['depot']['smart_charging']['accuracy']
            price_data_path = simulation_host.gc['depot']['smart_charging']['price_data_path']

            smart_charging = eflips.depot.SmartCharging(ev, start_date, price_data_path, power_limit_grid)
            if smart_charging.smart_charging_algorithm():
                print("Smart charging successful.")
            else:
                print("Smart charging NOT successful.")
            smart_charging.validation()

        validator = eflips.depot.Validator(ev)
        validator.all_periods({
            'depot general': 'depot general',
            'park': 'park',
            'serve': 'serve',
            'charge': ['charge_dc', 'charge_oc']})
        validator.single_matches()
        print('Simulation results valid:', validator.valid)
