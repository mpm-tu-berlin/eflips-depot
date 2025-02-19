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

# anpassbare Variablen: 
standard_block_length = 6 


def anzahl_an_line_parkplaetzen(session,peak_count,vehicle_type):
    # Länge und Breite für VehicleType abfragen 
    x = session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() #length 
    z = session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() #width
    

    if x is not None and z is not None:

        #Flächenberechnung für die Direct-Area geteilt durch die Fläche für einen Block-Parkplatz = maximale Anzahl der Block-Parkplätze     
        breite = x*math.sin(math.radians(45))+z*math.sin(math.radians(45))
        laenge = x*math.sin(math.radians(45))+z*math.sin(math.radians(45)) + (peak_count-1) * z/math.cos(math.radians(45)) 
        max_block_busse = math.floor((breite*laenge)/(x*z))

        #Wie viele Reihen sind mit den Bussen in Blockabstellung möglich?
        max_line_count = int(max_block_busse/standard_block_length)

        #Wird eine zusätzliche Blockreihe benötigt?
        extra_line_length = 0
        if max_block_busse % standard_block_length not in (1, 0):
            max_line_count += 1
            extra_line_length = max_block_busse%standard_block_length
            extra_line = True
            print(f"Es wird {max_line_count} Iterationen geben. Davon ist eine, eine Extra-Line mit der Cpacity von {extra_line_length} Parkplätzen")
        else:
            extra_line = False
            max_line_count = max_line_count

            print(f"Es wird {max_line_count} Iterationen geben")

        return max_line_count,extra_line,extra_line_length
    else:
        print(f"Keine Länge oder Breite für VehicleType{vehicle_type} gefunden")
        return None


# Funktion zur Abfrage von Depot,Plan und Prozessen aus dem Scenario 
def abfrage_aus_scenario(session,scenario):
    depot = session.query(Depot).filter(Depot.scenario_id == scenario.id).first()
    plan = session.query(Plan).filter(Plan.scenario_id == scenario.id).first()

    clean = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Clean").one()
    charging = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Charging").one()
    standby_departure = session.query(Process).filter(Process.scenario_id == scenario.id, Process.name == "Standby Departure").one()

    return depot,plan,clean,charging,standby_departure



# Funktion zur Verknüpfung der Assoc vor jeder Simulation 
def create_depot_areas_and_processes(session,scenario,plan,clean,charging,standby_departure):

    assocs = [
        AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
        AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=2),
        AssocPlanProcess(scenario=scenario, process=standby_departure, plan=plan, ordinal=3),
    ]
    session.add_all(assocs) 


# Funktion zur Bestimmung der Direct-Peak-Usages der Verschiedenen Ladezonen für die verschieden Vehicle-Types
def give_back_peak_usage_direct_for_multiple_types(session, charging_areas, scenario):
    result_by_area = {}

    for charging_area in charging_areas:
        # Step 1: Lade alle relevanten Events für die aktuelle Ladezone
        charging_events = session.query(Event).filter(
            Event.scenario_id == charging_area.scenario_id,
            Event.area_id == charging_area.id,
            Event.event_type == EventType.CHARGING_DEPOT
        ).all()

        # Fallback, falls keine Lade-Events gefunden wurden
        if not charging_events:
            print(f"Keine Lade-Events gefunden für {charging_area.name}.")
            cur_direct_peak = 0
        else:
            # Sortiere die Events nach Startzeit
            events_sorted_by_time = sorted(charging_events, key=lambda e: e.time_start)

            # Initialisierung für die Berechnung des Peak Usage
            current_count = 0
            cur_direct_peak = 0
            time_points = []

            # Wir sammeln alle Start- und Endpunkte in einer Liste
            for event in events_sorted_by_time:
                time_points.append((event.time_start, 'start'))
                time_points.append((event.time_end, 'end'))

            # Sortiere die Zeitpunkte
            time_points.sort()

            # Iteriere durch alle Zeitpunkte und berechne die gleichzeitigen Ladevorgänge
            for time, point_type in time_points:
                if point_type == 'start':
                    current_count += 1
                    cur_direct_peak = max(cur_direct_peak, current_count)
                elif point_type == 'end':
                    current_count -= 1

        # Fahrzeuganzahl für den aktuellen Fahrzeugtyp in der Ladezone
        vehicle_count_by_type = session.query(func.count(Vehicle.id)).filter(
            Vehicle.vehicle_type_id == charging_area.vehicle_type_id,
            Vehicle.scenario_id == scenario.id
        ).scalar()

        # Fahrzeugtyp für die aktuelle Ladezone holen
        vehicle_type = session.query(VehicleType).filter(
            VehicleType.id == charging_area.vehicle_type_id
        ).first()

        # Speichern der Peak Usage, Fahrzeuganzahl und Fahrzeugtyp in der Result-Dictionary
        result_by_area[charging_area.name] = {
            'peak_usage': cur_direct_peak,
            'vehicle_count': vehicle_count_by_type,
            'vehicle_type': vehicle_type
        }

    return result_by_area




# Funktion zur Bestimmung des Peak-Usage der Direct-Area für eine Charging-Area 
def give_back_peak_usage_direct(session, scenario, charging_area):
    # Lade alle relevanten Events für die aktuelle Ladezone
    charging_events = session.query(Event).filter(
        Event.scenario_id == scenario.id,
        Event.area_id == charging_area.id,
        Event.event_type == EventType.CHARGING_DEPOT
    ).all()

    # Falls keine Charging-Events gefunden wurden
    if not charging_events:
        print("Keine Lade-Events gefunden.")
        return 0

    # Initialisierung für die Berechnung des Peak Usage
    current_count = 0
    cur_direct_peak = 0
    time_points = []

    # Wir sammeln alle Start- und Endpunkte in einer Liste
    for event in charging_events:
        time_points.append((event.time_start, 'start'))
        time_points.append((event.time_end, 'end'))

    # Sortiere die Zeitpunkte
    time_points.sort()

    # Iteriere durch alle Zeitpunkte und berechne die gleichzeitigen Ladevorgänge
    for time, point_type in time_points:
        if point_type == 'start':
            current_count += 1
            cur_direct_peak = max(cur_direct_peak, current_count)
        elif point_type == 'end':
            current_count -= 1

    return cur_direct_peak



# Funktion zur Bestimmung der benötigten Fläche für die Iteration i
def flaechen_bedarf(session,i,cur_direct_peak,extra_line,extra_line_length,max_line_count,vehicle_type):
    # Länge und Breite für VehicleType abfragen 
    x = session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() #length 
    z = session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() #width
    

    flaeche = 0
    block_parking_slots = 0
    direct_parking_slots = 0
    simulation_with_extra_line = False

    # Prüfen ob es eine ExtraLine gibt und Fläche berechenen für die Block-Parkplätze 
    # Anzahl der Block-Parkplätze 
    if i == max_line_count and extra_line:
        flaeche += (i-1)*standard_block_length*(x*z)
        flaeche += extra_line_length*(x*z)
        block_parking_slots = (i-1)*standard_block_length + extra_line_length
        simulation_with_extra_line = True 
    else:
        flaeche += (i*standard_block_length)*(x*z)
        block_parking_slots = i*standard_block_length

    # Fläche der Direct-Parkplätze berechenen 
    # Anzahl der Direct-Parkplätze     
    if cur_direct_peak > 0:
        breite = x*math.sin(math.radians(45))+z*math.sin(math.radians(45))
        laenge = x*math.sin(math.radians(45))+z*math.sin(math.radians(45)) + (cur_direct_peak-1) * z/math.cos(math.radians(45))

        flaeche += breite*laenge  
        direct_parking_slots = cur_direct_peak  
    elif cur_direct_peak==0:
        flaeche += 0
        cur_direct_peak = 0
    
    return round(flaeche,2), block_parking_slots,direct_parking_slots,simulation_with_extra_line




