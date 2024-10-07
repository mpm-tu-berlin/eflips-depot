import time
from toolkit import *

### Setup ###
SCENARIO_ID = 1
CUT_OFF_PERCENTILE_STANDBY_TIME = 40
# Only stations in that percentile will be electrified. Rotations will be split with percentiles > 32
db_params = {
    'dbname': 'eflips',
    'user': 'linus',
    'password': '1234',
    'host': 'localhost',
    'port': '5432',
}
path_to_pg_dump = 'db_single_step.sql'
DATABASE_URL = f"postgresql://{db_params['user']}:{db_params['password']}@{db_params['host']}/{db_params['dbname']}"
station_standby = []
# split_rotation_ids = []
# split_rotations = []
station_blacklist = []  # stations that cannot be electrified (IDs)
station_whitelist = []  # stations that have to be electrified (IDs)
#############

def run_optimisation():
    new_oc_stations = tk.run_single_step()
    if len(new_oc_stations) > 1: print("\nError: Multiple Stations electrified, please implement case")

    def split():
        tk.undo_electrification(new_oc_stations[0])
        tk.run_consumption_sim()
        to_split = get_all_negative_rotations_at_station(tk, new_oc_stations[0])

        for rotation in to_split:
            # split_rotations.append(tk.get_rotation_info(rotation[0]))
            # split_rotation_ids.append(rotation[0])
            tk.run_rotation_split(rotation[0])

        run_optimisation()

    if new_oc_stations:
        eligible = False
        stations = tk.get_stations()

        for station in tk.eligible_stations:
            if station['station_id'] == new_oc_stations[0]:
                eligible = True
                print(f'\nElectrification of {stations[new_oc_stations[0]]} accepted.')

        if eligible: run_optimisation()

        if new_oc_stations[0] in station_blacklist:
            print(f'\nElectrification of blacklisted station {stations[new_oc_stations[0]]} denied.')
            split()
        elif not eligible:
            print(f'\nElectrification of {stations[new_oc_stations[0]]} denied.')
            split()
    else:
        tk.run_depot_sim()
        print(f"\nOptimization complete. Runtime: {round(time.time() - optimisation_start)} s."
              f"\n{len(tk.get_stations(electrified=True))} stations electrified."
              f"\n{tk.get_vehicle_count()} vehicles in scenario."
              f"\n{tk.get_rotation_count() - rotation_count} rotations split.")
        result = {'percentile': CUT_OFF_PERCENTILE_STANDBY_TIME,
                  'station_count': len(tk.get_stations(electrified=True)),
                  'stations': tk.get_stations(electrified=True),
                  'vehicles': tk.get_vehicle_count(),
                  'rotations': tk.get_rotation_count()}
        tk.json_dump(result)
        print("Optimisation result saved to results.json.")


if __name__ == '__main__':
    optimisation_start = time.time()
    print('\nStarting optimization...')
    tk = Toolkit(DATABASE_URL, SCENARIO_ID, CUT_OFF_PERCENTILE_STANDBY_TIME, True)
    # tk.run_depot_sim()
    tk.run_consumption_sim()
    tk = Toolkit(DATABASE_URL, SCENARIO_ID, CUT_OFF_PERCENTILE_STANDBY_TIME)
    rotation_count = tk.get_rotation_count()
    print(f'\n{len(tk.eligible_stations)} stations eligible for electrification'
          f' ({CUT_OFF_PERCENTILE_STANDBY_TIME} percentile):')

    run_optimisation()
