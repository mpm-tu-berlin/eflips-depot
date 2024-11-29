import numpy as np
import math
import matplotlib.pyplot as plt
from collections import defaultdict
import json

###GIVEN CODE:
#! /usr/bin/env python3
import argparse
import os
import warnings

from eflips.model import *
from eflips.model import ConsistencyWarning
from sqlalchemy import create_engine, distinct, false, or_
from sqlalchemy.orm import Session
###



class VehicleType_new:
    def __init__(self, vehicle_type):
        self.vehicle_type_id = vehicle_type.id
        self.battery_capacity = (vehicle_type.battery_capacity * 1000)/(3.2 * 200)
        self.battery_capacity_reserve = (vehicle_type.battery_capacity_reserve * 1000)/(3.2 * 200)
        self.full_capacity = self.battery_capacity + self.battery_capacity_reserve

#Klasse für Vehicles definieren, erbt von VehicleType
class Vehicle_new(VehicleType_new):
    def __init__(self, vehicle, soh = 1):
        #inherit properties of their VehicleType
        self.vehicle_type = all_vehicletypes[vehicle.vehicle_type_id]
        super().__init__(self.vehicle_type)

        #parameters:
        self.depot = None
        self.id = vehicle.id
        self.soh = 1
        self.yearly_soh = [self.soh]
        self.yearly_cap_fade = [0]
        self.cap_fade_abs = 0
        self.cap_fade = 0
        self.age = 0
        self.needs_replacement = False

        #store all events:
        self.driving_events = []
        self.charging_events = []

#helpful function to ensure that there are no event duplicates
def filter_uniques(objects):
    unique_objects = []
    logged_obj_ids = set()
    for obj in objects:
        if obj.id not in logged_obj_ids:
            unique_objects.append(obj)
            logged_obj_ids.add(obj.id)
    return unique_objects

def update_soh(veh, d_cf):
    veh.cap_fade_abs += d_cf
    veh.cap_fade += (d_cf/ (0.2* veh.full_capacity))   #full capacity as Q_nom
    veh.soh = 1 - veh.cap_fade
    veh.battery_capacity -= d_cf
    if veh.soh <= 0:
        veh.needs_replacement = True
        veh.soh = 0
        #todo: abklären: in Simulation Fahrzeuge rausfallen lassen aus loop? oder soh < 0 simulieren???


def calc_cap_fade(event, veh, T):
    #Parameter:
    #sidenote: could be defined outside of function as global constants for code efficiency, are left here for clarity
    k_s = [-4.092e-4, -2.167, 1.408e-5, 6.130]
    E_a = 78060                 #Aktivierungsenergie in mol/J
    R = 8.314                   #Gaskonstante in J/(molK)
    T_ref = 298.15              #Referenzwert Temperatur in K

    # Calculates for the driving event
    soc_avg = (event.soc_start + event.soc_end) / 2
    soc_dev = abs(event.soc_end - event.soc_start)/2
    Ah = abs(event.soc_start - event.soc_end) * veh.battery_capacity       #SoC auf available_cap bezogen, nicht ges_cap, da manchmal auch <0 (reserve capacity)
    d_cf = ((k_s[0] * soc_dev * math.exp(k_s[1] * soc_avg) + k_s[2] * math.exp(k_s[3] * soc_dev)) * math.exp(
        -(E_a / R) * (1 / T - 1 / T_ref)))*Ah

    relative_cf = d_cf / veh.battery_capacity
    update_soh(veh, d_cf)
    return(d_cf)

def calc_yearly_degen(vehicles):
    weeks_passed = 0
    while weeks_passed < 52:
        for vehicle in vehicles:
            # remove duplicate events before processing
            unique_charging_events = filter_uniques(vehicle.charging_events)
            # process each UNIQUE charging event
            for event in unique_charging_events:
                # todo: Temperaturabhängigkeit prüfen!!
                # todo: unrealisitische Annahme der identischen Wochen mit stark unterschiedlicher Belastung zwischen Fahrzeugen umgehen
                # zu todo: jede Woche driving events zykeln? wöchentlichen Durchschnitt nehmen?
                d_cf = calc_cap_fade(event, vehicle, T=300.15)  # in Kelvin)
        weeks_passed += 1

    for vehicle in vehicles:
        vehicle.age +=1
        vehicle.yearly_soh.append(vehicle.soh)
        vehicle.yearly_cap_fade.append(vehicle.cap_fade)

