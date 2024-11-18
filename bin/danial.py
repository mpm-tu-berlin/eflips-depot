import math
from datetime import timedelta

from eflips.model import (
    Area,
    AreaType,
    AssocPlanProcess,
    Depot,
    Event,
    EventType,
    Plan,
    Rotation,
    Vehicle,
    VehicleType,
    Process,
)
from sqlalchemy import func

import eflips.depot.api

# anpassbare Variablen:
STANDARD_BLOCK_LENGTH = 6


def anzahl_an_line_parkplaetzen(session, peak_count, vehicle_type):
    # Länge und Breite für VehicleType abfragen
    x = (
        session.query(VehicleType.length)
        .filter(VehicleType.id == vehicle_type.id)
        .scalar()
    )  # length
    z = (
        session.query(VehicleType.width)
        .filter(VehicleType.id == vehicle_type.id)
        .scalar()
    )  # width

    if x is not None and z is not None:
        # Flächenberechnung für die Direct-Area geteilt durch die Fläche für einen Block-Parkplatz = maximale Anzahl der Block-Parkplätze
        breite = x * math.sin(math.radians(45)) + z * math.sin(math.radians(45))
        laenge = (
            x * math.sin(math.radians(45))
            + z * math.sin(math.radians(45))
            + (peak_count - 1) * z / math.cos(math.radians(45))
        )
        max_block_busse = math.floor((breite * laenge) / (x * z))

        # Wie viele Reihen sind mit den Bussen in Blockabstellung möglich?
        max_line_count = int(max_block_busse / STANDARD_BLOCK_LENGTH)

        # Wird eine zusätzliche Blockreihe benötigt?
        extra_line_length = 0
        if max_block_busse % STANDARD_BLOCK_LENGTH not in (1, 0):
            max_line_count += 1
            extra_line_length = max_block_busse % STANDARD_BLOCK_LENGTH
            extra_line = True
            print(
                f"Es wird {max_line_count} Iterationen geben. Davon ist eine, eine Extra-Line mit der Cpacity von {extra_line_length} Parkplätzen"
            )
        else:
            extra_line = False
            max_line_count = max_line_count

            print(f"Es wird {max_line_count} Iterationen geben")

        return max_line_count, extra_line, extra_line_length
    else:
        print(f"Keine Länge oder Breite für VehicleType{vehicle_type} gefunden")
        return None


# Funktion zur Abfrage von Depot,Plan und Prozessen aus dem Scenario
def abfrage_aus_scenario(session, scenario):
    depot = session.query(Depot).filter(Depot.scenario_id == scenario.id).first()
    plan = session.query(Plan).filter(Plan.scenario_id == scenario.id).first()

    clean = (
        session.query(Process)
        .filter(Process.scenario_id == scenario.id, Process.name == "Clean")
        .one()
    )
    charging = (
        session.query(Process)
        .filter(Process.scenario_id == scenario.id, Process.name == "Charging")
        .one()
    )
    standby_departure = (
        session.query(Process)
        .filter(Process.scenario_id == scenario.id, Process.name == "Standby Departure")
        .one()
    )

    return depot, plan, clean, charging, standby_departure


# Funktion zur Verknüpfung der Assoc vor jeder Simulation
def create_depot_areas_and_processes(
    session, scenario, plan, clean, charging, standby_departure
):
    assocs = [
        AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
        AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=2),
        AssocPlanProcess(
            scenario=scenario, process=standby_departure, plan=plan, ordinal=3
        ),
    ]
    session.add_all(assocs)


