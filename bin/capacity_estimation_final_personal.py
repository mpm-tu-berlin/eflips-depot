import sqlalchemy.orm
import math
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import seaborn as sns
from eflips.model import (
    Area,
    AreaType,
    AssocPlanProcess,
    Depot,
    Event,
    EventType,
    Plan,
    Rotation,
    Scenario,
    Station,
    Trip,
    Vehicle,
    VehicleType,
    Process,
)
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
import eflips.depot.api

# given from elsewhere 
standard_block_length = 6 

def capacity_estimation(session,scenario):
    depots = session.query(Depot).filter(Depot.scenario_id == scenario.id).all()
    num_depots = len(depots)
    print(f"In dem Übergebenen Scenario wurden {num_depots} verschiedene Depots gefunden.")

    results_by_depot = {} 


    for depot in depots:
        result_by_area = first_simulation_run(session,scenario,depot)
        total_results = simulations_loop(result_by_area, session, scenario,depot)
        optimal_simulation(total_results, session, scenario,depot)

        results_by_depot[depot.id] = total_results

    
    return results_by_depot


# First simulation run with Direct parking spaces only
# Result: Number of Direct parking spaces, for each VehicleType, with which a simulation is possible.
def first_simulation_run(session,scenario,depot):

    # Query of all existing VehicleTypes in the Scanario
    # If no VehicleTypes are found --> abort 
    vehicle_types = session.query(VehicleType).filter(VehicleType.scenario_id == scenario.id).all()
    if not vehicle_types:
        print("In dem aktuellen Scenario befinden sich keine VehicleType Objekte.")
        return None
    
    for vehicle_type in vehicle_types:
        print(f"Für Depot {depot.id} wurde der Fahrzeugtyp {vehicle_type.name} (ID {vehicle_type.id}) wurde gefunden.") 

    # Query of the objects required for the simulation
        necessary_objects = necessary_object_query(session,scenario,depot)
        if any(value is None for value in necessary_objects):
            print("Plan oder einer der Prozesse konnte nicht aus dem Scenario abgefragt werden")
            continue
        else:
            plan,clean,charging,standby_departure = necessary_objects

    # Query for the number of rotations
    rotations = session.query(Rotation).filter(Rotation.scenario_id == scenario.id).count()
    
    # Creation of the corresponding Charging Areas for each found VehicleType
    # Direct-Parking-Spaces only
    charging_areas = [] 
    for vehicle_type in vehicle_types:
        charging_area = Area(
            scenario = scenario,
            name = f"Direct-Charging-Area for:{vehicle_type.name}",
            depot = depot,
            area_type = AreaType.DIRECT_ONESIDE,
            capacity = rotations,  # check: for enought capacity
            vehicle_type = vehicle_type,  
        )
        charging_areas.append(charging_area)
        session.add(charging_area)
        charging_area.processes.append(charging)
        charging_area.processes.append(standby_departure)

    # Creation of Assocs 
    create_depot_areas_and_processes(session,scenario,plan,clean,charging,standby_departure)
    
    # Clear previous vehicle and event data
    session.query(Rotation).filter(Rotation.scenario_id == scenario.id).update({"vehicle_id": None})
    session.query(Event).filter(Event.scenario == scenario).delete()
    session.query(Vehicle).filter(Vehicle.scenario == scenario).delete()

    # Run the simulation
    try:
        eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=True)
        eflips.depot.api.simulate_scenario(scenario, repetition_period=timedelta(days=1))
        session.flush()
        session.expire_all()
        eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=False) 

    except AssertionError:
        print("Fehler: SoC ist geringer als Erwartet.")
        session.rollback()
        return None 
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten: {e}")
        session.rollback()
        return None


    # Determine peak usage of Direct-Parking-Spaces for each ChargingArea 
    result_by_area = give_back_peak_usage_direct_for_multiple_types(session,charging_areas,scenario)
    session.rollback()
    
    # Read and print command for the results of the first simulation run
    for area_name, data in result_by_area.items():
        name = area_name
        peak_count = data['peak_usage']              
        vehicle_count_by_type = data['vehicle_count'] 
        vehicle_type = data['vehicle_type']           

        print(f" Für {name}: Die Spitzenbelastung ist {peak_count} Fahrzeuge. Und es sind {vehicle_count_by_type} Fahrzeuge von diesem Typ aktiv. Der Vehicle_Type lautet {vehicle_type}")
    
    return result_by_area