#not used, necessary??
def create_cap_fade_distr(vehicles, year, result_dict, results_array):
    #assert that all vehicles have the same age
    assert all(vehicle.age == year for vehicle in vehicles)
    vehicles_age = year

    # create arrays to store average ages in, for depots and for vehicle types, needed tor table later on
    avg_cap_fade_dict = defaultdict(list)
    depot_avg_sums = defaultdict(list)
    vehicle_type_avg_sums = defaultdict(list)

    # to unify scale of subplots
    global_min_cap_fade = min([vehicle.cap_fade for vehicle in vehicles])
    global_max_cap_fade = max([vehicle.cap_fade for vehicle in vehicles])

    # create plots to visualize capacity fade distribution, sorted by depot
    for depot_id, depot in all_depots.items():
        vehicles_in_depot = vehicles_of_depot[depot_id]

        fig, axes = plt.subplots(1, len(all_vehicletypes), figsize=(18, 6))
        fig.suptitle(f'Distribution of State of Health in Depot: {depot.name}', fontsize=25)
        # todo: Achsen vereinheitlichen für Vergleichbarkeit!

        ax_pos = 0  # help variable for position of subplot
        # Loop through each vehicle type to generate and save a histogram
        for v_type in all_vehicletypes.keys():
            # Filter vehicles of vehicle type out of vehicles of that depot
            cap_fade_of_vt = [vehicle.cap_fade for vehicle in vehicles_in_depot if vehicle.vehicle_type_id == v_type]
            n = len(cap_fade_of_vt)
            v_type_name = all_vehicletypes[v_type].name

            # Create histogram for capacity fade distribution:
            ax = axes[ax_pos]
            ax.hist(cap_fade_of_vt)
            ax.set_xlim(global_min_cap_fade, global_max_cap_fade)  # todo: also for y-axis??
            ax.set_xlabel(f'Capacity fade [/].')
            ax.tick_params(axis='x', rotation=45)  # rotates labels for readability
            ax.set_ylabel('Number of Vehicles')
            ax.set_title(f'Capacity fade distribution for {v_type_name}    - n = {n}')

            depot_index = depot_indizes[depot_id]
            vt_index = vehicle_type_indizes[v_type]

            # checks if there are no vehicles assigned to avoid error messages
            if n > 0:
                # show other relevant stats in graphs
                avg_cap_fade = np.mean(cap_fade_of_vt)

                additional_stats = (f'Average capacity fade: {avg_cap_fade:.3f}\n')
                # create box to display information
                ax.text(0.95, 0.95, additional_stats, transform=ax.transAxes, verticalalignment='top',
                        horizontalalignment='right', bbox=dict(facecolor='white', alpha=0.5))

                # save the weighted averages for table later on
                #todo: brauche ich noch für die durchschnittswerte, wie ersetzen?
                avg_cap_fade_dict[(depot_id, v_type)] = avg_cap_fade  # store in avg_ages_dict
                # store sum of capacity fades for weighted data of averages
                depot_avg_sums[depot_id].extend([avg_cap_fade] * n)
                vehicle_type_avg_sums[v_type].extend([avg_cap_fade] * n)

                # neu/besser so
                result_dict[(depot_id, v_type, year)] = avg_cap_fade
                results_array[depot_index, vt_index, year - 1] = avg_cap_fade
            else:
                avg_cap_fade = None
                results_array[depot_index, vt_index, year - 1] = np.nan

            ax_pos += 1  # move to next subplot position

        # Save the entire histogram for the depot with fitting filename:
        plt.tight_layout()
        plt.savefig(os.path.join(folder_path, f'capacityfade_distribution_for_{depot.name}.png'))
        plt.close(fig)

    depot_avg = {depot_id: np.sum(values)/len(vehicles_of_depot[depot_id]) for depot_id, values in depot_avg_sums.items()}
    vehicle_type_avg = {vt_id: np.sum(values)/len(vehicles_of_vehicletype[vt_id]) for vt_id, values in vehicle_type_avg_sums.items()}

    return avg_cap_fade_dict, depot_avg, vehicle_type_avg, vehicles_age