def erster_simulations_durchlauf(session,scenario):
    # Abfrage, aller existierenden VehicleTypes 
    vehicle_types = session.query(VehicleType).filter(VehicleType.scenario_id == scenario.id).all()

    if not vehicle_types:
        print("In dem aktuellen Scenario befinden sich keine VehicleType Objekte.")
        return None
    
    for vehicle_type in vehicle_types:
        print(f"Fahrzeugtyp {vehicle_type.name} (ID {vehicle_type.id}) wurde gefunden.") 

    
    rotations = session.query(Rotation).filter(Rotation.scenario_id == scenario.id).count()
    print(rotations)
    
    platzhalter =  abfrage_aus_scenario(session,scenario)
    if any(value is None for value in platzhalter):
        print("Depot,Plan oder einer der Prozesse konnte nicht aus dem Scenario abgefragt werden")
        return None 
    else:
        depot,plan,clean,charging,standby_departure = platzhalter

    charging_areas = []
    # Erstellen der entsprechenden Charging Areas für jeden gefundenen VehicleType
    # Ausschließlich Direct-Parkplätze 
    for vehicle_type in vehicle_types:
        charging_area = Area(
            scenario=scenario,
            name=f"Entenhausen Depot Charging Area - {vehicle_type.name}",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=rotations,  # Für ausreichende Kapazität 
            vehicle_type=vehicle_type,  
        )
        charging_areas.append(charging_area)
        session.add(charging_area)
        charging_area.processes.append(charging)
        charging_area.processes.append(standby_departure)

    # Erstellen von Assocs 
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

        # Überprüfungs-Tools:
        vehicle_type_counts = (
            session.query(Vehicle.vehicle_type_id, VehicleType.name, func.count(Vehicle.id))
            .join(VehicleType, Vehicle.vehicle_type_id == VehicleType.id)
            .filter(Vehicle.scenario_id == scenario.id)
            .group_by(Vehicle.vehicle_type_id, VehicleType.name)
            .all()
        )
        for vehicle_type_id, vehicle_type_name, count in vehicle_type_counts:
            print(f"Fahrzeugtyp {vehicle_type_name} (ID {vehicle_type_id}): {count} Fahrzeuge aktiv")  

    except AssertionError:
        print("Fehler: SoC ist geringer als Erwartet.")
        session.rollback()
        return None 
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten: {e}")
        session.rollback()
        return None


    # Peak-Usage von Direct-Parkplätzen für jede ChargingArea ermitteln:
    result_by_area = give_back_peak_usage_direct_for_multiple_types(session,charging_areas,scenario)
    session.rollback()
    
    for area_name, data in result_by_area.items():
        name = area_name
        peak_count = data['peak_usage']              # Zugriff auf 'peak_usage'
        vehicle_count_by_type = data['vehicle_count']  # Zugriff auf 'vehicle_count'
        vehicle_type = data['vehicle_type']           # Zugriff auf 'vehicle_type'

        print(f" Für {name}: Die Spitzenbelastung ist {peak_count} Fahrzeuge. Und es sind {vehicle_count_by_type} Fahrzeuge von diesem Typ aktiv. Der Vehicle_Type lautet {vehicle_type}")
    
    return result_by_area


def simulations_loop(result_by_area,session,scenario):
    if not result_by_area:
        print("Die übergebenen Ergebnisse sind fehlerhalft.")
        return None
    
    # Ergebnisse für alle Fahrzeugtypen
    ergebnisse_gesamt = {}

    
    depot,plan,clean,charging,standby_departure = abfrage_aus_scenario(session,scenario)

    for area_name, data in result_by_area.items():
        name = area_name
        peak_count = data['peak_usage']              # Zugriff auf 'peak_usage'
        vehicle_count_by_type = data['vehicle_count']  # Zugriff auf 'vehicle_count'
        vehicle_type = data['vehicle_type']           # Zugriff auf 'vehicle_type'

        print(f"Simulation für den Bus-Type{vehicle_type}")

        # Ergenisse für den aktuellen Fahrzeugtypen 
        ergebnisse = []

        # Berechnung wie viele Block-Parkplätze gerade noch kleiner sind, als die benötigten Direct-Parklätze für einen VehicleType 
        anzahl_block = anzahl_an_line_parkplaetzen(session,peak_count,vehicle_type)
        if anzahl_block is None:
            print("Keine Werte für Breite oder Länge in VehicleType Objekt gefunden")
            return None 
        else:
            max_line_count,extra_line,extra_line_length = anzahl_block


        # Schleife zur Ermittlung der minimalen Anzahl an Parkplätzen
        for i in range(1,max_line_count+1):  # Anzahl der möglichen Block-Parkplätzen 
            
            try:
                
                if i == max_line_count and extra_line:
                    charging_line_area_extra = Area(
                        scenario = scenario,
                        name = name,
                        depot = depot,
                        area_type = AreaType.LINE,
                        capacity = extra_line_length,               
                        vehicle_type = vehicle_type,
                    )
                    session.add(charging_line_area_extra)
                    charging_line_area_extra.processes.append(charging)
                    charging_line_area_extra.processes.append(standby_departure)

                    for b in range(i-1):
                        charging_line_area = Area(
                            scenario = scenario,
                            name = name,
                            depot = depot,
                            area_type = AreaType.LINE,
                            capacity = standard_block_length,               
                            vehicle_type = vehicle_type,
                        )
                        session.add(charging_line_area)
                        charging_line_area.processes.append(charging)
                        charging_line_area.processes.append(standby_departure)
                    
                else:
                    #Create Line Area with varibale Lines
                    for b in range(i):
                        charging_line_area = Area(
                            scenario = scenario,
                            name = name,
                            depot = depot,
                            area_type = AreaType.LINE,
                            capacity = standard_block_length,               
                            vehicle_type = vehicle_type,
                        )
                        session.add(charging_line_area)
                        charging_line_area.processes.append(charging)
                        charging_line_area.processes.append(standby_departure)

                # Create charging area: gesetze Direct-Kapazität
                charging_area = Area(
                    scenario=scenario,
                    name = name ,
                    depot=depot,
                    area_type=AreaType.DIRECT_ONESIDE,
                    capacity= peak_count,  # Aus der ersten Depotsimulation: fester Wert 
                    vehicle_type=vehicle_type,
                )
                session.add(charging_area)
                charging_area.processes.append(charging)
                charging_area.processes.append(standby_departure)

                # Puffer Parkplätze für die anderen in der Session enthaltenen Fahrzeugtypen 
                for area_name_other, data in result_by_area.items():
                    other_vehicle_type = data['vehicle_type']
                    vehicle_count = data['vehicle_count']
                    if other_vehicle_type != vehicle_type:
                        # Parkfläche für Pufferzonen 
                        charging_area_buffer = Area(
                            scenario = scenario,
                            name = name,
                            depot = depot,
                            area_type = AreaType.LINE,
                            capacity = vehicle_count+5,               
                            vehicle_type = other_vehicle_type,
                        )
                        session.add(charging_area_buffer)
                        charging_area_buffer.processes.append(charging)
                        charging_area_buffer.processes.append(standby_departure)
                                

                
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


            # Vehicle count für aktuellen Vehicle-Type
            vehicle_count = session.query(Vehicle).filter(Vehicle.vehicle_type == vehicle_type).count()

            # Überprüfung ob ein Fahrzeugmehrbedarf entstanden ist 
            if vehicle_count > vehicle_count_by_type:
                print(f"Iteration:{i}  Für die Depotauslegung gab es einen Fahrzeugmehrbedarf. Es wurden insgesamt {vehicle_count} Fahrzeuge benötigt.")
                session.rollback()
                continue

            # Peak-Usage von Direct-Parkplätzen, für die Konfiguration mit i*block_length Block-Parkplätzen, ermitteln:
            cur_direct_peak = give_back_peak_usage_direct(session,scenario,charging_area)
            print(cur_direct_peak)
            
            # Flächenbedarf für aktuelle Konfiguration ermitteln
            flaeche,block_parking_slots,direct_parking_slots,simulation_with_extra_line =flaechen_bedarf(session,i,cur_direct_peak,extra_line,extra_line_length,max_line_count,vehicle_type)
            
            zeile = {
                "VehicleType": vehicle_type,
                "Fläche": flaeche,
                "Block Parkplätze": block_parking_slots,
                "Direct Parkplätze": direct_parking_slots,
                "Fahrzeug Anzahl": vehicle_count,
                "Simulation mit ExtraLine": simulation_with_extra_line,
                "ExtraLine Länge": extra_line_length,
                "Iteration":i
            }
            
            ergebnisse.append(zeile)
            
            session.rollback()
        
        if not ergebnisse:
            print(f"Keine Ergebnisse für {vehicle_type} gefunden")
            continue
        else:
            flaechen_min_depot_konfiguration = min(ergebnisse, key=lambda x: x["Fläche"])

        ergebnisse_gesamt[f"Fahrzeugtyp{vehicle_type}"] = flaechen_min_depot_konfiguration
        #ergebnisse_gesamt.append(flaechen_min_depot_konfiguration)

    return ergebnisse_gesamt