def simulations_loop(result_by_area,session,scenario,depot):
    """
    This function runs the depot simulation in a loop, where a block parking line is added in each iteration.
    In the end, the parking configuration with the smallest area is chosen for each VehicleType.
    """

    # Check whether the provided results are not faulty
    if not result_by_area:
        print("Die übergebenen Ergebnisse sind fehlerhalft.")
        return None
    
    # Creation of Assocs
    plan,clean,charging,standby_departure = necessary_object_query(session,scenario,depot)

    # List of needed processes
    processes = [charging,standby_departure]

    # List for the results of all VehicleTypes 
    total_results = {}

    # Iteration over the results from the first simulation run for each VehicleType
    for area_name, data in result_by_area.items():
        name = area_name
        peak_count = data['peak_usage']             
        vehicle_count_by_type = data['vehicle_count']  
        vehicle_type = data['vehicle_type']          

        print(f"Simulation für den Bus-Type{vehicle_type}")

        # List for the result of the current VehicleType 
        results = []

        # Calculation of how many line parking spaces are still smaller than the required direct parking spaces for a VehicleType
        num_of_line_parking_spaces = calc_num_of_line_parking_spaces(session,peak_count,vehicle_type)
        if num_of_line_parking_spaces is None:
            print("Keine Werte für Breite oder Länge in VehicleType Objekt gefunden")
            return None 
        else:
            max_line_count,extra_line,extra_line_length = num_of_line_parking_spaces


        # Loop to determine the parking configuration with the smallest area for each VehicleType
        for i in range(1,max_line_count+1): # Number of possible line parking rows
            
            try:
                
                if i == max_line_count and extra_line:
                    charging_line_area_extra = create_charging_area(session,scenario,depot,name,AreaType.LINE,extra_line_length,vehicle_type,processes)
                    

                    for b in range(i-1):
                        charging_line_area = create_charging_area(session,scenario,depot,name,AreaType.LINE,standard_block_length,vehicle_type,processes)
                    
                else:
                    # Create Line Area with variable Lines
                    for b in range(i):
                        charging_line_area = create_charging_area(session,scenario,depot,name,AreaType.LINE,standard_block_length,vehicle_type,processes)
                        

                # Create direct-charging-area: set direct spaces capacity
                charging_area = create_charging_area(session,scenario,depot,name,AreaType.DIRECT_ONESIDE,peak_count,vehicle_type,processes) 
                

                # Buffer parking spaces for the other vehicle types contained in the session
                for area_name_other, data in result_by_area.items():
                    other_vehicle_type = data['vehicle_type']
                    vehicle_count = data['vehicle_count']
                    if other_vehicle_type != vehicle_type:
                        # Charging area for buffer parking spaces
                        charging_area_buffer = create_charging_area(session,scenario,depot,name,AreaType.LINE,vehicle_count+5,other_vehicle_type,processes)
                        
                                
                
                # Call the function to connect processes
                create_depot_areas_and_processes(session,scenario,plan,clean,charging,standby_departure)
                
                # Simulation 
                # Clear previous vehicle and event data
                session.query(Rotation).filter(Rotation.scenario_id == scenario.id).update({"vehicle_id": None})
                session.query(Event).filter(Event.scenario == scenario).delete()
                session.query(Vehicle).filter(Vehicle.scenario == scenario).delete()

                eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=True)
                eflips.depot.api.simulate_scenario(scenario, repetition_period=timedelta(days=1))
                session.flush()
                session.expire_all()
                eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=False)
                
                
            except AssertionError as e:
                print(f"Iteration {i}: Für Fahrzeugtyp{vehicle_type}, Simulation fehlgeschlagen - Delay aufgetreten")
                session.rollback()
                continue  

            except Exception as e:
                print(f"Iteration:{i} Ein unerwarteter Fehler ist aufgetreten: {e}")
                session.rollback()
                continue
            else:
                print(f"Iteration:{i} Keine Fehler bei der Simulation aufgetreten.")


            # Vehicle count for the current VehicleType
            vehicle_count = session.query(Vehicle).filter(Vehicle.vehicle_type == vehicle_type).count()

            # Check whether an additional vehicle demand has arisen
            if vehicle_count > vehicle_count_by_type:
                print(f"Iteration:{i}  Für die Depotauslegung gab es einen Fahrzeugmehrbedarf. Es wurden insgesamt {vehicle_count} Fahrzeuge benötigt.")
                session.rollback()
                continue

            # Determine peak usage of direct parking spaces for the configuration with i * block_length block parking spaces:
           
            result_dict = give_back_peak_usage_direct_for_multiple_types(session, charging_area, scenario)
            cur_direct_peak = result_dict[charging_area.name]['peak_usage']

            print(cur_direct_peak)
            
            # Determine the area requirement in square meters for the current configuration
            flaeche,line_parking_slots,direct_parking_slots,simulation_with_extra_line = calculate_area_demand(session,i,cur_direct_peak,extra_line,extra_line_length,max_line_count,vehicle_type)
            
           # Store the results of this iteration for the selected VehicleType
            zeile = {
                "VehicleType": vehicle_type,
                "Area": flaeche,
                "Line Parking Slots": line_parking_slots,
                "Given Line Lenght": standard_block_length,
                "Direct Parking Slots": direct_parking_slots,
                "Vehicle Count": vehicle_count,
                "Simulation with ExtraLine": simulation_with_extra_line,
                "ExtraLine Length": extra_line_length,
                "Iteration":i
            }
            
            results.append(zeile)
            
            session.rollback()
        
        if not results:
            print(f"Keine Ergebnisse für {vehicle_type} gefunden")
            continue
        else:
            min_area_depot_configuration = min(results, key=lambda x: x["Area"])

        # Store the configuration with the smallest area for the selected VehicleType
        total_results[f"VehicleType{vehicle_type}"] = min_area_depot_configuration

    return total_results


