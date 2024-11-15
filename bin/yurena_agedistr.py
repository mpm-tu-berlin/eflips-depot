import numpy as np
import math
import matplotlib.pyplot as plt
from collections import defaultdict

###GIVEN CODE:
#! /usr/bin/env python3
import argparse
import os
import warnings

from eflips.model import *
from eflips.model import ConsistencyWarning
from sqlalchemy import create_engine, distinct, false, or_
from sqlalchemy.orm import Session

from eflips.depot.api import (
    add_evaluation_to_database,
    delete_depots,
    init_simulation,
    insert_dummy_standby_departure_events,
    run_simulation,
    generate_realistic_depot_layout,
    simple_consumption_simulation,
    apply_even_smart_charging,
)
###



class VehicleType_new:
    def __init__(self, vehicle_type):
        self.vehicle_type_id = vehicle_type.id
        self.vehicle_type_name = vehicle_type.name
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
        self.vehicle_id = vehicle.id
        self.soh = soh                  #init as 100%
        self.cap_fade = 0
        self.needs_replacement = False
        self.cycle_count = 0             #tracks cycles through event-dataset to calculate age

        #store all events:
        self.driving_events = []
        self.charging_events = []

#not used, necessary?
class DepotParameters:
    def __init__(self, depot,  avg, std, range, replacement_rate):
        self.depot = depot
        self.avg = avg
        self.std = std
        self.range = range
        self.replacement_rate = replacement_rate


#helpful function to ensure that there are no event duplicates
def filter_unique_events(events):
    unique_events = []
    logged_event_ids = set()
    for event in events:
        if event.id not in logged_event_ids:
            unique_events.append(event)
            logged_event_ids.add(event.id)
    return unique_events

def update_soh(veh, d_cf):
    veh.cap_fade += d_cf
    veh.soh = 1 - (veh.cap_fade/ (0.2* veh.full_capacity))  #full capacity as Q_nom
    if veh.soh <= 0:
        veh.needs_replacement = True
        veh.soh = 0