# Simulation und Speichern der besten Depot-Auslegung 
def optimale_simulation(ergebnisse_gesamt,session,scenario):
    if not ergebnisse_gesamt:
        print("Die übergebenen Ergebnisse sind fehlerhalft.")
        return

    depot,plan,clean,charging,standby_departure = abfrage_aus_scenario(session,scenario)
    
    

    for key, value in ergebnisse_gesamt.items():
        # Entpacken der Werte aus dem inneren Dictionary
        vehicle_type = value["VehicleType"]
        flaeche = value["Fläche"]
        block_parking_slots = value["Block Parkplätze"]
        direct_parking_slots = value["Direct Parkplätze"]
        vehicle_used = value["Fahrzeug Anzahl"]
        optimum_with_extra_line = value["Simulation mit ExtraLine"]
        extra_line_length = value["ExtraLine Länge"]
        iteration = value["Iteration"]

        if optimum_with_extra_line:
            charging_line_area_extra = Area(
                scenario = scenario,
                name = "Entenhausen Depot Area",
                depot = depot,
                area_type = AreaType.LINE,
                capacity = extra_line_length,     # Falls eine extra Line vorhanden          
                vehicle_type = vehicle_type,
            )
            session.add(charging_line_area_extra)
            charging_line_area_extra.processes.append(charging)
            charging_line_area_extra.processes.append(standby_departure)
            
            for b in range(iteration-1):
                charging_line_area = Area(
                    scenario = scenario,
                    name = "Entenhausen Depot Area",
                    depot = depot,
                    area_type = AreaType.LINE,
                    capacity = standard_block_length,               
                    vehicle_type = vehicle_type,
                )
                session.add(charging_line_area)
                charging_line_area.processes.append(charging)
                charging_line_area.processes.append(standby_departure)
        else:
            for b in range(iteration):
                charging_line_area = Area(
                    scenario = scenario,
                    name = "Entenhausen Depot Area",
                    depot = depot,
                    area_type = AreaType.LINE,
                    capacity = standard_block_length,               
                    vehicle_type = vehicle_type,
                )
                session.add(charging_line_area)
                charging_line_area.processes.append(charging)
                charging_line_area.processes.append(standby_departure)

        if direct_parking_slots > 0:    
            charging_area = Area(
                scenario=scenario,
                name="Entenhausen Depot Area",
                depot=depot,
                area_type=AreaType.DIRECT_ONESIDE,
                capacity=direct_parking_slots,  # Aus opimaler-Depot-Kofiguration 
                vehicle_type=vehicle_type,
            )
            session.add(charging_area)
            charging_area.processes.append(charging)
            charging_area.processes.append(standby_departure)

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



"""
Ab hier beginnt der Bin-Packing-Alogrithmus nach der Vorlage aus Patrik Mundts Arbeit.
"""

# Schrittgröße, um wie viele Meter sich die Seitenlängen der Parkfläche pro Schleifendurchlauf verringern.
reduction_step = 5
# Breite der Fahrwege, die am inneren Rand der Parkfläche liegen (Aus Mundts Arbeit: 8m)  
innerer_rand = 8 

def best_possible_packing(session, ergebnisse_gesamt,breite_behaelter=None, hoehe_behaelter=None):
    """
    Dreistufiger Ansatz:
    1) Reduziert Breite und Höhe simultan.
    2) Nur Breite weiter reduzieren.
    3) Nur Höhe weiter reduzieren.
    """

    # 1) Erster Aufruf von bin_packing
    if breite_behaelter is None and hoehe_behaelter is None:
        result = bin_packing(session, ergebnisse_gesamt)
    else:
        result = bin_packing(session, ergebnisse_gesamt, breite_behaelter, hoehe_behaelter)

    if not isinstance(result, tuple):
        print("Warnung: bin_packing hat keine gültigen Rückgabewerte geliefert!")
        return None, None, None, None, None


    # Entpacken
    platzierte_stellflaechen, fahrwege, box_width, box_length, available_spaces = result

    # Merken wir uns die Startwerte
    beginning_width = box_width
    beginning_length = box_length

    # ----------------------------------------------------------
    # 1) Simultane Reduktion von Breite + Höhe
    # ----------------------------------------------------------

    # Lokales "last success"
    last_success = result

    counter_sim = 0
    

    while True:
        # Nächster Kandidat
        candidate_w = box_width - reduction_step
        candidate_h = box_length - reduction_step

        # Abbruchkriterium
        if candidate_w <= 0 or candidate_h <= 0:
            break

        # Aufruf mit kleineren Maßen
        candidate_result = bin_packing(session, ergebnisse_gesamt, candidate_w, candidate_h)

        if candidate_result is None or not isinstance(candidate_result, tuple):
            # Fehlschlag -> wir bleiben bei (box_width, box_length)
            # und brechen ab
            break
        else:
            # Erfolg: übernehmen wir candidate_w, candidate_h
            box_width  = candidate_w
            box_length = candidate_h
            last_success = candidate_result
            counter_sim += 1

    # Hier enden wir mit der letzten funktionierenden (box_width, box_length)
    # => "simultan" reduziert.

    # ----------------------------------------------------------
    # 2) Nur die Breite weiter reduzieren
    # ----------------------------------------------------------
    # Wir starten von der "simultanen" Endlösung
    # (box_width, box_length) - das war gerade "last_success"
    # extrahieren wir nochmal
    platzierte_stellflaechen, fahrwege, box_width, box_length, _a = last_success

    counter_w = 0

    while True:
        candidate_w = box_width - reduction_step
        if candidate_w <= 0:
            break

        candidate_result = bin_packing(session, ergebnisse_gesamt, candidate_w, box_length)
        if candidate_result is None or not isinstance(candidate_result, tuple):
            # Fehlschlag
            break
        else:
            # Erfolg
            box_width = candidate_w
            last_success = candidate_result
            counter_w += 1

    # ----------------------------------------------------------
    # 3) Nur die Höhe weiter reduzieren
    # ----------------------------------------------------------
    # Starten von der "nur Breite" Endlösung
    platzierte_stellflaechen, fahrwege, box_width, box_length, _a = last_success

    counter_h = 0

    while True:
        candidate_h = box_length - reduction_step
        if candidate_h <= 0:
            break

        candidate_result = bin_packing(session, ergebnisse_gesamt, box_width, candidate_h)
        if candidate_result is None or not isinstance(candidate_result, tuple):
            # Fehlschlag
            break
        else:
            box_length = candidate_h
            last_success = candidate_result
            counter_h += 1

    # ----------------------------------------------------------
    # letztes Erfolgsergebnis "last_success" entpacken und ausgeben
    # ----------------------------------------------------------
    platzierte_stellflaechen,fahrwege,final_width,final_length,available_spaces = last_success

    print("Ergebnis:")
    print(f"Die Parkfläche wurde {counter_sim} Mal simultan um {reduction_step}x{reduction_step} reduziert")
    print(f"Anschließend wurde die Breite um weitere {counter_w} Mal um {reduction_step} reduziert")
    print(f"Abschließend wurde die Höhe um weitere {counter_h} Mal um {reduction_step} reduziert")
    print(f"Ursprüngliche Breite x Länge: {beginning_width} x {beginning_length}")
    print(f"Endgültige Breite x Länge   : {final_width} x {final_length}")
    print(f"Parkfläche: {final_width * final_length} Quadratmeter")


    #platzierte_stellflaechen,fahrwege,final_width,final_length,available_spaces = bin_packing(session,ergebnisse_gesamt,breite_behaelter,hoehe_behaelter)

    return platzierte_stellflaechen, fahrwege, final_width, final_length, available_spaces