def create_cap_fade_array(vehicles, year, result_dict, results_array):
    #assert that all vehicles have the same age
    assert all(vehicle.age == year for vehicle in vehicles)
    #AFTER year has passed, the age gets updated to number of that year
    #vehicles_new_age = year            not needed??

    # create arrays to store average ages in, for depots and for vehicle types, needed tor table later on
    avg_cap_fade_dict = defaultdict(list)
    depot_avg_sums = defaultdict(list)
    vehicle_type_avg_sums = defaultdict(list)

    # create plots to visualize capacity fade distribution, sorted by depot
    for depot_id, depot in all_depots.items():
        vehicles_in_depot = vehicles_of_depot[depot_id]

        # Loop through each vehicle type to generate and save a histogram
        for v_type in all_vehicletypes.keys():
            # Filter vehicles of vehicle type out of vehicles of that depot
            cap_fade_of_vt = [vehicle.cap_fade for vehicle in vehicles_in_depot if vehicle.vehicle_type_id == v_type]

            n = len(cap_fade_of_vt)

            depot_index = depot_indizes[depot_id]
            vt_index = vehicle_type_indizes[v_type]

            # checks if there are no vehicles assigned to avoid error messages
            if n > 0:
                # show other relevant stats in graphs
                avg_cap_fade = np.mean(cap_fade_of_vt)

                # save the weighted averages for table later on
                #todo: brauche ich noch für die durchschnittswerte, wie ersetzen?
                avg_cap_fade_dict[(depot_id, v_type)] = avg_cap_fade  # store in avg_ages_dict
                depot_avg_sums[depot_id].extend([avg_cap_fade] * n)
                vehicle_type_avg_sums[v_type].extend([avg_cap_fade] * n)


                #results get put in for the next year, due to how indizes work into the index of year
                #could also be imagined as,
                result_dict[(depot_id, v_type, year)] = avg_cap_fade
                results_array[depot_index, vt_index, year] = avg_cap_fade
            else:
                avg_cap_fade = None
                results_array[depot_index, vt_index, year] = np.nan

    depot_avg = {depot_id: np.sum(values)/len(vehicles_of_depot[depot_id]) for depot_id, values in depot_avg_sums.items()}
    vehicle_type_avg = {vt_id: np.sum(values)/len(vehicles_of_vehicletype[vt_id]) for vt_id, values in vehicle_type_avg_sums.items()}

    return avg_cap_fade_dict, depot_avg, vehicle_type_avg



def create_cap_fade_table(vehicles, avg_cap_fade_dict, depot_avg, vehicle_type_avg, year):
    # calculate total weighted average
    sum_cap_fades = sum([vehicle.cap_fade for vehicle in vehicles])  # Sum up the ages of all vehicles
    all_avg_cap_fade = sum_cap_fades / len(vehicles)

    all_avg_file = os.path.join(folder_path, f"average_capacity_fades_in_year{year}.csv")
    with open(all_avg_file, "w") as file:
        file.write(f"Average capacity fade for vehicles after {year} years\n")
        columns = ['Depot'] + [all_vehicletypes[vt_id].name for vt_id in all_vehicletypes.keys()] + [
            'Depot Weighted Average']
        file.write(",".join(columns) + "\n")

        for depot_id in all_depots.keys():
            rows = [all_depots[depot_id].name]
            for vt_id in all_vehicletypes.keys():
                avg_cap_fade = avg_cap_fade_dict.get((depot_id, vt_id), None)
                rows.append(f'{avg_cap_fade:.3f}' if avg_cap_fade is not None else "---")
            depot_avg_ = depot_avg[depot_id]

            rows.append(f'{depot_avg_:.3f}')
            file.write(",".join(rows) + "\n")

        # zwischen oneweek und oneday unterscheiden
        # todo: wieder auf oneweek anpassen!
        avg_row = ["Vehicle Type Weighted Average"] + [f'{vehicle_type_avg[vt_id]:.3f}' for vt_id in
                                                       vehicle_type_avg.keys()]
        avg_row.append(f'{all_avg_cap_fade:.3f}')
        file.write(",".join(avg_row) + "\n")

    return



