from dataclasses import dataclass
from math import ceil
from typing import Dict
import math
from datetime import timedelta

import sqlalchemy.orm
import math
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
import eflips.depot.api
from sqlalchemy import func


@dataclass
class DrivewayAndSpacing:
    """
    Driveway and spacing information.

    All numbers are in meters

    TODO: Verify that these are the values in VDV 822
    """

    side_by_side: float = 1.0
    """Distance between two buses that are side by side."""
    front_to_back: float = 0.5
    """Distance between the front of one bus and the back of the next bus."""

    # TODO: Validate the assumption that direct areas MUST be accessed without reversing

    #        TOP
    #          /
    #         /
    # LEFT   / /   RIGHT
    #         /
    #        /
    #     BOTTOM
    # Note: Buses enter and exit driving downwards

    direct_area_top = 10
    """The total driving space required from a bus to enter the topmost parking spot."""
    direct_area_bottom = direct_area_top
    """The total driving space required from a bus to exit the bottommost parking spot."""
    direct_area_left = 10
    """The total driving space required from a bus to exit onto the left."""
    direct_area_right = direct_area_left
    """The total driving space required from a bus to enter from the right."""

    #     TOP
    #       | |
    # LEFT  | | RIGHT
    #       | |
    #       | |
    #     BOTTOM
    # Note: Buses enter at the top, exit at the bottom

    line_area_top = 12
    """The total driving space required from a bus to enter the topmost parking spot."""
    line_area_bottom = line_area_top
    """The total driving space required from a bus to exit the bottommost parking spot."""
    line_area_left = side_by_side
    """The space needed to be kept free left of a line of buses."""
    line_area_right = line_area_left
    """The space needed to be kept free right of a line of buses."""


@dataclass
class CapacityEstimate:
    """This class is the capacity estimate for the number of areas needed for a given vehicle type at a given depot."""

    line_peak_util: int
    """
    The peak utilization of the line area.

    This is the maximum number of buses that will be parked in the line area.
    """

    line_length: int
    """
    The length of the line area.

    This is the number of buses that will be parked in the line area behind each other.
    """

    direct_count: int
    """
    The number of parking spots in direct areas needed.

    This is the same as the peak utilization of the direct area.
    """

    area_square_meters: float | None = None
    """
    The area needed in square meters.

    Is calculated by `capacity_estimation`. If you are manually creating this
    object, you can set it to `None`.
    """

    @property
    def line_count(self) -> int:
        """
        The count of rows of line areas needed.

        This is calculated by dividing the total number of buses by the `line_length`.
        """
        return ceil(self.line_peak_util / self.line_length)


def area_square_meters(
    area: Area,
    spacing_params: DrivewayAndSpacing = DrivewayAndSpacing(),
    standard_block_length: int = 6
) -> float:
    """
    For a given `Area` object, calculate the actual area needed in square meters.
    Provided that the given `Area` needs driving lanes.

    :param area: An `Area` object. Vehicle length and width will be taken from `Area.vehicle_type`. The area
    type and size will be taken directly from the `Area`. Note that `AreaType.DIRECT_TWOSIDE` is not supported.
    :return: The area required in square meters.
    """

    vehicle_length = area.vehicle_type.length 
    vehicle_width = area.vehicle_type.width 
    area_capacity = area.capacity 

    front_to_back = spacing_params.front_to_back
    side_by_side = spacing_params.side_by_side

    direct_area_left = spacing_params.direct_area_left
    direct_area_right = direct_area_left

    line_area_top = spacing_params.line_area_top
    line_area_bottom = line_area_top


    if area.area_type is AreaType.LINE:
        amount_of_lines = area_capacity/standard_block_length

        #The Area-Length including driving-lanes for entering and exiting.
        parking_area_length = standard_block_length*vehicle_length + (standard_block_length-1)*front_to_back + line_area_top + line_area_bottom

        #The Area-Width inculding a spacing of `side_by_side` for the two outermost rows.
        parking_area_width = amount_of_lines*vehicle_width + (amount_of_lines+1)*side_by_side 

        parking_area = parking_area_length*parking_area_width

    elif area.area_type is AreaType.DIRECT:
        #The Area-Width including driving-lanes to the left and to the right for entering and exiting.
        parking_area_width = vehicle_length*math.sin(math.radians(45))+vehicle_width*math.sin(math.radians(45)) + direct_area_left + direct_area_right 
        #The Area-Length including a spacing of `side_by_side` between each parkingslot.
        parking_area_length = vehicle_length*math.sin(math.radians(45))+vehicle_width*math.sin(math.radians(45)) + (area_capacity-1) * vehicle_width/math.cos(math.radians(45))+ (area_capacity-1)*side_by_side*math.sin(math.radians(45))

        parking_area = parking_area_length*parking_area_width

    else:
        raise NotImplementedError("This AreaType is not supported.")


    return parking_area