def bin_packing(session,ergebnisse_gesamt,breite_behaelter=None,hoehe_behaelter=None):
    # Liste der zu platzierenden Parkflächen 
    liste = []

    # Berechnen der Flächeninhalte aller Stellplätze 
    fleache_ohne_fahrwege = 0
    for key, value in ergebnisse_gesamt.items():
        fleache = value["Fläche"]
        fleache_ohne_fahrwege += fleache
    
    # Vorprüfung nach Mundt:
    # prüfen, ob Flächeninhalt des Behälters ausreicht für Flächen der Stellplätze (exklusive der Fahrwege)
    if breite_behaelter is not None and hoehe_behaelter is not None:
        behaelter_flaeche = breite_behaelter*hoehe_behaelter
        if behaelter_flaeche < fleache_ohne_fahrwege:
            print(f"Fläche der Stellplätze:{fleache_ohne_fahrwege}\nFläche der Parkfläche:{behaelter_flaeche}\nDie Übergebene Parkfläche reicht nicht vom Flächeninhalt, um alle Stellplätze zu plazieren.")
            return None 

    # Falls keine Depotfläche vorgegeben ist, wird hier eine quadratische Fläche erzeugt 
    if breite_behaelter is None and hoehe_behaelter is None:     
        breite_behaelter = math.ceil(math.sqrt(fleache_ohne_fahrwege))+200
        hoehe_behaelter =  math.ceil(math.sqrt(fleache_ohne_fahrwege))+200
        print("Es wurde eine ausreichend große und quadratische Parkfläche erzeugt")

    # Erzeugung der zu platzierenden Rechtecke/Stellflächen
    for key, value in ergebnisse_gesamt.items():
        vehicle_type = value["VehicleType"]
        block_parking_slots = value["Block Parkplätze"]
        direct_parking_slots = value["Direct Parkplätze"]
        
        # Prüfen, ob eine Extra-Line existiert 
        extra_line_length = block_parking_slots%standard_block_length
        block_lines = block_parking_slots//standard_block_length
        if extra_line_length > 0:
            block_lines += 1
        
        # Länge und Breite des VehicleTypes abfragen und speichern 
        x = session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() #länge
        z = session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() #breite 

        # Höhe und Breite der Stellfläche für die Blockstellplätze des VehicleTypes
        block_hohe = standard_block_length*x
        block_breite = block_lines*z
        flaeche_block = (vehicle_type, block_hohe*block_breite)
        is_block = True 
        block_stellplaetze = (vehicle_type,block_breite,block_hohe,is_block)

        # Höhe und Breite der Parkfläche für die Directparkplätze des VehicleTypes
        direct_stellplaetze = None 
        if direct_parking_slots > 0:
            breite = x*math.sin(math.radians(45))+z*math.sin(math.radians(45))
            laenge = x*math.sin(math.radians(45))+z*math.sin(math.radians(45)) + (direct_parking_slots-1) * z/math.cos(math.radians(45))

            direct_flaeche = (vehicle_type,breite*laenge)
            direct_stellplaetze = (vehicle_type,breite,laenge, not is_block)
        
        # Wenn Direct-Parkplätze existieren, werden diese zur Liste der zu platzierenden Stellplätze hinzugefügt 
        if direct_stellplaetze: 
            liste.extend([block_stellplaetze,direct_stellplaetze])
        else:
            liste.append(block_stellplaetze)
    
    # Nach der Best-Fit-Decreasing-Heuristik von Mundt
    # decreasing sortieren der Stellflächen, primär nach x und sekundär nach y 
    #stellplaetze_sortiert = sorted(liste, key = lambda r: (-r[1],-r[2])) 

    # Eigene neue Sortierung:
    # unterscheidet zwischen der Sortierung der dsr-Stellflächen und der block-Stellflächen
    # dsr: nach Mundts-Heuristik primär nach x und sekundär nach y
    # block: primär nach y und sekundär nach x
    # DSR-Stellplätze vor den Block-Stellplätzen
    stellplaetze_sortiert = sorted(liste, key = lambda r: (0, -r[1], -r[2]) if not r[3] else (1, -r[2], -r[1]))



    # zweite Vorprüfung nach Mundt:
    # prüfen, ob eine der Seitenlängen der Stellplätze größer als die Seitenlängen der Parkfläche ist.
    max_breite = max(width[1] for width in stellplaetze_sortiert)
    max_hoehe = max(length[2] for length in stellplaetze_sortiert)
    if max_breite > breite_behaelter:
        print("Die Breite einer Stellfläche übersteigt die Breite der Parkfläche")
        return None 
    if max_hoehe > hoehe_behaelter:
        print(f"Die Höhe einer Stellfläche übersteigt die Höhe der Parkfläche\nDie maximale Höhe einer Stellfläche beträgt{max_hoehe}\nDie Höhe der Parkfläche beträgt{hoehe_behaelter}")
        return None 


    # Verfügbarer Platz im Behälter unter Berücksichtigung der Fahrbahn am inneren Rand der Parkfläche
    available_spaces = [(innerer_rand,innerer_rand,breite_behaelter-2*innerer_rand,hoehe_behaelter-2*innerer_rand)] # (x,y,breite,hoehe)
    platzierte_stellflaechen = []
    fahrwege = []

    # Fahrweg am inneren Rand der Parkfläche  
    linker_rand = (0,0,innerer_rand,hoehe_behaelter)
    oberer_rand = (0,hoehe_behaelter- innerer_rand,breite_behaelter,innerer_rand)
    rechter_rand = (breite_behaelter-innerer_rand,0,innerer_rand,hoehe_behaelter)
    unterer_rand = (0,0,breite_behaelter,innerer_rand)
    
    fahrwege.extend([linker_rand,oberer_rand,rechter_rand,unterer_rand])

        
    # Platzierungsfunktion 
    # iterieren über alle Stellplätze in sortierter Reihenfolge 
    for stellplatz in stellplaetze_sortiert:
        # die Funktion sucht für den ausgewählten Stellplatz eine passende Parkfläche und platziert Stellflächen als auch die zugehörigen Fahrwege
        # die Listen 'available_spaces', 'platzierte_stellflaechen' und 'fahrwege' werden bei jedem Aufruf aktualisiert
        # falls ein Stellplatz nicht platziert werden kann bricht die gesamte Funktion ab
        # eine nicht platzierte Stellfläche ist ein Abbruchkriterium
        placed = placing_vehicle_on_parking_space(stellplatz,available_spaces,fahrwege,platzierte_stellflaechen)
        
        if not placed:
            print(f"{stellplatz} konnte nicht platziert werden. Abbruch des 'Bin Packing'-Algorithmus")
            return None 


    fehlende_stellflaechen = [s for s in stellplaetze_sortiert if (s[1],s[2]) not in [(p[3],p[4]) for p in platzierte_stellflaechen]]
    if fehlende_stellflaechen:
        raise ValueError(f"Die folgenden Stellplätze konnten nicht platziert werden: {fehlende_stellflaechen}")
    
    return platzierte_stellflaechen, fahrwege, breite_behaelter, hoehe_behaelter, available_spaces



