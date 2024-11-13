import pandas as pd
import numpy as np
import math
import matplotlib.pyplot as plt
import scipy as sp
from docutils.utils.math.latex2mathml import math_alphabets
from scipy import integrate
import csv
from collections import defaultdict
import cProfile         #optimize speed of running code

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
        self.battery_capacity = (vehicle_type.battery_capacity * 1000)/(3.2 * 200)      #[Ah]
        #2.5 V
        self.battery_capacity_reserve = vehicle_type.battery_capacity_reserve
        #ggfls. noch consumption und charging curve?



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

def update_soh(event, vehicle_id, d_cf):
    veh = all_vehicles_d[vehicle_id]
    veh.cap_fade += d_cf
    veh.soh = 1 - (veh.cap_fade/(0.2 * event.vehicle_type.battery_capacity))  # includes EOL condition at 80% of initial battery capacity
    #how does battery capacity reserve factor in??
    if veh.soh <= 0:
        veh.needs_replacement = True
        veh.soh = 0


def calc_cap_fade(event, T = 305.15):
    #Parameter:
    #sidenote: could be defined outside of function as global constants for code efficiency, are left here for clarity
    k_s = [-4.092e-4, -2.167, 1.408e-5, 6.130]
    E_a = 78060                 #Aktivierungsenergie in mol/J
    R = 8.314                   #Gaskonstante in J/(molK)
    T_ref = 298.15              #Referenzwert Temperatur in K
    init_cap = event.vehicle_type.battery_capacity

    # Calculates for the driving event
    soc_avg = (event.soc_start + event.soc_end) / 2
    soc_dev = math.sqrt(3 / 2 * ((event.soc_start - soc_avg) ** 2 + (event.soc_end - soc_avg) ** 2))
    Ah = abs(event.soc_start - event.soc_end) * init_cap
    d_cf = ((k_s[0] * soc_dev * math.exp(k_s[1] * soc_avg) + k_s[2] * math.exp(k_s[3] * soc_dev)) * math.exp(
        -(E_a / R) * (1 / T - 1 / T_ref))) * Ah

    relative_cf = d_cf / init_cap
    update_soh(event, event.vehicle_id, d_cf)
    return(relative_cf)


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
            driving_events_by_vehicle[event.vehicle_id].append(event)
        charging_events_by_vehicle = defaultdict(list)
        for event in all_charging_events:
            charging_events_by_vehicle[event.vehicle_id].append(event)

        #collect all vehicles from Dataframe:
        all_vehicles = session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).all()

        #Vehicles will be saved as new objects to modify parameters. All vehicles saved as dict and list for accessibility
        #since we are altering the objects our list/dict are referencing directly, they can be used interchangeably!
        all_vehicles_d = {}
        all_vehicles_l = []
        vehicles_of_depot = defaultdict(list)   #dict to sort vehicles into list for each depots
        #create Vehicle objects, assign events, assign depots:
        #things are all done in one loop for efficiency
        for v in all_vehicles:
            vehicle = Vehicle_new(v)
            #assign driving and charging events from dict:
            vehicle.driving_events = driving_events_by_vehicle.get(v.id, [])
            vehicle.charging_events = charging_events_by_vehicle.get(v.id, [])


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

            #group vehicles by depot for visualization later
            if vehicle.depot:
                vehicles_of_depot[vehicle.depot.id].append(vehicle)
            else:
                print(f"Vehicle {vehicle.vehicle_id} has no assigned depot.")


        #calculate average d_soc:
        d_soc = [abs(event.soc_start - event.soc_end) for event in all_driving_events]
        d_soc_avg = sum(d_soc)/len(d_soc)
        #charging events nach diesem average splitten!!!


        #CALC AND VISUALIZE DEGENERATION FOR A YEAR:
        weeks_passed = 0
        while weeks_passed < 52:
            for event in all_driving_events:
                calc_cap_fade(event)
            weeks_passed += 1

       #create plots to visualize SoH distribution, sorted by depot
        for depot in all_depots.values():
            vehicles_in_depot = vehicles_of_depot[depot.id]

            fig, axes = plt.subplots(1, len(all_vehicletypes), figsize=(18, 6))
            fig.suptitle(f'Distribution of State of Health in Depot: {depot.name}', fontsize=25)

            ax_pos = 0    #help variable for position of subplot
            #Loop through each vehicle type to generate and save a histogram
            for v_type in all_vehicletypes.keys():
                #Filter vehicles of vehicle type out of vehicles of that depot
                #alternatively: (vehicle.cycle_count * 7)/365 instead of vehicle.soh for age distribution
                vehicles_of_type = [vehicle.soh for vehicle in vehicles_in_depot if vehicle.vehicle_type_id == v_type]
                avg_soh = np.mean(vehicles_of_type)

                n = len(vehicles_of_type)
                v_type_name = all_vehicletypes[v_type].name
                #Create histogram for SoH distribution:
                ax = axes[ax_pos]
                ax.hist(vehicles_of_type)
                ax.set_xlabel(f'State of Health [/]. Average SoH for vehicle type: {avg_soh}')
                ax.tick_params(axis='x', rotation=45)    #rotates labels for readability
                ax.set_ylabel('Number of Vehicles')
                ax.set_title(f'SoH distribution for {v_type_name}    - n = {n}')

                ax_pos += 1     #move to next subplot position

            #Save the entire histogram for the depot with fitting filename:
            plt.tight_layout()
            plt.savefig(f'histograms_for_{depot.name}.png')
            plt.close(fig)



        '''
        operational_vehicles = all_vehicles_l.copy()     #only contains vehicles with needs_replacement = Falso
        #cycle through bus plan until all vehicles need replacement!
        #vehicles that reach EoL condition fall out of loop
        #weeks_passed = 0
        while operational_vehicles:
            remaining_vehicles = []

            for vehicle in operational_vehicles:
                for event in vehicle.driving_events:
                    calc_cap_fade(event)       #Temperaturabhängigkeit????#
                #for event in vehicle.charging_events:
                #    calc_cap_fade(event)                #--> bad results -> examine soc_dev!!!

                vehicle.cycle_count += 1            #for each time driving events get looped, age goes up by one cycle
                if not vehicle.needs_replacement:
                    operational_vehicles.append(vehicle)  #remove non-operatable vehicle from list
                    break

            operational_vehicles = remaining_vehicles
        '''