# Simulation and saving of the best depot configuration
def optimal_simulation(total_results,session,scenario,depot):
    if not total_results:
        print("Die übergebenen Ergebnisse sind fehlerhalft.")
        return

    plan,clean,charging,standby_departure = necessary_object_query(session,scenario,depot)
    
    # List of needed processes
    processes = [charging,standby_departure]
    

    for key, value in total_results.items():
        # Unpacking the values from the inner dictionary 
        vehicle_type = value["VehicleType"]
        flaeche = value["Area"]
        line_parking_slots = value["Line Parking Slots"]
        direct_parking_slots = value["Direct Parking Slots"]
        vehicle_used = value["Vehicle Count"]
        optimum_with_extra_line = value["Simulation with ExtraLine"]
        extra_line_length = value["ExtraLine Length"]
        iteration = value["Iteration"]
        name = "ChargingArea for optimum depot configuration"

        if optimum_with_extra_line:
            charging_line_area_extra = create_charging_area(session,scenario,depot,name,AreaType.LINE,extra_line_length,vehicle_type,processes)
            
            for b in range(iteration-1):
                charging_line_area = create_charging_area(session,scenario,depot,name,AreaType.LINE,standard_block_length,vehicle_type,processes)
                
        else:
            for b in range(iteration):
                charging_line_area = create_charging_area(session,scenario,depot,name,AreaType.LINE,standard_block_length,vehicle_type,processes)

        if direct_parking_slots > 0:   
            charging_area = create_charging_area(session,scenario,depot,name,AreaType.DIRECT_ONESIDE,direct_parking_slots,vehicle_type,processes) 


        create_depot_areas_and_processes(session,scenario,plan,clean,charging,standby_departure)

    session.commit()
    # Simulation 
    # Clear previous vehicle and event data
    session.query(Rotation).filter(Rotation.scenario_id == scenario.id).update({"vehicle_id": None})
    session.query(Event).filter(Event.scenario == scenario).delete()
    session.query(Vehicle).filter(Vehicle.scenario == scenario).delete()

    # Run the simulation
    eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=True)
    session.commit()
    eflips.depot.api.simulate_scenario(scenario, repetition_period=timedelta(days=1))
    eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=False)




# ----------------------------------------------------------
# Helper Functions
# ----------------------------------------------------------

#Creates a new charging area (Area object) and associates the specified processes with it.
def create_charging_area(session,scenario,depot,name,area_type,capacity,vehicle_type,processes):

    if not isinstance(processes,list) or not processes:
        raise ValueError("Der Parameter 'processes' muss eine nicht leere Liste mit Objekten sein.")
    

    area = Area(
        scenario = scenario,
        name = name,
        depot = depot,
        area_type = area_type,
        capacity = capacity,
        vehicle_type = vehicle_type,
    )
    session.add(area)

    for process in processes:
        area.processes.append(process)
    
    return area



# Function to query the necessary objects for the simulation
def necessary_object_query(session, scenario, depot):
    plan = session.query(Plan).filter(Plan.scenario_id == scenario.id, Plan.id == depot.default_plan_id).first()
    clean = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Clean").one()
    charging = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Charging").one()
    standby_departure = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Standby Departure").one()
    return plan, clean, charging, standby_departure



# Function to link the Assocs before each simulation
def create_depot_areas_and_processes(session,scenario,plan,clean,charging,standby_departure):

    assocs = [
        AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
        AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=2),
        AssocPlanProcess(scenario=scenario, process=standby_departure, plan=plan, ordinal=3),
    ]
    session.add_all(assocs) 