def placing_vehicle_on_parking_space(stellplatz, available_spaces,fahrwege,platzierte_stellflaechen):
    # 1) Entpacken 
    vehicle_type,breite_,hoehe_ , isblock = stellplatz

    # 2) iterieren über die verfügbaren Behälter(Parkflächen) in passender Sortierung
    for i, available in enumerate(available_spaces):
        x, y, free_w,free_h = available
                    
        # prüfen ob der Stellplatz in die ausgewählte Parkfläche passt.
        if free_h >= hoehe_  and free_w >= breite_ :
            best_fit_index = i
            parkflaeche = available_spaces[i]

            # wenn ein passender Behälter gefunden wurde, wird geprüft, ob ein Fahrweg von ausreichender Größe an ihm grenzt.
            # für eine 'dsr'- Stellfläche reicht ein Fahrweg von ausreichender Höhe links neben der Parkfläche 
            # für eine 'block'- Stellfläche muss sowohl ober- als auch unterhalb der Parkfläche ein Fahrweg liegen.
            # zusätzlich muss die Höhe der Stellfläche die der Parkfläche entsprechen, damit kein zusätzlicher Fahrweg notwendig ist. 
            drivinglane_necessary = fahrweg_pruefung(stellplatz, parkflaeche, fahrwege)
            if drivinglane_necessary['Fahrweg_links_anliegend'] == True or drivinglane_necessary['Block_oben_unten'] == True:
                # wenn ein ausreichend großer Fahrweg existiert, kann die Stellfläche platziert werden
                place_vehicle_and_update(stellplatz,available_spaces,best_fit_index,fahrwege,drivinglane_necessary,platzierte_stellflaechen)
            
                return True 
            
            # für den Fall, dass kein Fahrweg anliegt
            else: 
                
                if enough_space_for_vehicle_and_drivinglanes(stellplatz,parkflaeche,drivinglane_necessary):
                    # wenn die Parkfläche groß genug für die Stellfläche inklusive Fahrweg ist werden eben diese platziert.
                    place_vehicle_and_update(stellplatz,available_spaces,best_fit_index,fahrwege, drivinglane_necessary, platzierte_stellflaechen)

                    return True 
            
                else:
                    # die ausgewählte Parkfläche ist nicht groß genug um die Stellfläche inklusive des benötigten Fahrwegs zuplatzieren.
                    # --> nächste Parkfläche
                    continue  
        else:
            # Stellplatz ist zu groß für die ausgewählte Parkfläche
            # --> nächste Parkfläche 
            continue
    
    # keine Fläche hat gepasst
    return False  



def fahrweg_pruefung(stellplatz,parkflaeche,fahrwege):
    # Entpacken 
    x,y,free_w,free_h = parkflaeche
    vehicle_type,breite_stell,hoehe_stell, isblock = stellplatz

    # Flags
    fahrweg_links_anliegend = False
    fahrweg_unten_anliegend = False 
    fahrweg_oben_anliegend = False
    fahrweg_oben_anliegend_genutzt = False 
    fahrweg_oben_anliegend_nichtgenutzt = False 
    hoehe_stell_gleich_hoehe_park = False 

    rect_type = 'block' if isblock else 'dsr'

    # benötigte Fahrwegbreite ermitteln
    conflict_matrix = create_conflict_matrix()
    if not isblock:
        required_gap = conflict_matrix.get((rect_type, 'left'), 0)
    else:
        required_gap = conflict_matrix.get((rect_type, 'top'), 0) # vorausgesetzt, dass top- und bottom-Gap gleich groß sind.

    # Fallunterscheidung ob es sich um eine'dsr'-Stellfläche oder eine 'block'-Stellfläche
    # Erste Prüfung für 'dsr'-Stellflächen
    if not isblock:
        # Iteriere durch alle Fahrwege, um zu überprüfen, ob ein Fahrweg links anliegt
        for fahrweg in fahrwege:
            x_fahrweg, y_fahrweg, breite_fahrweg, hoehe_fahrweg = fahrweg
            
            # Prüfe, ob der Fahrweg direkt links an der Stellfläche anliegt und die Höhe ausreichend ist
            if (x_fahrweg + breite_fahrweg == x) and (y_fahrweg <= y) and (y_fahrweg + hoehe_fahrweg >= y + hoehe_stell):
                fahrweg_links_anliegend = True
                break
    
    # Zweite Prüfung für 'block'-Stellflächen 
    else:
        # Iteriere durch alle Fahrwege, um zu überprüfen, ob Fahrwege an der Stellfläche anliegen.
        for fahrweg in fahrwege:
            x_fahrweg, y_fahrweg, breite_fahrweg, hoehe_fahrweg = fahrweg
            
            # Prüfe, ob der Fahrweg direkt oben an der Parkfläche anliegt und die Breite ausreichend ist
            if (y_fahrweg == y + free_h) and (x_fahrweg <= x) and (x_fahrweg + breite_fahrweg >= x + breite_stell):
                fahrweg_oben_anliegend = True
            
            # Prüfe, ob ein Fahrweg unten an der Stellfläche/Parkfläche anliegt und die Breite ausreichend ist. 
            if (y_fahrweg + hoehe_fahrweg == y) and (x_fahrweg <= x) and (x_fahrweg + breite_fahrweg >= x + breite_stell):
                fahrweg_unten_anliegend = True 

            # Prüfen, ob die Höhe der Stellflächen der Höhe der Parkfläche entspricht.
            # wichtig für den Fall, wenn sowohl oberhalb als auch unterhalb der Stellfläche ein Fahrweg liegt.
            # es muss weder ober- noch unterhalb der Stellfläche ein Fahrweg hinzugefügt werden. 
            if fahrweg_oben_anliegend  and fahrweg_unten_anliegend:
                if math.isclose(y+ hoehe_stell, y + free_h, rel_tol = 1e-6):
                    hoehe_stell_gleich_hoehe_park = True
                    break
        
        # Für den Fall, dass nur oberhalb der Parkfläche ein Fahrweg anliegt.
        # Prüfen, ob dieser nach der Plazierung nach BLF genutzt wird oder nicht.
        if fahrweg_oben_anliegend and not fahrweg_unten_anliegend:
            if math.isclose(hoehe_stell + required_gap, free_h, rel_tol = 1e-6):
                fahrweg_oben_anliegend_genutzt = True 
            else:
                fahrweg_oben_anliegend_nichtgenutzt =  True 
        

    return {
        'Fahrweg_links_anliegend': fahrweg_links_anliegend,
        'Fahrweg_unten_anliegend': fahrweg_unten_anliegend,
        'Fahrweg_oben_anliegend' : fahrweg_oben_anliegend,
        'Fahrweg_oben_anliegend_genutzt': fahrweg_oben_anliegend_genutzt,
        'Fahrweg_oben_anliegend_nichtgenutzt': fahrweg_oben_anliegend_nichtgenutzt,
        'Block_oben_unten' : hoehe_stell_gleich_hoehe_park
    }