# Funktion zur Bestimmung der Direct-Peak-Usages der Verschiedenen Ladezonen für die verschieden Vehicle-Types
def give_back_peak_usage_direct_for_multiple_types(session, charging_areas, scenario):
    result_by_area = {}

    for charging_area in charging_areas:
        # Step 1: Lade alle relevanten Events für die aktuelle Ladezone
        charging_events = (
            session.query(Event)
            .filter(
                Event.scenario_id == charging_area.scenario_id,
                Event.area_id == charging_area.id,
                Event.event_type == EventType.CHARGING_DEPOT,
            )
            .all()
        )

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
                time_points.append((event.time_start, "start"))
                time_points.append((event.time_end, "end"))

            # Sortiere die Zeitpunkte
            time_points.sort()

            # Iteriere durch alle Zeitpunkte und berechne die gleichzeitigen Ladevorgänge
            for time, point_type in time_points:
                if point_type == "start":
                    current_count += 1
                    cur_direct_peak = max(cur_direct_peak, current_count)
                elif point_type == "end":
                    current_count -= 1

        # Fahrzeuganzahl für den aktuellen Fahrzeugtyp in der Ladezone
        vehicle_count_by_type = (
            session.query(func.count(Vehicle.id))
            .filter(
                Vehicle.vehicle_type_id == charging_area.vehicle_type_id,
                Vehicle.scenario_id == scenario.id,
            )
            .scalar()
        )

        # Fahrzeugtyp für die aktuelle Ladezone holen
        vehicle_type = (
            session.query(VehicleType)
            .filter(VehicleType.id == charging_area.vehicle_type_id)
            .first()
        )

        # Speichern der Peak Usage, Fahrzeuganzahl und Fahrzeugtyp in der Result-Dictionary
        result_by_area[charging_area.name] = {
            "peak_usage": cur_direct_peak,
            "vehicle_count": vehicle_count_by_type,
            "vehicle_type": vehicle_type,
        }

    return result_by_area


# Funktion zur Bestimmung des Peak-Usage der Direct-Area für eine Charging-Area
def give_back_peak_usage_direct(session, scenario, charging_area):
    # Lade alle relevanten Events für die aktuelle Ladezone
    charging_events = (
        session.query(Event)
        .filter(
            Event.scenario_id == scenario.id,
            Event.area_id == charging_area.id,
            Event.event_type == EventType.CHARGING_DEPOT,
        )
        .all()
    )

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
        time_points.append((event.time_start, "start"))
        time_points.append((event.time_end, "end"))

    # Sortiere die Zeitpunkte
    time_points.sort()

    # Iteriere durch alle Zeitpunkte und berechne die gleichzeitigen Ladevorgänge
    for time, point_type in time_points:
        if point_type == "start":
            current_count += 1
            cur_direct_peak = max(cur_direct_peak, current_count)
        elif point_type == "end":
            current_count -= 1

    return cur_direct_peak


# Funktion zur Bestimmung der benötigten Fläche für die Iteration i
def flaechen_bedarf(
    session,
    i,
    cur_direct_peak,
    extra_line,
    extra_line_length,
    max_line_count,
    vehicle_type,
):
    # Länge und Breite für VehicleType abfragen
    x = (
        session.query(VehicleType.length)
        .filter(VehicleType.id == vehicle_type.id)
        .scalar()
    )  # length
    z = (
        session.query(VehicleType.width)
        .filter(VehicleType.id == vehicle_type.id)
        .scalar()
    )  # width

    flaeche = 0
    block_parking_slots = 0
    direct_parking_slots = 0
    simulation_with_extra_line = False

    # Prüfen ob es eine ExtraLine gibt und Fläche berechenen für die Block-Parkplätze
    # Anzahl der Block-Parkplätze
    if i == max_line_count and extra_line:
        flaeche += (i - 1) * STANDARD_BLOCK_LENGTH * (x * z)
        flaeche += extra_line_length * (x * z)
        block_parking_slots = (i - 1) * STANDARD_BLOCK_LENGTH + extra_line_length
        simulation_with_extra_line = True
    else:
        flaeche += (i * STANDARD_BLOCK_LENGTH) * (x * z)
        block_parking_slots = i * STANDARD_BLOCK_LENGTH

    # Fläche der Direct-Parkplätze berechenen
    # Anzahl der Direct-Parkplätze
    if cur_direct_peak > 0:
        breite = x * math.sin(math.radians(45)) + z * math.sin(math.radians(45))
        laenge = (
            x * math.sin(math.radians(45))
            + z * math.sin(math.radians(45))
            + (cur_direct_peak - 1) * z / math.cos(math.radians(45))
        )

        flaeche += breite * laenge
        direct_parking_slots = cur_direct_peak
    elif cur_direct_peak == 0:
        flaeche += 0
        cur_direct_peak = 0

    return (
        round(flaeche, 2),
        block_parking_slots,
        direct_parking_slots,
        simulation_with_extra_line,
    )