def calc_cap_fade(event, veh, T = 305.15):
    #Parameter:
    #sidenote: could be defined outside of function as global constants for code efficiency, are left here for clarity
    k_s = [-4.092e-4, -2.167, 1.408e-5, 6.130]
    E_a = 78060                 #Aktivierungsenergie in mol/J
    R = 8.314                   #Gaskonstante in J/(molK)
    T_ref = 298.15              #Referenzwert Temperatur in K
    available_cap = veh.battery_capacity

    # Calculates for the driving event
    soc_avg = (event.soc_start + event.soc_end) / 2
    soc_dev = abs(event.soc_end - event.soc_start)/2      #Shuyao schlägt das lieber vor!!!!!
    #soc_dev = math.sqrt(3 / 2 * ((event.soc_start - soc_avg) ** 2 + (event.soc_end - soc_avg) ** 2))
    Ah = abs(event.soc_start - event.soc_end) * available_cap        #SoC auf available_cap bezogen, nicht ges_cap, da manchmal auch <0 (reserve capacity)
    d_cf = ((k_s[0] * soc_dev * math.exp(k_s[1] * soc_avg) + k_s[2] * math.exp(k_s[3] * soc_dev)) * math.exp(
        -(E_a / R) * (1 / T - 1 / T_ref)))*Ah

    relative_cf = d_cf / available_cap
    #veh.events_processed += 1
    update_soh(veh, d_cf)
    return(d_cf)



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
        vehicles_of_vehicletype = defaultdict(list) ##dict to sort vehicles into lists for each vehicle type

        #create Vehicle objects, assign events, assign depots:
        #things are all done in one loop for efficiency

        for v in all_vehicles:
            vehicle = Vehicle_new(v)
            #assign driving and charging events from dict:
            vehicle.driving_events = driving_events_by_vehicle.get(v.id, [])        #needed for depot assignment
            vehicle.charging_events = charging_events_by_vehicle.get(v.id, [])

            #check if every vehicle has a list of events!!!!!!
            has_charging_events = bool(vehicle.charging_events)
            if not vehicle.charging_events:
                print(f"Warning: Vehicle ID {vehicle.vehicle_id} has no charging events.")

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
                print(f"Warning: Vehicle {vehicle.vehicle_id} has no assigned depot.")

            #group vehicles by vehicle_type:
            vehicles_of_vehicletype[vehicle.vehicle_type.id].append(vehicle)

        #RUN UNTIL ALL VEHICLES REACH EOL-CONDITION, VISUALIZE VEHICLE AGE PER DEPOT

        operational_vehicles = all_vehicles_l.copy()  # only contains vehicles with needs_replacement = False
        # cycle through bus plan until all vehicles need replacement!
        # vehicles that reach EoL condition fall out of loop
        weeks_passed = 0
        while operational_vehicles:
            remaining_vehicles = []

            for vehicle in operational_vehicles:
                unique_charging_events = filter_unique_events(vehicle.charging_events)
                # process each UNIQUE charging event
                for event in unique_charging_events:
                    d_cf = calc_cap_fade(event, vehicle)

                vehicle.cycle_count += 1  # for each time driving events get looped, age goes up by one cycle
                if vehicle.needs_replacement:
                    operational_vehicles.remove(vehicle)  # remove non-operatable vehicle from list
                    break

        # Find vehicles that are still operational
        operational_vehicles = [vehicle for vehicle in all_vehicles_l if not vehicle.needs_replacement]

        # Check if any vehicles are still operational
        if operational_vehicles:
            print("The following vehicles have not reached EoL and are still operational:")
            for vehicle in operational_vehicles:
                print(vehicle)
                print(len(vehicle.driving_events))
        else:
            print("All vehicles have reached their end-of-life and need replacement.")


        #todo: manually add maximum age based on values from calendaric degradation (ausreißer unrealistisch)

        #create folder to store results in
        folder_name = 'age_distributions'
        folder_path = os.path.join(os.getcwd(), folder_name)
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        #create arrays to store average ages in, for depots and for vehicle types, needed tor table later on
        avg_ages_dict = {}
        depot_avg = defaultdict(list)
        vehicle_type_avg = defaultdict(list)

        #visualize age distribution sorted by depots and vehicle types
        for depot in all_depots.values():
            vehicles_in_depot = vehicles_of_depot[depot.id]

            fig, axes = plt.subplots(1, len(all_vehicletypes), figsize=(18, 6))
            fig.suptitle(f'Distribution of Age in Depot: {depot.name}', fontsize=25)

            ax_pos = 0  #help variable for position of subplot
            #loop through each vehicle type to generate and save a histogram
            for v_type in all_vehicletypes.keys():
                #Filter vehicles of vehicle type out of vehicles of that depot
                vehicles_of_type = [(vehicle.cycle_count)*7/365 for vehicle in vehicles_in_depot if vehicle.vehicle_type_id == v_type]
                n = len(vehicles_of_type)
                v_type_name = all_vehicletypes[v_type].name


                #Create histogram for SoH distribution:
                ax = axes[ax_pos]
                ax.hist(vehicles_of_type)
                ax.set_xlabel(f'Age after EoL condition is met in years')
                ax.tick_params(axis='x', rotation=45)  # rotates labels for readability
                ax.set_ylabel('Number of Vehicles')
                ax.set_title(f'Age distribution for {v_type_name}    - n = {n}')

                #checks if there are no vehicles assigned to avoid error messages
                if vehicles_of_type:
                    #show other relevant stats in graphs
                    avg_age = np.mean(vehicles_of_type)
                    std_dev = np.std(vehicles_of_type)
                    age_range = np.ptp(vehicles_of_type)
                    if avg_age > 0:
                        repl_rate = math.ceil(n/avg_age)
                    else:
                        repl_rate = None

                    avg_ages_dict[(depot.id, v_type)] = avg_age

                    globals()[f"{depot.name_short}_params"] = DepotParameters(depot, avg_age, std_dev, age_range, repl_rate)

                    additional_stats = (f'Average Age: {avg_age:.2f} years\n'
                                  f'Standard deviation: {std_dev:.2f} years\n'
                                  f'Range: {age_range:.2f} years\n'
                                  f'replacement rate: {repl_rate}')

                    #create box to display information
                    ax.text(0.95, 0.95, additional_stats, transform=ax.transAxes, verticalalignment='top', horizontalalignment='right', bbox=dict(facecolor='white', alpha=0.5))

                    #save the weighted averages for table later on
                    avg_ages_dict[(depot.id, v_type)] = avg_age  #store in avg_ages_dict

                    #store sum of ages for weighted data of averages
                    depot_avg[depot.id].extend([avg_age] * n)
                    vehicle_type_avg[v_type].extend([avg_age] * n)

                ax_pos += 1  # move to next subplot position

            plt.tight_layout()
            plt.savefig(os.path.join(folder_path, f'age_distribution_for_{depot.name}.png'))
            plt.close(fig)


        #calculate final weighted averages by depot and vehicle type
        depot_avg = {depot_id: np.mean(values) for depot_id, values in depot_avg.items()}
        vehicle_type_avg = {vt_id: np.mean(values) for vt_id, values in vehicle_type_avg.items()}

        #calculate total weighted average
        sum_ages = sum((vehicle.cycle_count)*7/365 for vehicle in all_vehicles_l)  # Sum up the ages of all vehicles
        all_avg = sum_ages / len(all_vehicles_l)


        all_avg_file = os.path.join(folder_path, "average_ages_table.csv")
        with open(all_avg_file, "w") as file:
            file.write("Average age for vehicles in years\n")
            columns = ['Depot'] + [all_vehicletypes[vt_id].name for vt_id in all_vehicletypes.keys()] + ['Depot Weighted Average']
            file.write(",".join(columns) + "\n")

            for depot_id in all_depots.keys():
                rows = [all_depots[depot_id].name]
                for vt_id in all_vehicletypes.keys():
                    avg_age = avg_ages_dict.get((depot_id, vt_id), None)
                    rows.append(f'{avg_age:.2f}' if avg_age is not None else "---")
                depot_avg_ = depot_avg[depot_id]
                rows.append(f'{depot_avg_:.2f}')
                file.write(",".join(rows) + "\n")

            avg_row = ["Vehicle Type Weighted Average"] + [f'{vehicle_type_avg[vt_id]:.2f}' for vt_id in all_vehicletypes.keys()]
            avg_row.append(f'{all_avg:.2f}')
            file.write(",".join(avg_row) + "\n")


        #calculate replacement rates and and creaty steady states for each vehicle type
        replacement_rates = {vt_id: math.floor(age) for vt_id, age in vehicle_type_avg.items()}
        replacements_per_year = {}
        for vt_id in all_vehicletypes.keys():
            replacements_per_year[vt_id] = math.ceil(len(vehicles_of_vehicletype[vt_id])/vehicle_type_avg[vt_id])
        print(replacements_per_year)
        print(replacement_rates)

        #todo: create steady state for each vt based on len() and replacement_rates

        """
                new_vehicle_type = VehicleType(name = "Ebuscon_80soh", scenario=scenario)  #usw, unnötige muss ich nicht parametrisieren
                #opp_charge_capable, brauche ich noch
                #rechtsklicK auf vehicleType in database "modify Table" -> alle parameter mit not null müssen parametrisiert werden
                session.add(new_vehicle_type)

                ...
                session.commit()  #zuallerletzt! alle Änderungen in Database übertragen, also auch erst machen wenn alten vehicletypes nicht mehr benötigt werden!