def place_vehicle_and_update(stellplatz,available_spaces,best_fit_index,fahrwege,drivinglane_necessary,platzierte_stellflaechen):
    # Entpacken
    vehicle_type, breite_stell, hoehe_stell, isblock = stellplatz
    parkflaeche = available_spaces[best_fit_index]
    x, y, free_w, free_h = parkflaeche

    rect_type = 'block' if isblock else 'dsr'

    fahrweg_links_anliegend = drivinglane_necessary['Fahrweg_links_anliegend']
    fahrweg_unten_anliegend = drivinglane_necessary['Fahrweg_unten_anliegend']
    fahrweg_oben_anliegend = drivinglane_necessary['Fahrweg_oben_anliegend']
    fahrweg_oben_anliegend_genutzt = drivinglane_necessary['Fahrweg_oben_anliegend_genutzt']
    fahrweg_oben_anliegend_nichtgenutzt = drivinglane_necessary['Fahrweg_oben_anliegend_nichtgenutzt']
    block_oben_unten = drivinglane_necessary['Block_oben_unten']

    # benötigte Fahrwegbreite ermitteln
    conflict_matrix = create_conflict_matrix()
    if not isblock:
        required_gap = conflict_matrix.get(('dsr', 'left'), 0)
    else:
        required_gap = conflict_matrix.get(('block', 'top'), 0) # vorausgesetzt, dass top und bottom-Gap gleich groß sind.
    

    # für den Fall, dass eine 'dsr'-Stellfläche oder 'block'-Stellfläche ohne zusätzlichen Fahrweg platziert werden kann
    if fahrweg_links_anliegend or block_oben_unten:

        # 1) Maße des Rechtecks, das auf der Parkfläche platziert wird.
        stellplatz_und_fahrweg_kombination = (x, y, breite_stell, hoehe_stell)

        # 2) Liste der Parkflächen updaten
        new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination, available_spaces, best_fit_index)
        available_spaces.clear()
        available_spaces.extend(new_spaces)

        # 3) Platzierung der Stellfläche 
        platzierte_stellflaechen.append((vehicle_type, x , y ,breite_stell ,hoehe_stell, isblock))

        return None 
    

    # für den Fall, dass ein zusätzlicher Fahrweg links von der 'dsr'- Stellfläche hinzugefügt werden muss
    if rect_type=='dsr' and not fahrweg_links_anliegend:
        
        # 1) Neue Maße des Rechteckes, das aus Stellfläche und Fahrweg besteht und auf der Parkfläche platziert wird
        stellplatz_und_fahrweg_kombination = (x, y, breite_stell + required_gap, hoehe_stell)

        # 2) Liste der Parkflächen updaten
        new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination,available_spaces,best_fit_index)
        available_spaces.clear()
        available_spaces.extend(new_spaces)
       
        # 3) Platzierung der Stellfläche unter Berücksichtigung der x-Koordinatenverschiebung 
        platzierte_stellflaechen.append((vehicle_type, x + required_gap , y ,breite_stell ,hoehe_stell , isblock))

        # 4) Platzierung des Fahrweges 
        fahrweg = (x, y, required_gap, hoehe_stell)
        fahrwege.append(fahrweg)

        return None 
    
    if rect_type == 'block':

        # für den Fall, dass ein Fahrweg sowohl oben als auch unten an der Parkfläche anliegt, die Höhe der 'block'-Stellfläche jedoch geringer ist als die der ausgewählten Parkfläche: 
        # nach der Bottom-Left-Fill Heuristik:
        # in diesem Fall wird der untere bereits existierende Fahrweg genutzt und ein neuer oberer hinzugefügt.
        if fahrweg_unten_anliegend:

            # 1) Neue Maße des Rechteckes, das aus Stellfläche und Fahrweg besteht und auf der Parkfläche platziert wird
            stellplatz_und_fahrweg_kombination = (x , y, breite_stell , hoehe_stell + required_gap)

            # 2) Liste der Parkflächen updaten
            new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination,available_spaces,best_fit_index)
            available_spaces.clear()
            available_spaces.extend(new_spaces)

            # 3) Platzierung der Stellfläche 
            platzierte_stellflaechen.append((vehicle_type, x, y ,breite_stell ,hoehe_stell , isblock))

            # 4) Platzierung des Fahrweges 
            fahrweg = (x, y + hoehe_stell , breite_stell, required_gap)
            fahrwege.append(fahrweg)

            return None 
        
        # für den Fall, dass ein Fahrweg nur oberhalb der Parkfläche anliegt:
        # Fall 1: 
        # nach der BLF-Heuristik und der Platzierung der Stellfläche in der linken-unteren Ecke der Parkfläche, entspricht die Höhe der Stellfläche inkusive des unteren Fahrwegs,
        # der Höhe der Parkfläche und der oben anliegende Fahrweg wird genutzt
        elif fahrweg_oben_anliegend_genutzt:
            
            # 1) Neue Maße des Rechteckes, das aus Stellfläche und Fahrweg besteht und auf der Parkfläche platziert wird
            stellplatz_und_fahrweg_kombination = (x, y, breite_stell, hoehe_stell + required_gap)

            # 2) Liste der Parkflächen updaten
            new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination,available_spaces,best_fit_index)
            available_spaces.clear()
            available_spaces.extend(new_spaces)

            # 3) Platzierung der Stellfläche 
            platzierte_stellflaechen.append((vehicle_type, x, y + required_gap, breite_stell ,hoehe_stell , isblock))

            # 4) Platzierung des Fahrweges 
            fahrweg = (x, y, breite_stell, required_gap)
            fahrwege.append(fahrweg)

            return None 
        
        # für den Fall, dass nur ein Fahrweg oberhalb der Parkfläche anliegt:
        # Fall 2: 
        # nach der BLF-Heuristik und der Platzierung der Stellfläche in der linken-unteren Ecke der Parkfläche, entspricht die Höhe der Stellfläche inkusive des unteren Fahrwegs,
        # nicht der Höhe der Parkfläche und der oben anliegende Fahrweg kann nicht genutzt werden.
        elif fahrweg_oben_anliegend_nichtgenutzt:

            # 1) Neue Maße des Rechteckes, das aus Stellfläche und Fahrweg besteht und auf der Parkfläche platziert wird
            stellplatz_und_fahrweg_kombination = (x, y, breite_stell, hoehe_stell + 2*required_gap)

            # 2) Liste der Parkflächen updaten
            new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination,available_spaces,best_fit_index)
            available_spaces.clear()
            available_spaces.extend(new_spaces)

            # 3) Platzierung der Stellfläche 
            platzierte_stellflaechen.append((vehicle_type, x, y + required_gap, breite_stell ,hoehe_stell , isblock))

            # 4) Platzierung der Fahrwege
            fahrweg_unten = (x, y, breite_stell, required_gap)
            fahrweg_oben = (x, y + required_gap + hoehe_stell, breite_stell, required_gap)
            fahrwege.append(fahrweg_unten)
            fahrwege.append(fahrweg_oben)

            return None 

        # für den Fall, dass kein Fahrweg an der Parkfläche anliegt und sowohl oberhalb als auch unterhalb der Stellfläche ein Fahrweg hinzugefügt wird.
        elif not fahrweg_unten_anliegend and not fahrweg_oben_anliegend:

    
            # 1) Neue Maße des Rechteckes, das aus Stellfläche und Fahrwegen besteht und auf der Parkfläche platziert wird
            stellplatz_und_fahrweg_kombination = (x , y , breite_stell , hoehe_stell + 2*required_gap)

            # 2) Liste der Parkflächen updaten
            new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination,available_spaces,best_fit_index)
            available_spaces.clear()
            available_spaces.extend(new_spaces)

            # 3) Platzierung der Stellfläche 
            platzierte_stellflaechen.append((vehicle_type, x , y + required_gap ,breite_stell ,hoehe_stell , isblock))

            # 4) Platzierung des Fahrweges 
            fahrweg_unten = (x, y, breite_stell, required_gap)
            fahrweg_oben = (x, y + required_gap + hoehe_stell, breite_stell, required_gap)
            fahrwege.append(fahrweg_unten)
            fahrwege.append(fahrweg_oben)

            return None 
        

    return None