def erster_simulations_durchlauf(session, scenario):
    # Abfrage, aller existierenden VehicleTypes
    vehicle_types = (
        session.query(VehicleType).filter(VehicleType.scenario_id == scenario.id).all()
    )

    if not vehicle_types:
        print("In dem aktuellen Scenario befinden sich keine VehicleType Objekte.")
        return None

    for vehicle_type in vehicle_types:
        print(f"Fahrzeugtyp {vehicle_type.name} (ID {vehicle_type.id}) wurde gefunden.")

    rotations = (
        session.query(Rotation).filter(Rotation.scenario_id == scenario.id).count()
    )
    print(rotations)

    platzhalter = abfrage_aus_scenario(session, scenario)
    if any(value is None for value in platzhalter):
        print(
            "Depot,Plan oder einer der Prozesse konnte nicht aus dem Scenario abgefragt werden"
        )
        return None
    else:
        depot, plan, clean, charging, standby_departure = platzhalter

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
    create_depot_areas_and_processes(
        session, scenario, plan, clean, charging, standby_departure
    )

    # Clear previous vehicle and event data
    session.query(Rotation).filter(Rotation.scenario_id == scenario.id).update(
        {"vehicle_id": None}
    )
    session.query(Event).filter(Event.scenario == scenario).delete()
    session.query(Vehicle).filter(Vehicle.scenario == scenario).delete()

    # Run the simulation
    try:
        eflips.depot.api.simple_consumption_simulation(
            scenario, initialize_vehicles=True
        )
        eflips.depot.api.simulate_scenario(
            scenario, repetition_period=timedelta(days=1)
        )
        session.flush()
        session.expire_all()
        eflips.depot.api.simple_consumption_simulation(
            scenario, initialize_vehicles=False
        )

        # Überprüfungs-Tools:
        vehicle_type_counts = (
            session.query(
                Vehicle.vehicle_type_id, VehicleType.name, func.count(Vehicle.id)
            )
            .join(VehicleType, Vehicle.vehicle_type_id == VehicleType.id)
            .filter(Vehicle.scenario_id == scenario.id)
            .group_by(Vehicle.vehicle_type_id, VehicleType.name)
            .all()
        )
        for vehicle_type_id, vehicle_type_name, count in vehicle_type_counts:
            print(
                f"Fahrzeugtyp {vehicle_type_name} (ID {vehicle_type_id}): {count} Fahrzeuge aktiv"
            )

    except AssertionError:
        print("Fehler: SoC ist geringer als Erwartet.")
        session.rollback()
        return None
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist aufgetreten: {e}")
        session.rollback()
        return None

    # Peak-Usage von Direct-Parkplätzen für jede ChargingArea ermitteln:
    result_by_area = give_back_peak_usage_direct_for_multiple_types(
        session, charging_areas, scenario
    )
    session.rollback()

    for area_name, data in result_by_area.items():
        name = area_name
        peak_count = data["peak_usage"]  # Zugriff auf 'peak_usage'
        vehicle_count_by_type = data["vehicle_count"]  # Zugriff auf 'vehicle_count'
        vehicle_type = data["vehicle_type"]  # Zugriff auf 'vehicle_type'

        print(
            f" Für {name}: Die Spitzenbelastung ist {peak_count} Fahrzeuge. Und es sind {vehicle_count_by_type} Fahrzeuge von diesem Typ aktiv. Der Vehicle_Type lautet {vehicle_type}"
        )

    return result_by_area