###GIVEN CODE:
def list_scenarios(database_url: str):
    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        scenarios = session.query(Scenario).all()
        for scenario in scenarios:
            rotation_count = (
                session.query(Rotation)
                .filter(Rotation.scenario_id == scenario.id)
                .count()
            )
            print(f"{scenario.id}: {scenario.name} with {rotation_count} rotations.")
###

if __name__ == "__main__":
    ###GIVEN CODE:      (pick scenario)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario_id",
        "--scenario-id",
        type=int,
        help="The id of the scenario to be simulated. Run with --list-scenarios to see all available scenarios.",
    )
    parser.add_argument(
        "--list_scenarios",
        "--list-scenarios",
        action="store_true",
        help="List all available scenarios.",
    )
    parser.add_argument(
        "--database_url",
        "--database-url",
        type=str,
        help="The url of the database to be used. If it is not specified, the environment variable DATABASE_URL is used.",
        required=False,
    )

    args = parser.parse_args()

    if args.database_url is None:
        if "DATABASE_URL" not in os.environ:
            raise ValueError(
                "The database url must be specified either as an argument or as the environment variable DATABASE_URL."
            )
        args.database_url = os.environ["DATABASE_URL"]

    if args.list_scenarios:
        list_scenarios(args.database_url)
        exit()

    if args.scenario_id is None:
        raise ValueError(
            "The scenario id must be specified. Use --list-scenarios to see all available scenarios, then run with "
            "--scenario-id <id>."
        )
    ###

    engine = create_engine(args.database_url, echo=False)
    with Session(engine) as session:
        scenario = session.query(Scenario).filter(Scenario.id == args.scenario_id).one()
        assert isinstance(scenario, Scenario)

        #collect all classes_needed from Dataframe into dictionaries for easy access:
        classes_needed = ['VehicleType', 'Depot', 'Trip', 'Route']
        for c in classes_needed:
            class_type = globals().get(c)   #converts String
            query_result = session.query(class_type).filter(class_type.scenario_id == scenario.id).all()
            globals()[f"all_{c.lower()}s"] = {x.id: x for x in query_result}      #e.g. dictionary is called all_vehicletypes, keys are vehicletype ids

        # create helpful dict that references depots by station id instead of depot id:
        depot_station_ids = {depot.station_id: depot for depot in all_depots.values()}

        #The code interacts with Events and Vehicles in a more complex way, so they get handled separately
        #collect all DRIVING and CHARGING events:
        all_driving_events = session.query(Event).filter(Event.scenario_id == scenario.id).filter(Event.event_type == EventType.DRIVING).all()
        all_charging_events = session.query(Event).filter(Event.scenario_id == scenario.id, or_(Event.event_type == EventType.CHARGING_DEPOT, Event.event_type == EventType.CHARGING_OPPORTUNITY)).all()

        all_charging_events = filter_uniques(all_charging_events)
        all_driving_events = filter_uniques(all_driving_events)


        #sort driving and charging events by vehicle
        #saves the lists in a dictionary with vehicle_ids as keys:
        driving_events_by_vehicle = defaultdict(list)
        for event in all_driving_events:
            #makes sure there are no duplicates:
            driving_events_by_vehicle[event.vehicle_id].append(event)
        charging_events_by_vehicle = defaultdict(list)
        for event in all_charging_events:
            charging_events_by_vehicle[event.vehicle_id].append(event)

        #remove redundant charging events between weeks to not count them both
        for veh_id, charging_events in charging_events_by_vehicle.items():
            if charging_events:  #check if the list is non-empty to avoid errors
                charging_events.pop()     ##remove last charging event from each list to delete redundancies

        #todo: to be more accurate sort the events by date and remove the latest

        #collect all vehicles from Dataframe:
        all_vehicles = session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).all()

        #Vehicles will be saved as new objects to modify parameters. All vehicles saved as dict and list for accessibility
        #since we are altering the objects our list/dict are referencing directly, they can be used interchangeably!
        all_vehicles_d = {}
        all_vehicles_l = []
        vehicles_of_depot = defaultdict(list)   #dict to sort vehicles into lists for each depot
        vehicles_of_vehicletype = defaultdict(list)  ##dict to sort vehicles into lists for each vehicle type

        #create Vehicle objects, assign events, assign depots:
        #things are all done in one loop for efficiency

        for v in all_vehicles:
            vehicle = Vehicle_new(v)
            #assign driving and charging events from dict:
            vehicle.driving_events = driving_events_by_vehicle.get(v.id, [])       #needed for depot assignment
            vehicle.charging_events = charging_events_by_vehicle.get(v.id, [])

            #check if every vehicle has a list of events!!!!!!
            has_charging_events = bool(vehicle.charging_events)
            if not vehicle.charging_events:
                print(f"Warning: Vehicle ID {vehicle.id} has no charging events.")

            #assign vehicles to their depot, dataset only assigns one depot to each vehicle (this was double-checked as well)
            for event in vehicle.driving_events:
                r = all_routes[all_trips[event.trip_id].route_id]   #accesses route of event through trip id
                #checks if route has a start/destination that is a depot, if so depot is assigned and loop can be broken
                if r.departure_station_id in depot_station_ids.keys():
                    vehicle.depot = depot_station_ids[r.departure_station_id]  #assigned to attribute depot
                    break
                elif r.arrival_station_id in depot_station_ids.keys():
                    vehicle.depot = depot_station_ids[r.arrival_station_id]
                    break

            #save in dict and list
            all_vehicles_d[v.id] = vehicle
            all_vehicles_l.append(vehicle)

            #group vehicles by depot for visualization thelater
            if vehicle.depot:
                vehicles_of_depot[vehicle.depot.id].append(vehicle)
            else:
                print(f"Warning: Vehicle {vehicle.id} has no assigned depot.")

            # group vehicles by vehicle_type:
            vehicles_of_vehicletype[vehicle.vehicle_type.id].append(vehicle)

        #check for doubled assignments
        for vt_id in vehicles_of_vehicletype.keys():
            vehicles_of_vehicletype[vt_id] = filter_uniques(vehicles_of_vehicletype[vt_id])
        for depot_id in vehicles_of_depot.keys():
            vehicles_of_depot[depot_id] = filter_uniques(vehicles_of_depot[depot_id])


        #choose years to be simulated --> todo: turn into argument for running code, like scenario_id
        # todo: check if years/age are always implemented correctly in the code
        years = 12
        #note: the age of a vehicle during the year is always year-1 (analogous to birthdays)!
        #to iterate through array
        depot_indizes = {depot_id: i for i, depot_id in enumerate(all_depots.keys())}
        vehicle_type_indizes = {v_type_id: i for i, v_type_id in enumerate(all_vehicletypes.keys())}


        #saves capacity fade at the BEGINNING of each year, includes year 13 where all SoH are = 0 and the vehicles get replaced
        results_array = np.full((len(all_depots), len(all_vehicletypes), years+1), np.nan)
        #set capacity fade for the beginning of the first year to zero for all vehicle types and depots
        #index position can be interpreted as vehicle age during that year
        results_array[:, :, 0] = 0  #capacity fade is 0 initially
        result_dict = {}  # saves entries three-dimensionally with depot id, vt_id and year as keys

        folder_path = os.path.join(os.getcwd(), f'capfade_distributions_per_year')
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        #simulate capacity fade for 12 years, starting at year 1 where the SoH gets altered first!
        for year in range(1, years+1):
            age = year-1

            #calculates yearly degeneration for all vehicles
            calc_yearly_degen(all_vehicles_l)
            #todo: create bool if you want distribution printed
            avg_cap_fade_dict, depot_avg, vehicle_type_avg = create_cap_fade_array(all_vehicles_l, year, result_dict, results_array)
            create_cap_fade_table(all_vehicles_l, avg_cap_fade_dict, depot_avg, vehicle_type_avg, year)

        #visualize SoH progression of each vehicletype for each depot through the years
        output_folder = os.path.join(os.getcwd(), "avg_soh_progression")
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        for vt_id, vt_index in vehicle_type_indizes.items():
            vt_name = all_vehicletypes[vt_id].name

            plt.figure(figsize=(12, 8))
            plt.title(f"Average Capacity Fade Progression for {vt_name}", fontsize=25)
            plt.xlabel("Years")
            plt.ylabel("State of Health")
            plt.ylim(bottom=0)
            #grabh gets cut of at SoH = 0, to easily read the lifespan
            plt.axhline(y=0, linestyle=":", label="SOH = 0")
            plt.grid(True)

            #
            x_values = list(range(0, years+1))

            for depot_id, depot_index in depot_indizes.items():
                depot_name = all_depots[depot_id].name_short
                y_values = 1 - results_array[depot_index, vt_index, :]

                if not np.isnan(y_values).all():  # Only plot if there is valid data
                    plt.plot(x_values, y_values, label=f"{depot_name}")

            #also plot the overall soh progression!
            #
            y_values_all_0 = []
            y_values_all = []
            for year in range(0, years+1):
                #avg_soh_0 hat keine soh < 0!! (Fahrzeuge scheiden aus)
                #todo: welcher approach ist richtig??
                avg_soh_0 =  np.mean([veh.yearly_soh[year] for veh in vehicles_of_vehicletype[vt_id]])
                y_values_all_0.append(avg_soh_0)
                avg_soh =  np.mean([1 - veh.yearly_cap_fade[year] for veh in vehicles_of_vehicletype[vt_id]])
                y_values_all.append(avg_soh)

            plt.plot(x_values, y_values_all_0, label="All Depots, SoH > 0", color="grey", linewidth=2)
            plt.plot(x_values, y_values_all, label = "All Depots", color = "black", linewidth = 2)


            filename = os.path.join(output_folder, vt_name + ".png")
            plt.legend(title = "Depot:")
            plt.tight_layout()
            plt.savefig(filename)
            plt.close()


        max_ages = np.full((len(all_depots), len(all_vehicletypes)), None)

        for depot_id, depot_index in depot_indizes.items():
            depot_name_short = all_depots[depot_id].name_short
            filename = os.path.join(output_folder, f"{depot_name_short}_avg_soh_progression_table.csv")

            with open(filename, "w") as file:
                #Header: Vehicle types as rows and years as columns
                file.write(f"Average SoH for {depot_name_short} Over {years} Years\n" )
                columns = ['Vehicle Type'] + [f"Age {year}" for year in range(1,years+1)] + ["Lifespan"]
                file.write(",".join(columns) + "\n")

                for vt_id, vt_index in vehicle_type_indizes.items():
                    row = [all_vehicletypes[vt_id].name]

                    for age in range(0, years):
                        #set maximum age to 12 years
                        if age >= years:
                            row.append("0")
                            max_ages[depot_index, vt_index] = years
                            break

                        cap_fade = results_array[depot_index, vt_index, age]
                        if not np.isnan(cap_fade):
                            entry = 1 - cap_fade
                            # if SoH dips elow zero, vehicle does not reach the next age. current age gets set as max_age
                            if entry < 0:
                                max_ages[depot_index, vt_index] = age
                                break
                            # show SoH progression while SoH is above zero
                            else:
                                row.append(f"{entry:.3f}")
                        #fill rows with blank entries when there are no vehicles assigned
                        else:
                            row.append('---')

                    file.write(",".join(row) + "\n")

        print(max_ages)

        np.save("soh_progression.npy", results_array)





