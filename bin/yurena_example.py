import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import math
import scipy as sp
from scipy import integrate
import csv



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
        self.vehicle_type = vehicle_types_d[vehicle.vehicle_type_id]
        super().__init__(self.vehicle_type)

        #parameters:
        self.vehicle_id = vehicle.id
        self.soh = soh                  #init as 100%
        self.cap_fade = 0

        #store all events:
        self.driving_events = []
        self.charging_events = []

        needs_replacement = False



def update_soh(event, vehicle_id, d_cf):
    all_vehicles_d[vehicle_id].cap_fade += d_cf
    all_vehicles_d[vehicle_id].soh = 1 - (all_vehicles_d[vehicle_id].cap_fade/(0.2 * event.vehicle_type.battery_capacity))  # includes EOL condition at 80% of initial battery capacity
    #how does battery capacity reserve factor in??
    if all_vehicles_d[vehicle_id].soh <= 0:
        all_vehicles_d[vehicle_id].needs_replacement = True



def calc_cap_fade(event, T = 305.15):
    #Parameter:
    k_s = [-4.092e-4, -2.167, 1.408e-5, 6.130]
    E_a = 78060                 #Aktivierungsenergie in mol/J
    R = 8.314                   #Gaskonstante in J/(molK)
    T_ref = 298.15              #Referenzwert Temperatur in K
    init_cap = event.vehicle_type.battery_capacity

    if event.event_type == EventType.DRIVING:
        #Calculates for the driving event
        soc_avg = (event.soc_start + event.soc_end) / 2
        soc_dev = math.sqrt(3 * ((event.soc_end - soc_avg) ** 2 + (event.soc_start - soc_avg) ** 2))
        Ah = abs(event.soc_start - event.soc_end) * init_cap
        d_cf = ((k_s[0] * soc_dev * math.exp(k_s[1] * soc_avg) + k_s[2] * math.exp(k_s[3] * soc_dev)) * math.exp(-(E_a / R) * (1 / T - 1 / T_ref))) * Ah

    elif event.event_type == EventType.CHARGING_DEPOT or  event.event_type == EventType.CHARGING_OPPORTUNITY:
        #fragment charging events so the soc_dev corresponds with driving events
        d_soc_ = abs(event.soc_end - event.soc_start)   #calculate total SOC difference for the charging event
        splits = math.ceil(d_soc_ / d_soc_avg)          #number of fragments needed to adjust
        soc_increment = d_soc_ / splits

        d_cf_total = 0
        #calculate capacity fade for each increment
        for i in range(splits):
            #calculate SOC for the split
            soc_start = event.soc_start + i * soc_increment
            soc_end = soc_start + soc_increment

            #identical with method for driving events
            soc_avg = (soc_start + soc_end) / 2
            soc_dev = math.sqrt(3 * ((soc_end - soc_avg) ** 2 + (soc_start - soc_avg) ** 2))
            Ah = abs(soc_start - soc_end) * init_cap

            #calculate capacity fade for splits and sum up
            d_cf_split = ((k_s[0] * soc_dev * math.exp(k_s[1] * soc_avg) + k_s[2] * math.exp(k_s[3] * soc_dev)) * math.exp(-(E_a / R) * (1 / T - 1 / T_ref))) * Ah
            d_cf_total += d_cf_split

        d_cf = d_cf_total

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

        #collect all DRIVING and CHARGING events
        #Dataset for 1 day and 244 vehicles
        all_driving_events = session.query(Event).filter(Event.scenario_id == scenario.id).filter(Event.event_type == EventType.DRIVING).all()
        all_charging_events = session.query(Event).filter(Event.scenario_id == scenario.id, or_(Event.event_type == EventType.CHARGING_DEPOT, Event.event_type == EventType.CHARGING_OPPORTUNITY)).all()

        #Query all vehicle types and vehicles and save them in a dictionary
        vehicle_types_l = session.query(VehicleType).filter(VehicleType.scenario_id == scenario.id).all()
        vehicle_types_d = {vt.id: vt for vt in vehicle_types_l}
        vehicle_type_ids = vehicle_types_d.keys()

        all_vehicles = session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).all()
        all_vehicles_d = {}
        all_vehicles_l = []          #also as list for easier access


        #create Vehicle-objects and assign events to vehicles
        for v in all_vehicles:
            vehicle = Vehicle_new(v)
            vehicle.driving_events = [event for event in all_driving_events if event.vehicle_id == v.id]
            vehicle.charging_events = [event for event in all_charging_events if event.vehicle_id == v.id]

            # erstmal als list und dict (key: vehicle_id) speichern
            all_vehicles_d[v.id] = vehicle
            all_vehicles_l.append(vehicle)

        #iterate all events, calc cap_fade and update soh
        #same for charging events?????


        #calculate average d_soc:
        d_soc = [abs(event.soc_start - event.soc_end) for event in all_driving_events]
        d_soc_avg = sum(d_soc)/len(d_soc)
        #charging events nach diesem average splitten!!!

        days_passed = 0
        while days_passed < (10):
            for event in all_driving_events:
                calc_cap_fade(event)       #Temperaturabhängigkeit????#
            for event in all_charging_events:
                calc_cap_fade(event)                #--> bad results -> examine soc_dev!!!

            days_passed += 1


        #Loop through each vehicle type to generate and save a histogram
        for v_type in vehicle_type_ids:
            #Filter vehicles of type
            vehicles_of_type = [vehicle.soh for vehicle in all_vehicles_l if vehicle.vehicle_type_id == v_type]
            n = len(vehicles_of_type)
            v_type_name = vehicle_types_d[v_type].name

            #Create a new figure and axis for each histogram
            fig, ax = plt.subplots()
            ax.hist(vehicles_of_type)

            ax.set_xlabel('State of Health')
            ax.set_ylabel('Number of Vehicles')
            ax.set_title(f'Histogram for {v_type_name}    - n = {n}')
            # Save the histogram with the vehicle type in the filename
            plt.savefig(f'histogram_{v_type_name}.png')
            plt.close(fig)  # Close the figure after saving to free memory