def capacity_estimation(
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    spacing_params: DrivewayAndSpacing = DrivewayAndSpacing(),
    standard_block_length: int = 6
) -> Dict[Depot, Dict[VehicleType, CapacityEstimate]]:
    """
    Find the capacity estimates for all depots in the scenario.

    This is done be TODO DANIAL SUMMARIZE HOW.

    :param scenario: A `Scenario` object. It must have the `Depot` objects for each depot and DRIVING `Event`s. So
                     the consumption simulation should have been run before calling this function.
    :param session:  An open database session.
    :param spacing_params: A `DrivewayAndSpacing` object that contains the driveway and spacing information. Default
                           values are taken from the VDV 822 standard.
    :return: A nested dictionary with the depots as the first key, the vehicle types as the second key, and the
             `CapacityEstimate` object as the value.
    """
    
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



    # Function to query the necessary processes for the simulation
    def necessary_processes_query(session, scenario, depot):
        plan = session.query(Plan).filter(Plan.scenario_id == scenario.id, Plan.id == depot.default_plan_id).first()
        clean = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Clean").one()
        charging = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Charging").one()
        standby_departure = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Standby Departure").one()
        return plan, clean, charging, standby_departure



    # Function to link the Assocs before each simulation
    def associate_plan_with_processes(session,scenario,plan,clean,charging,standby_departure):

        assocs = [
            AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
            AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=2),
            AssocPlanProcess(scenario=scenario, process=standby_departure, plan=plan, ordinal=3),
        ]
        session.add_all(assocs) 


    # Function to determine the Direct peak usages of the different Charging Areas for the different Vehicle Types
    def give_back_peak_usage_direct_for_multiple_types(session, charging_areas, scenario):
        """
        Determine the peak number of concurrent charging operations (peak usage) for each charging area
        across different vehicle types in a given scenario.
        This function uses a sweep-line algorithm to calculate the maximum number of simultaneous charging events.
        It retrieves all events of type CHARGING_DEPOT for each charging area, sorts the start and end times,
        and then iterates through these time points to count concurrent events. In addition, it queries the total 
        number of vehicles associated with the charging areas vehicle type.
        """
        result_by_area = {}

        # Ensure the charging_areas variable is a list so we can iterate uniformly.
        if not isinstance(charging_areas, list):
            charging_areas = [charging_areas]
            
        # Process each charging area individually.
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
                # Sort the charging events by their start time to ensure proper ordering.
                events_sorted_by_time = sorted(charging_events, key=lambda e: e.time_start)

                # Initialization for peak usage calculation
                current_count = 0
                cur_direct_peak = 0
                time_points = []

                # Build a list of time points indicating the start and end of each event.
                for event in events_sorted_by_time:
                    time_points.append((event.time_start, 'start'))
                    time_points.append((event.time_end, 'end'))

                # Sort the time points
                time_points.sort()

                # Iterate over all time points to calculate the maximum number of simultaneous charging events.
                for time, point_type in time_points:
                    if point_type == 'start':
                        current_count += 1
                        cur_direct_peak = max(cur_direct_peak, current_count)
                    elif point_type == 'end':
                        current_count -= 1

            # Query the number of vehicles in the scenario for the given vehicle type associated with the charging area.
            vehicle_count_by_type = session.query(func.count(Vehicle.id)).filter(
                Vehicle.vehicle_type_id == charging_area.vehicle_type_id,
                Vehicle.scenario_id == scenario.id
            ).scalar()

            # Get the vehicle type for the current charging area
            vehicle_type = session.query(VehicleType).filter(
                VehicleType.id == charging_area.vehicle_type_id
            ).first()

            # Store the results for this charging area in the output dictionary.
            result_by_area[charging_area.name] = {
                'peak_usage': cur_direct_peak,
                'vehicle_count': vehicle_count_by_type,
                'vehicle_type': vehicle_type
            }

        return result_by_area

    # Function to determine the required rows of line-parking-spaces for current VehicleType 
    def calc_num_of_line_parking_spaces(session,peak_count,vehicle_type,standard_block_length):
        # Query length and width for current VehicleType 
        x = session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() #length 
        z = session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() #width
        

        if x is not None and z is not None:

            # Area calculated for the Direct-Area divided by the are of Line-parking-spaces = maximum number of Line-parking-spaces   
            width = x*math.sin(math.radians(45))+z*math.sin(math.radians(45))
            length = x*math.sin(math.radians(45))+z*math.sin(math.radians(45)) + (peak_count-1) * z/math.cos(math.radians(45)) 
            max_line_buses = math.floor((width*length)/(x*z))

            # For given row length 
            # How many rows for the amount of Line-parking-spaces 
            max_row_count = int(max_line_buses/standard_block_length)

            # Is an additional Line-row needed? 
            extra_line_length = 0
            # ChargingArea from AreaType Line with capacity: 1 not possible 
            if max_line_buses % standard_block_length not in (1, 0):
                max_row_count += 1
                extra_line_length = max_line_buses%standard_block_length
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
    def calculate_area_demand(session,i,cur_direct_peak,extra_line,extra_line_length,max_line_count,vehicle_type,standard_block_length):

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
    

    # ----------------------------------------------------------
    # Simulations
    # ----------------------------------------------------------

    
    def first_simulation_run(session,scenario,depot):
        """
        First simulation run with Direct parking spaces only
        Result: Number of Direct parking spaces, for each VehicleType, with which a simulation is possible.
        """

        # Query of all existing VehicleTypes in the Scanario
        # If no VehicleTypes are found --> abort 
        vehicle_types = session.query(VehicleType).filter(VehicleType.scenario_id == scenario.id).all()
        if not vehicle_types:
            raise ValueError("In dem aktuellen Scenario befinden sich keine VehicleType Objekte.")
        
        for vehicle_type in vehicle_types:
            print(f"Für Depot {depot.id} wurde der Fahrzeugtyp {vehicle_type.name} (ID {vehicle_type.id}) wurde gefunden.") 

        # Query of the objects required for the simulation
        necessary_processes = necessary_processes_query(session,scenario,depot)
        if any(value is None for value in necessary_processes):
            print("Plan oder einer der Prozesse konnte nicht aus dem Scenario abgefragt werden")
            return None 
        else:
            plan,clean,charging,standby_departure = necessary_processes

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
        associate_plan_with_processes(session,scenario,plan,clean,charging,standby_departure)
        
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


    def simulations_loop(result_by_area,session,scenario,depot,standard_block_length):
        """
        This function runs the depot simulation in a loop, where a block parking line is added in each iteration.
        In the end, the parking configuration with the smallest area is chosen for each VehicleType.
        """

        # Check whether the provided results are not faulty
        if not result_by_area:
            print("Die übergebenen Ergebnisse sind fehlerhalft.")
            return None
        
        # Query of the processes required for the simulation
        plan,clean,charging,standby_departure = necessary_processes_query(session,scenario,depot)

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
            num_of_line_parking_spaces = calc_num_of_line_parking_spaces(session,peak_count,vehicle_type,standard_block_length)
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
                            charging_area_buffer = create_charging_area(session,scenario,depot,name,AreaType.DIRECT_ONESIDE,vehicle_count+5,other_vehicle_type,processes)
                            
                                    
                    
                    # Call the function to connect processes
                    associate_plan_with_processes(session,scenario,plan,clean,charging,standby_departure)
                    
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
                #print(cur_direct_peak)

                # Determine the area requirement in square meters for the current configuration
                area,line_parking_slots,direct_parking_slots,simulation_with_extra_line = calculate_area_demand(session,i,cur_direct_peak,extra_line,extra_line_length,max_line_count,vehicle_type,standard_block_length)


                
                # Store the results of this iteration for the selected VehicleType
                zeile = {
                    "VehicleType": vehicle_type,
                    "Area": area,
                    "Line Parking Slots": line_parking_slots,
                    "Given Line Length": standard_block_length,
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
    
    

    # ----------------------------------------------------------
    # Function Calls
    # ----------------------------------------------------------

    depots = session.query(Depot).filter(Depot.scenario_id == scenario.id).all()
    capacity_estimates: Dict[Depot, Dict[VehicleType, CapacityEstimate]] = {}

    for depot in depots:
        try:
            result_by_area = first_simulation_run(session, scenario, depot)
        #If no VehicleTypes exist in the current scenario, abort the entire process.
        except ValueError as e:
            if str(e) == "In dem aktuellen Scenario befinden sich keine VehicleType Objekte.":
                print(f"Abbruch: {e}")
                return None
            else:
                continue        
        
        if result_by_area is None:
            continue

        total_results = simulations_loop(result_by_area, session, scenario,depot, standard_block_length)
        if total_results is None:
            continue

        #optimal_simulation(total_results, session, scenario,depot)

        depot_estimates: Dict[VehicleType, CapacityEstimate] = {}
        for key, result in total_results.items():
            vehicle_type = result["VehicleType"]
            estimate = CapacityEstimate(
                line_peak_util=result["Line Parking Slots"],
                line_length=result["Given Line Length"],
                direct_count=result["Direct Parking Slots"],
                area_square_meters=result["Area"]
            )
            depot_estimates[vehicle_type] = estimate
        capacity_estimates[depot] = depot_estimates


    return capacity_estimates


def update_depot_capacities(
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    capacity_estimates: Dict[Depot, Dict[VehicleType, CapacityEstimate]],
):
    """
    Update the depots in the database from a dictionary of capacity estimates, as returned by `capacity_estimation`.

    :param scenario: A `Scenario` object. It must have `Depot` objects for each depot in the keys of `capacity_estimates`.
    :param session: An open database session.
    :param capacity_estimates: A nested dictionary with the depots as the first key, the vehicle types as the second key, and the
                               `CapacityEstimate` object as the value.
    :return:
    """
    charging = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Charging").one()
    standby_departure = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Standby Departure").one()

    # Iterate over each depot in the dictionary
    for depot, vehicle_estimates in capacity_estimates.items():
        # Optional: Prüfen, ob das Depot zum übergebenen Scenario gehört
        if depot.scenario_id != scenario.id:
            continue

        # Für jeden VehicleType im aktuellen Depot
        for vehicle_type, cap_est in vehicle_estimates.items():
            # Berechne die Anzahl der anzulegenden LINE Areas
            num_line_areas = cap_est.line_count  # line_count = ceil(line_peak_util / line_length)
            for i in range(num_line_areas):
                line_area = Area(
                    scenario_id=scenario.id,
                    depot_id=depot.id,
                    vehicle_type_id=vehicle_type.id,
                    area_type= AreaType.LINE,  
                    capacity=cap_est.line_length,
                    name=f"Line Area {i+1} for {vehicle_type.name} at {depot.name}",
                    name_short=f"LINE-{vehicle_type.name[:5]}-{i+1}"
                )
                session.add(line_area)
                line_area.processes.append(charging)
                line_area.processes.append(standby_departure)

            # Erstelle die DIRECT Area für den aktuellen VehicleType
            if cap_est.direct_count > 0:
                direct_area = Area(
                    scenario_id=scenario.id,
                    depot_id=depot.id,
                    vehicle_type_id=vehicle_type.id,
                    area_type= AreaType.DIRECT_ONESIDE, 
                    capacity=cap_est.direct_count,
                    name=f"Direct Area for {vehicle_type.name} at {depot.name}",
                    name_short=f"DIRECT-{vehicle_type.name[:5]}"
                )
                session.add(direct_area)
                direct_area.processes.append(charging)
                direct_area.processes.append(standby_departure)
        

    session.commit()
    