def simulations_loop(result_by_area, session, scenario):
    if not result_by_area:
        print("Die übergebenen Ergebnisse sind fehlerhalft.")
        return None

    # Ergebnisse für alle Fahrzeugtypen
    ergebnisse_gesamt = {}

    depot, plan, clean, charging, standby_departure = abfrage_aus_scenario(
        session, scenario
    )

    for area_name, data in result_by_area.items():
        name = area_name
        peak_count = data["peak_usage"]  # Zugriff auf 'peak_usage'
        vehicle_count_by_type = data["vehicle_count"]  # Zugriff auf 'vehicle_count'
        vehicle_type = data["vehicle_type"]  # Zugriff auf 'vehicle_type'

        print(f"Simulation für den Bus-Type{vehicle_type}")

        # Ergenisse für den aktuellen Fahrzeugtypen
        ergebnisse = []

        # Berechnung wie viele Block-Parkplätze gerade noch kleiner sind, als die benötigten Direct-Parklätze für einen VehicleType
        anzahl_block = anzahl_an_line_parkplaetzen(session, peak_count, vehicle_type)
        if anzahl_block is None:
            print("Keine Werte für Breite oder Länge in VehicleType Objekt gefunden")
            return None
        else:
            max_line_count, extra_line, extra_line_length = anzahl_block

        # Schleife zur Ermittlung der minimalen Anzahl an Parkplätzen
        for i in range(1, max_line_count + 1):  # Anzahl der möglichen Block-Parkplätzen
            try:
                if i == max_line_count and extra_line:
                    charging_line_area_extra = Area(
                        scenario=scenario,
                        name=name,
                        depot=depot,
                        area_type=AreaType.LINE,
                        capacity=extra_line_length,
                        vehicle_type=vehicle_type,
                    )
                    session.add(charging_line_area_extra)
                    charging_line_area_extra.processes.append(charging)
                    charging_line_area_extra.processes.append(standby_departure)

                    for b in range(i - 1):
                        charging_line_area = Area(
                            scenario=scenario,
                            name=name,
                            depot=depot,
                            area_type=AreaType.LINE,
                            capacity=STANDARD_BLOCK_LENGTH,
                            vehicle_type=vehicle_type,
                        )
                        session.add(charging_line_area)
                        charging_line_area.processes.append(charging)
                        charging_line_area.processes.append(standby_departure)

                else:
                    # Create Line Area with varibale Lines
                    for b in range(i):
                        charging_line_area = Area(
                            scenario=scenario,
                            name=name,
                            depot=depot,
                            area_type=AreaType.LINE,
                            capacity=STANDARD_BLOCK_LENGTH,
                            vehicle_type=vehicle_type,
                        )
                        session.add(charging_line_area)
                        charging_line_area.processes.append(charging)
                        charging_line_area.processes.append(standby_departure)

                # Create charging area: gesetze Direct-Kapazität
                charging_area = Area(
                    scenario=scenario,
                    name=name,
                    depot=depot,
                    area_type=AreaType.DIRECT_ONESIDE,
                    capacity=peak_count,  # Aus der ersten Depotsimulation: fester Wert
                    vehicle_type=vehicle_type,
                )
                session.add(charging_area)
                charging_area.processes.append(charging)
                charging_area.processes.append(standby_departure)

                # Puffer Parkplätze für die anderen in der Session enthaltenen Fahrzeugtypen
                for area_name_other, data in result_by_area.items():
                    other_vehicle_type = data["vehicle_type"]
                    vehicle_count = data["vehicle_count"]
                    if other_vehicle_type != vehicle_type:
                        # Parkfläche für Pufferzonen
                        charging_area_buffer = Area(
                            scenario=scenario,
                            name=name,
                            depot=depot,
                            area_type=AreaType.LINE,
                            capacity=vehicle_count + 5,
                            vehicle_type=other_vehicle_type,
                        )
                        session.add(charging_area_buffer)
                        charging_area_buffer.processes.append(charging)
                        charging_area_buffer.processes.append(standby_departure)

                # Call the function to connect processes
                create_depot_areas_and_processes(
                    session, scenario, plan, clean, charging, standby_departure
                )

                # Simulation
                # Clear previous vehicle and event data
                session.query(Rotation).filter(
                    Rotation.scenario_id == scenario.id
                ).update({"vehicle_id": None})
                session.query(Event).filter(Event.scenario == scenario).delete()
                session.query(Vehicle).filter(Vehicle.scenario == scenario).delete()

                eflips.depot.api.simple_consumption_simulation(
                    scenario, initialize_vehicles=True
                )
                eflips.depot.api.simulate_scenario(
                    scenario, repetition_period=timedelta(days=1)
                )
                session.flush()
                session.expire_all()
                eflips.depot.api.simple_consumption_simulation(
                    scenario, initialize_vehicles=False
                )

            except AssertionError as e:
                print(
                    f"Iteration {i}: Für Fahrzeugtyp{vehicle_type}, Simulation fehlgeschlagen - Delay aufgetreten"
                )
                session.rollback()
                continue

            except Exception as e:
                print(f"Iteration:{i} Ein unerwarteter Fehler ist aufgetreten: {e}")
                session.rollback()
                continue
            else:
                print(f"Iteration:{i} Keine Fehler bei der Simulation aufgetreten.")

            # Vehicle count für aktuellen Vehicle-Type
            vehicle_count = (
                session.query(Vehicle)
                .filter(Vehicle.vehicle_type == vehicle_type)
                .count()
            )

            # Überprüfung ob ein Fahrzeugmehrbedarf entstanden ist
            if vehicle_count > vehicle_count_by_type:
                print(
                    f"Iteration:{i}  Für die Depotauslegung gab es einen Fahrzeugmehrbedarf. Es wurden insgesamt {vehicle_count} Fahrzeuge benötigt."
                )
                session.rollback()
                continue

            # Peak-Usage von Direct-Parkplätzen, für die Konfiguration mit i*block_length Block-Parkplätzen, ermitteln:
            cur_direct_peak = give_back_peak_usage_direct(
                session, scenario, charging_area
            )
            print(cur_direct_peak)

            # Flächenbedarf für aktuelle Konfiguration ermitteln
            (
                flaeche,
                block_parking_slots,
                direct_parking_slots,
                simulation_with_extra_line,
            ) = flaechen_bedarf(
                session,
                i,
                cur_direct_peak,
                extra_line,
                extra_line_length,
                max_line_count,
                vehicle_type,
            )

            zeile = {
                "VehicleType": vehicle_type,
                "Fläche": flaeche,
                "Block Parkplätze": block_parking_slots,
                "Direct Parkplätze": direct_parking_slots,
                "Fahrzeug Anzahl": vehicle_count,
                "Simulation mit ExtraLine": simulation_with_extra_line,
                "ExtraLine Länge": extra_line_length,
                "Iteration": i,
            }

            ergebnisse.append(zeile)

            session.rollback()

        if not ergebnisse:
            print(f"Keine Ergebnisse für {vehicle_type} gefunden")
            continue
        else:
            flaechen_min_depot_konfiguration = min(
                ergebnisse, key=lambda x: x["Fläche"]
            )

        ergebnisse_gesamt[
            f"Fahrzeugtyp{vehicle_type}"
        ] = flaechen_min_depot_konfiguration
        # ergebnisse_gesamt.append(flaechen_min_depot_konfiguration)

    return ergebnisse_gesamt