def get_newfree_rectangles(stellplatz_und_fahrweg_kombination, available_spaces, best_fit_index, min_size=2):
    """
    Schneidet aus dem freien Rechteck (`parkflaeche`) die neu platzierte Stellfläche (`stellplatz`)
    heraus und gibt eine Liste der übrigen Teil-Rechtecke zurück, 
    (sofern sie größer als 15x15 sind.)

    Args:
        parkflaeche: (x, y, breite, höhe) des verfügbaren Bereichs.
        stellplatz: (x, y, breite, höhe) des platzierten Objekts.
        min_size (int, optional): Minimale Breite und Höhe der neuen Rechtecke. Defaults to 15.
    """
    parkflaeche = available_spaces[best_fit_index]
    x_frei, y_frei, frei_breite, frei_hoehe = parkflaeche
    x_stell, y_stell, breite_stell, hoehe_stell = stellplatz_und_fahrweg_kombination

    # Berechnung der rechten unteren Ecke beider Rechtecke
    x_frei_rechts = x_frei + frei_breite
    y_frei_unten = y_frei + frei_hoehe
    x_stell_rechts = x_stell + breite_stell
    y_stell_unten = y_stell + hoehe_stell

    # Liste für die neuen freien Rechtecke
    free_rects = []

    # Überprüfen, ob es überhaupt eine Überlappung gibt
    if not (x_stell_rechts <= x_frei or x_stell >= x_frei_rechts or
            y_stell_unten <= y_frei or y_stell >= y_frei_unten):

        # Oberes Rechteck
        if y_stell > y_frei:
            neue_hoehe = y_stell - y_frei
            if neue_hoehe >= min_size:
                free_rects.append((x_frei, y_frei, frei_breite, neue_hoehe))

        # Linkes Rechteck
        if x_stell > x_frei:
            neue_breite = x_stell - x_frei
            if neue_breite >= min_size:
                free_rects.append((x_frei, y_stell, neue_breite, min(hoehe_stell, y_frei_unten - y_stell)))

        # Rechtes Rechteck
        if x_stell_rechts < x_frei_rechts:
            neue_breite = x_frei_rechts - x_stell_rechts
            if neue_breite >= min_size:
                free_rects.append((x_stell_rechts, y_stell, neue_breite, min(hoehe_stell, y_frei_unten - y_stell)))

        # Unteres Rechteck
        if y_stell_unten < y_frei_unten:
            neue_hoehe = y_frei_unten - y_stell_unten
            if neue_hoehe >= min_size:
                free_rects.append((x_frei, y_stell_unten, frei_breite, neue_hoehe))


    available_spaces.extend(free_rects)

    # Entfernen der verwendeten Fläche aus der Liste der verfügbaren Flächen im Behälter
    del available_spaces[best_fit_index]

    # die neue Liste der freien Flächen auf mögliche Zusammenführung von benachbarten Flächen prüfen
    merge_rectangles_in_available_spaces(available_spaces)

    # Sortiert die freien Flächen primär nach Höhe(Y) und sekundär nach Breite(X) nach Patrik Mundt 
    available_spaces = sorted(available_spaces, key=lambda s: (s[3], s[2]))
    
    return available_spaces




def merge_rectangles_in_available_spaces(available_spaces):
        
    def can_merge(rect1, rect2):
        """
        Überprüft, ob zwei Rechtecke zusammengeführt werden können.
        """
        x1,y1,w1,h1 = rect1
        x2,y2,w2,h2 = rect2
        
        # Prüfen, ob zwei freie Rechtecke vertikal nebeneinanderliegen
        if approximatly_equal(x1 , x2) and approximatly_equal(w1 , w2):
            if approximatly_equal(y1 + h1 , y2) or approximatly_equal(y2 + h2 , y1):
                return 'vertical'

        # Prüfen, ob zwei freie Rechtecke horizontal nebeneinanderliegen 
        if approximatly_equal(y1,y2) and approximatly_equal(h1,h2):
            if approximatly_equal(x1+w1, x2) or approximatly_equal(x2 +w2, x1):
                return 'horizontal'
    
        return False 
        

    def merge_rectangles(rect1, rect2, direction):
        """
        Führt zwei zusammenführbare Rechtecke zusammen.
        """
        x1,y1,w1,h1 = rect1
        x2,y2,w2,h2 = rect2

        if direction == 'horizontal':
            new_x = min(x1,x2)
            new_y = y1 # y1 == y2
            new_w = w1 + w2
            new_h = h1
        elif direction == 'vertical':
            new_x = x1
            new_y = min(y1,y2)
            new_w = w1
            new_h = h1 + h2
        else:
            return None
        
        return(new_x,new_y,new_w,new_h)
    
    merged = True
    while merged:
        merged = False
        n = len(available_spaces)
        for i in range(n):
            rect1 = available_spaces[i]
            for j in range(i + 1, n):
                rect2 = available_spaces[j]
                direction = can_merge(rect1,rect2)
                if direction:
                    new_rect = merge_rectangles(rect1,rect2,direction)

                    # Entfernen der beiden freien Rechtecke aus der Liste und Hinzufügen des neuen Rechteckes
                    available_spaces.pop(j)
                    available_spaces.pop(i)
                    available_spaces.append(new_rect)
                    merged = True 
                    break
            if merged:
                break

    return None



def approximatly_equal(a,b,epsilon = 1e-6):
    return abs(a - b)< epsilon



def enough_space_for_vehicle_and_drivinglanes(stellplatz,parkflaeche,drivinglane_necessary):
    '''
    Für den Fall, dass bereits geprüft wurde, ob eine direkte Platzierung ohne zusätzlichen Fahrweg möglich ist. 
    Und das Ergebnis zeigt, dass ein zusätzlicher Fahrweg erforderlich ist,
    überprüft diese Funktion, ob die ausgewählte Parkfläche ausreichend groß für die Stellfläche inklusive der notwendigen Fahrwege ist.
    '''
    # Entpacken 
    vehicle_type, breite_stell, hoehe_stell, isblock = stellplatz
    x, y, free_w, free_h = parkflaeche

    rect_type = 'block' if isblock else 'dsr'

    fahrweg_links_anliegend = drivinglane_necessary['Fahrweg_links_anliegend']
    fahrweg_unten_anliegend = drivinglane_necessary['Fahrweg_unten_anliegend']
    fahrweg_oben_anliegend = drivinglane_necessary['Fahrweg_oben_anliegend']
    fahrweg_oben_anliegend_genutzt = drivinglane_necessary['Fahrweg_oben_anliegend_genutzt']
    fahrweg_oben_anliegend_nichtgenutzt = drivinglane_necessary['Fahrweg_oben_anliegend_nichtgenutzt']
    #block_oben_unten = drivinglane_necessary['Block_oben_unten']


    # benötigte Fahrwegbreite ermitteln
    conflict_matrix = create_conflict_matrix()
    if not isblock:
        required_gap = conflict_matrix.get((rect_type, 'left'), 0)
    else:
        required_gap = conflict_matrix.get((rect_type, 'top'), 0) # vorausgesetzt, dass top- und bottom-Gap gleich groß sind.


    # für den Fall, dass ein zusätzlicher Fahrweg links von der 'dsr'- Stellfläche hinzugefügt werden muss
    if not isblock:
        if not fahrweg_links_anliegend:
            if free_h >= hoehe_stell and free_w >= breite_stell + required_gap:
                return True 
        
    else:
        # für den Fall, dass kein Fahrweg, weder oberhalb noch unterhalb der Stellfläche, vorhanden ist.
        # in diesem Fall muss sowohl ein Fahrweg am unteren Rand als auch am oberen Rand der Stellfläche hinzugefügt werden.
        if not fahrweg_oben_anliegend and not fahrweg_unten_anliegend:
            if free_w >= breite_stell and free_h >= hoehe_stell + 2*required_gap:
                return True 
        
        # für den Fall, dass nur oben ein Fahrweg anliegt und dieser mit einer BLF Platzierung genutzt werden kann.
        if fahrweg_oben_anliegend_genutzt:
            if free_w >= breite_stell and math.isclose(free_h, hoehe_stell + required_gap, rel_tol = 1e-6):
                return True 
        
        # für den Fall, dass nur oben ein Fahrweg anliegt, dieser jedoch nicht genutzt wird und 2 Fahrwege platziert werden müssen.
        if fahrweg_oben_anliegend_nichtgenutzt:
            if free_w >= breite_stell and free_h >= hoehe_stell + 2*required_gap:
                return True 

        # für den Fall, dass unterhalb der Stellfläche/Parkfläche ein Fahrweg anliegt, oberhalb jedoch einer erzeugt werden muss.
        # gilt auch für den Fall wenn oben ein ungenutzer Fahrweg anliegt 
        if fahrweg_unten_anliegend:
            if free_w >= breite_stell and free_h >= hoehe_stell + required_gap:
                return True 
        

    return False 