# Function to determine the Direct peak usages of the different Charging Areas for the different Vehicle Types
def give_back_peak_usage_direct_for_multiple_types(session, charging_areas, scenario):
    result_by_area = {}

    if not isinstance(charging_areas, list):
        charging_areas = [charging_areas]
        

    for charging_area in charging_areas:
        # Step 1: Load all relevant events for the current charging area
        charging_events = session.query(Event).filter(
            Event.scenario_id == charging_area.scenario_id,
            Event.area_id == charging_area.id,
            Event.event_type == EventType.CHARGING_DEPOT
        ).all()

        # Fallback if no charging events are found
        if not charging_events:
            print(f"No charging events found for {charging_area.name}.")
            cur_direct_peak = 0
        else:
            # Sort the events by start time
            events_sorted_by_time = sorted(charging_events, key=lambda e: e.time_start)

            # Initialization for peak usage calculation
            current_count = 0
            cur_direct_peak = 0
            time_points = []

            # Collect all start and end points in a list
            for event in events_sorted_by_time:
                time_points.append((event.time_start, 'start'))
                time_points.append((event.time_end, 'end'))

            # Sort the time points
            time_points.sort()

            # Iterate through all time points and calculate concurrent charging operations
            for time, point_type in time_points:
                if point_type == 'start':
                    current_count += 1
                    cur_direct_peak = max(cur_direct_peak, current_count)
                elif point_type == 'end':
                    current_count -= 1

        # Number of vehicles for the current vehicle type in the charging area
        vehicle_count_by_type = session.query(func.count(Vehicle.id)).filter(
            Vehicle.vehicle_type_id == charging_area.vehicle_type_id,
            Vehicle.scenario_id == scenario.id
        ).scalar()

        # Get the vehicle type for the current charging area
        vehicle_type = session.query(VehicleType).filter(
            VehicleType.id == charging_area.vehicle_type_id
        ).first()

        # Store peak usage, vehicle count, and vehicle type in the result dictionary
        result_by_area[charging_area.name] = {
            'peak_usage': cur_direct_peak,
            'vehicle_count': vehicle_count_by_type,
            'vehicle_type': vehicle_type
        }

    return result_by_area

# Function to determine the required rows of line-parking-spaces for current VehicleType 
def calc_num_of_line_parking_spaces(session,peak_count,vehicle_type):
    # Query length and width for current VehicleType 
    x = session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() #length 
    z = session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() #width
    

    if x is not None and z is not None:

        # Area calculated for the Direct-Area divided by the are of Line-parking-spaces = maximum number of Line-parking-spaces   
        width = x*math.sin(math.radians(45))+z*math.sin(math.radians(45))
        length = x*math.sin(math.radians(45))+z*math.sin(math.radians(45)) + (peak_count-1) * z/math.cos(math.radians(45)) 
        max_line_busse = math.floor((width*length)/(x*z))

        # For given row length 
        # How many rows for the amount of Line-parking-spaces 
        max_row_count = int(max_line_busse/standard_block_length)

        # Is an additional Line-row needed? 
        extra_line_length = 0
        # ChargingArea from AreaType Line with capacity: 1 not possible 
        if max_line_busse % standard_block_length not in (1, 0):
            max_row_count += 1
            extra_line_length = max_line_busse%standard_block_length
            extra_line = True
            print(f"Es wird {max_row_count} Iterationen geben. Davon ist eine, eine Extra-Line mit der Kapazität von {extra_line_length} Parkplätzen")
        else:
            extra_line = False
            max_row_count = max_row_count

            print(f"Es wird {max_row_count} Iterationen geben")

        return max_row_count,extra_line,extra_line_length
    else:
        print(f"Keine Länge oder Breite für VehicleType{vehicle_type} gefunden")
        return None
    


# Function to determine the required area for iteration i for current VehicleType 
def calculate_area_demand(session,i,cur_direct_peak,extra_line,extra_line_length,max_line_count,vehicle_type):

    # Query length and width for current VehicleType 
    x = session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() #length 
    z = session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() #width
    

    area = 0
    line_parking_slots = 0
    direct_parking_slots = 0
    simulation_with_extra_line = False

    # Check if a ExtraLine exists 
    # Calculate the area for Line-parking-spaces 
    # Determine the number of Line-parking-spaces 
    if i == max_line_count and extra_line:
        area += (i-1)*standard_block_length*(x*z)
        area += extra_line_length*(x*z)
        line_parking_slots = (i-1)*standard_block_length + extra_line_length
        simulation_with_extra_line = True 
    else:
        area += (i*standard_block_length)*(x*z)
        line_parking_slots = i*standard_block_length


    # Calculate the area of Direct-parking-spaces
    # Determine the number of Direct-parking-spaces
    if cur_direct_peak > 0:
        width = x*math.sin(math.radians(45))+z*math.sin(math.radians(45))
        length = x*math.sin(math.radians(45))+z*math.sin(math.radians(45)) + (cur_direct_peak-1) * z/math.cos(math.radians(45))

        area += width*length  
        direct_parking_slots = cur_direct_peak  
    elif cur_direct_peak==0:
        area += 0
        cur_direct_peak = 0
    
    return round(area,2), line_parking_slots, direct_parking_slots, simulation_with_extra_line