# Simulation und Speichern der besten Depot-Auslegung
def optimale_simulation(ergebnisse_gesamt, session, scenario):
    if not ergebnisse_gesamt:
        print("Die übergebenen Ergebnisse sind fehlerhalft.")
        return

    depot, plan, clean, charging, standby_departure = abfrage_aus_scenario(
        session, scenario
    )

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
                scenario=scenario,
                name="Entenhausen Depot Area",
                depot=depot,
                area_type=AreaType.LINE,
                capacity=extra_line_length,  # Falls eine extra Line vorhanden
                vehicle_type=vehicle_type,
            )
            session.add(charging_line_area_extra)
            charging_line_area_extra.processes.append(charging)
            charging_line_area_extra.processes.append(standby_departure)

            for b in range(iteration - 1):
                charging_line_area = Area(
                    scenario=scenario,
                    name="Entenhausen Depot Area",
                    depot=depot,
                    area_type=AreaType.LINE,
                    capacity=STANDARD_BLOCK_LENGTH,
                    vehicle_type=vehicle_type,
                )
                session.add(charging_line_area)
                charging_line_area.processes.append(charging)
                charging_line_area.processes.append(standby_departure)
        else:
            for b in range(iteration):
                charging_line_area = Area(
                    scenario=scenario,
                    name="Entenhausen Depot Area",
                    depot=depot,
                    area_type=AreaType.LINE,
                    capacity=STANDARD_BLOCK_LENGTH,
                    vehicle_type=vehicle_type,
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

        create_depot_areas_and_processes(
            session, scenario, plan, clean, charging, standby_departure
        )
    session.commit()
    # Simulation
    # Clear previous vehicle and event data
    session.query(Rotation).filter(Rotation.scenario_id == scenario.id).update(
        {"vehicle_id": None}
    )
    session.query(Event).filter(Event.scenario == scenario).delete()
    session.query(Vehicle).filter(Vehicle.scenario == scenario).delete()

    # Run the simulation
    eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=True)
    session.commit()
    eflips.depot.api.simulate_scenario(scenario, repetition_period=timedelta(days=1))
    eflips.depot.api.simple_consumption_simulation(scenario, initialize_vehicles=False)