def create_conflict_matrix():
    return {
        ('block', 'top'): 8,
        ('block', 'bottom'): 8,
        ('block', 'left'): 0,
        ('block', 'right'): 0,
        ('dsr', 'left'): 8,
        ('dsr', 'right'): 0,
        ('dsr', 'top'): 0,
        ('dsr', 'bottom'): 0
    }


def visualize_available_spaces(container_width, container_height, available_spaces):
    """
    Visualisiert die verfügbaren freien Flächen in einem Behälter.

    :param container_width: Breite des Behälters
    :param container_height: Höhe des Behälters
    :param available_spaces: Liste mit Tupeln (x, y, breite, hoehe), die freie Rechtecke repräsentieren
    """
    # Erstelle die Figur und Achsen
    fig, ax = plt.subplots()  # Dynamische Skalierung
    
    # Setze die Begrenzungen des Behälters
    ax.set_xlim(0, container_width)
    ax.set_ylim(0, container_height)
    ax.set_aspect('equal')  # Quadratverhältnisse beibehalten
    ax.set_title("Visualisierung der freien Flächen")
    
    # Zeichne den Behälter als äußeres Rechteck
    container_rect = patches.Rectangle((0, 0), container_width, container_height, linewidth=2, edgecolor='black', facecolor='none')
    ax.add_patch(container_rect)

    available_spaces = sorted(available_spaces, key=lambda s: (s[3], s[2]))

    # Zeichne alle freien Flächen als Rechtecke
    for i, (x, y, breite, hoehe) in enumerate(available_spaces):
        rect = patches.Rectangle((x, y), breite, hoehe, linewidth=1, edgecolor='green', facecolor='green', alpha=0.3)
        ax.add_patch(rect)
        
        # Beschriftung mit Index in der Mitte des Rechtecks
        ax.text(x + breite / 2, y + hoehe / 2, str(i + 1), ha='center', va='center', fontsize=8, color='black')

    # Zeige das Diagramm
    plt.grid(True, which='both', linestyle='--', linewidth=0.5, alpha=0.7)
    plt.tight_layout()
    plt.show()




def visualize_placements(placements, fahrwege, container_width, container_height,session):
    """
    Visualisiere die Platzierungen der Rechtecke in einem Container.

    Parameter:
    placements (list of tuples): Liste der platzierten Rechtecke mit deren Koordinaten (x, y, width, height).
    container_width (float): Breite des Containers.
    container_height (float): Höhe des Containers.
    """
    angle = 315

    unique_vehicle_types = {entry[0] for entry in placements}
    count_vehicle_types = len(unique_vehicle_types)    
    color_list = sns.color_palette("husl", count_vehicle_types)
    
    color_map = {}
    color_index = 0
    
    # Erstelle eine Figur und eine Achse
    fig, ax = plt.subplots()
    ax.set_xlim(0, container_width)
    ax.set_ylim(0, container_height)
    ax.set_aspect('equal')
    ax.set_title("Visualisierung der Depotauslegung")
    ax.set_xlabel("Breite")
    ax.set_ylabel("Höhe")
    
    # Zeichne den Container
    container = patches.Rectangle((0, 0), container_width, container_height, linewidth=1, edgecolor='black', facecolor='none')
    ax.add_patch(container)

    # Zeichne jedes platzierte Rechteck
    for i, (vehicle_type, x, y, width, height,isblock) in enumerate(placements):
        # Länge und Breite des VehicleTypes abfragen und speichern 
        bus_length = session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() 
        bus_width = session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() 

        if vehicle_type not in color_map:
            color_map[vehicle_type] = color_list[color_index]
            color_index = color_index +1

        color = color_map[vehicle_type]

        # Frame zeichenen  
        #hatch_pattern = '|' if isblock else '/'
        rect = patches.Rectangle((x, y), width, height, linewidth=1, edgecolor = 'black', facecolor= color, alpha=0.3)
        ax.add_patch(rect)
        
        # Fahrzeuge im Frame zeichenen
        # Directstellplatze zeichenen 
        if not isblock:
            direct = []
            # Anzhal der Directstellplätze ermitteln
            direct_parking_slots = 1 + (height - ((bus_length + bus_width) * math.sqrt(2) / 2)) / (bus_width * math.sqrt(2))
            for i in range(round(direct_parking_slots)):
                if i == 0:
                    bus = patches.Rectangle((x, y + bus_width*math.cos(math.radians(45)) ), bus_width, bus_length, angle= angle, edgecolor= 'black', fill = False, linewidth = 0.3)
                    ax.add_patch(bus)
                    direct.append(bus)
                else:
                    prev_bus = direct[-1]
                    new_y = prev_bus.get_y() + bus_width/(math.cos(math.radians(45)))
                    next_bus = patches.Rectangle((x, new_y), bus_width, bus_length, angle= angle, edgecolor= 'black', fill = False, linewidth = 0.3)
                    ax.add_patch(next_bus)
                    direct.append(next_bus)
        else:
            # Blockstellplätze zeichenen 
            # Anzahl der Fahrzeuge die in Reihe stehen ermitteln (in der Regel sollten das standard_block_length entsprechen)
            anzahl_in_reihe = round(height/bus_length)
            anzahl_der_reihen = round(width/bus_width)

            for reihe in range(anzahl_der_reihen):
                for bus in range(anzahl_in_reihe):
                    rect_x = x + reihe*bus_width
                    rect_y = y + bus* bus_length
                    rect = patches.Rectangle((rect_x,rect_y), bus_width, bus_length,edgecolor= 'black', fill = False, linewidth = 0.3)
                    ax.add_patch(rect)

        # Text zum Rechteck hinzufügen
        ax.text(x + width / 2, y + height / 2, f"{i+1}", ha='center', va='center', fontsize=8, color='black')

   
    for i, (x,y,width,height) in enumerate(fahrwege):
        rect = patches.Rectangle((x,y), width , height,linewidth=1, edgecolor = 'black', facecolor= 'black', hatch = '/', alpha=0.3 )
        ax.add_patch(rect)
    
    legend_ = []
    for vehicle_types, color in color_map.items():
        legend_.append(patches.Patch(facecolor= color , edgecolor= 'black',label = vehicle_types))
        
    legend_.append(patches.Patch(facecolor='white', edgecolor='black', hatch = '|', label = "Blockparkplätze"))
    legend_.append(patches.Patch(facecolor='white', edgecolor='black', hatch = '/', label = "Directparkplätze"))

    ax.legend(handles=legend_, loc='center', bbox_to_anchor=(0.5, -0.15), title="Legende", ncol=2)

    # Achsenbeschriftung und Anzeige der Visualisierung
    plt.grid(visible=True, linestyle='--', linewidth=0.5)
    plt.show()

