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

standard_block_length = 6
# maximale Breite der benötigten Fahrwege in Metern 
# die Hälfte von zwei aneinander grenzenden Fahrwegen ergibt einen Fahrweg 
max_driving_lane_width = 8
driving_lane_width = math.ceil(max_driving_lane_width/2)

def bin_packing(session,ergebnisse_gesamt, width_bin=None, height_bin=None):
    stellplaetze_sortiert, stellplaetze_withframe_sortiert = create_stellplaetze(session,ergebnisse_gesamt)
    available_spaces, fahrwege, width_bin, height_bin = create_bin(stellplaetze_sortiert,stellplaetze_withframe_sortiert, width_bin=None, height_bin=None)
    boolean, placed_stellplaetze = place_rectangles_bottom_left_fill(stellplaetze_withframe_sortiert, available_spaces)

    if boolean == True:
        return placed_stellplaetze, fahrwege, width_bin, height_bin
    else:
        raise ValueError("Bin-Packing fehlgeschlagen: Die Stellplätze konnten nicht in den Behälter platziert werden.")




def create_stellplaetze(session,ergebnisse_gesamt):

    stellplaetze = []
    stellplaetze_with_frame = []

    for key, value in ergebnisse_gesamt.items():
        vehicle_type = value["VehicleType"]
        block_parking_slots = value["Block Parkplätze"]
        direct_parking_slots = value["Direct Parkplätze"]

        # Länge und Breite des VehicleTypes aus DB abfragen und speichern 
        bus_length = session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() 
        bus_width = session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() 
        
        # --- BLOCK-STELLPLÄTZE ---
        if block_parking_slots > 0:
            # Anzahl der Reihen in Blockabstellung ermitteln
            extra_line_length = block_parking_slots%standard_block_length
            block_lines = block_parking_slots//standard_block_length
            if extra_line_length > 0:
                block_lines += 1

            # Höhe und Breite der Stellfläche für die Blockstellplätze des VehicleTypes
            # Hinzufügen der erstellten Block-Stellfläche zur Liste aller zu plattzierenden Stellflächen
            block_hohe = standard_block_length * bus_length
            block_breite = block_lines * bus_width
            flaeche_block = (vehicle_type, block_hohe * block_breite)
            is_block = True 
            block_stellplatz = (vehicle_type, block_breite, block_hohe, is_block)
            # Block-Stellplatz mit Fahrbahnumrandung 
            block_stellplatz_with_frame = (vehicle_type, block_breite + max_driving_lane_width, block_hohe + max_driving_lane_width, is_block)

            stellplaetze_with_frame.append(block_stellplatz_with_frame)
            stellplaetze.append(block_stellplatz)


        # --- DIRECT-STELLPLÄTZE ---
        if direct_parking_slots > 0:
            # Höhe und Breite der Stellfläche für die Directparkplätze des VehicleTypes
            sin_45 = math.sin(math.radians(45))
            cos_45 = math.cos(math.radians(45))
            width_of_direct_stellplatz = bus_length * sin_45 + bus_width * sin_45
            length_of_direct_stellplatz = bus_length * sin_45 + bus_width * sin_45 + (direct_parking_slots-1) * bus_width/cos_45
            direct_flaeche = (vehicle_type,width_of_direct_stellplatz*length_of_direct_stellplatz)
            direct_stellplatz = (vehicle_type, width_of_direct_stellplatz, length_of_direct_stellplatz, not is_block)
            # Direct-Stellplatz mit Fahrbahnumrandung 
            direct_stellplatz_with_frame = (vehicle_type, width_of_direct_stellplatz + max_driving_lane_width, length_of_direct_stellplatz + max_driving_lane_width, not is_block)

            stellplaetze_with_frame.append(direct_stellplatz_with_frame)
            stellplaetze.append(direct_stellplatz)



    # Nach der Best-Fit-Decreasing-Heuristik von Mundt
    # decreasing sortieren der Stellflächen, primär nach x und sekundär nach y 
    stellplaetze_sortiert = sorted(stellplaetze, key = lambda r: (-r[1],-r[2])) 
    
    # Decreasing Area (sortiert nach absteigender Fläche)
    stellplaetze_withframe_sortiert = sorted(stellplaetze_with_frame, key = lambda r: r[1]*r[2], reverse =True)

    return stellplaetze_sortiert, stellplaetze_withframe_sortiert



def create_bin(stellplaetze_sortiert,stellplaetze_withframe_sortiert, width_bin=None, height_bin=None):

    # Flächeninhalt aller Stellflächen inklusive Fahrbahnumrandung berechnen
    flaeche = sum(width * length for _, width, length, _ in stellplaetze_withframe_sortiert)

    # Vorprüfung:
    # prüfen, ob Flächeninhalt des Behälters ausreichend ist für die gesamte Fläche der Stellplätze (inklusive der Fahrwege)
    if width_bin is not None and height_bin is not None:
        behaelter_flaeche = width_bin * height_bin
        if behaelter_flaeche < flaeche:
            print("Die Übergebene Parkfläche reicht nicht vom Flächeninhalt, um alle Stellplätze zu plazieren.")
            return None 
        
    # Falls keine Depotfläche vorgegeben ist, wird hier eine quadratische Fläche erzeugt 
    if width_bin is None and height_bin is None:     
        width_bin = math.ceil(math.sqrt(flaeche))+150
        height_bin =  math.ceil(math.sqrt(flaeche))+150
        print("Es wurde eine ausreichend große und quadratische Parkfläche erzeugt")

    # --> hier fehlt der Teil, falls eine Stellfläche zu lang für Depot sein sollte 
    # --> Spliten 

    # Verfügbarer Platz im Behälter unter Berücksichtigung der Fahrbahn am inneren Rand der Parkfläche
    available_spaces = [(driving_lane_width, driving_lane_width, width_bin - max_driving_lane_width, height_bin - max_driving_lane_width)] # (x,y,breite,hoehe)
    
    # Fahrwegumrandung der Parkfläche für die Visualisierung  
    fahrwege = []
    linker_rand = (0, 0, driving_lane_width, height_bin)
    oberer_rand = (0, height_bin - driving_lane_width, width_bin, driving_lane_width)
    rechter_rand = (width_bin - driving_lane_width, 0, driving_lane_width, height_bin)
    unterer_rand = (0, 0, width_bin, driving_lane_width)
    fahrwege.extend([linker_rand,oberer_rand,rechter_rand,unterer_rand])

    return available_spaces, fahrwege, width_bin, height_bin

    


def place_rectangles_bottom_left_fill(stellplaetze_withframe_sortiert, available_spaces):
    """
    Versucht, jedes Rechteck (nach bereits erfolgter Sortierung) 
    mit der Bottom-Left-Fill-Heuristik in den vorhandenen Freiflächen zu platzieren.
    
    Parameter:
    -----------
    stellplaetze_withframe_sortiert : list of tuples
        [(vehicle_type, breite, laenge, boolean), ...] (bereits sortiert)
        
    available_spaces : list of tuples
        [(x, y, width, height), ...] mit zunächst einer Startfläche 
        und ggf. weiteren Freiflächen
    
    Returns:
    --------
    True, wenn alle Rechtecke platziert wurden.
    Exception oder Abbruch, falls nicht platzierbar.
    """
    placed_stellplaetze = []
    # Gehe alle Rechtecke in gegebener Reihenfolge (absteigende Fläche) durch
    for stellplatz in stellplaetze_withframe_sortiert:
        vehicle_type, width_stellplatz, length_stellplatz, isblock = stellplatz
        
        # Suche die "beste" freie Fläche nach Bottom-Left-Kriterium:
        # -> minimalste y-Koordinate
        # (bei Gleichheit minimalste x-Koordinate)
        found_index = None
        best_x = float('inf')
        best_y = float('inf')
        
        # Durchsuche alle verfügbaren Flächen
        for i, (space_x, space_y, space_width, space_height) in enumerate(available_spaces):
            
            # Prüfen, ob der ausgewählte Stellplatz in ausgewählte Bin passt 
            if width_stellplatz <= space_width and length_stellplatz <= space_height:
                
                # Bottom-Left-Kriterium: (space_y < best_y) oder (gleiches y, aber space_x < best_x)
                if (space_y < best_y) or (space_y == best_y and space_x < best_x):
                    best_y = space_y
                    best_x = space_x
                    found_index = i
        
        # Prüfen, ob eine passende Bin gefunden wurde
        if found_index is not None:
            # Stellplatz platzieren und available_spaces updaten
            x, y, _, _ = available_spaces[found_index]
            stellplatz = (vehicle_type, x, y, width_stellplatz, length_stellplatz, isblock)
            placed_stellplaetze.append(stellplatz)
            update_available_spaces(available_spaces, found_index, stellplatz)


        else:
            # Konnte nicht platziert werden: Abbruch
            raise RuntimeError(f"Rechteck (Typ: {vehicle_type}, B: {width_stellplatz}, L: {length_stellplatz}) ""konnte nicht platziert werden!")
    
    # Falls alle Rechtecke platziert wurden, gebe True zurück
    return True, placed_stellplaetze



def update_available_spaces(available_spaces, best_fit_index, stellplatz, min_size=2):
    """
    Schneidet aus dem freien Rechteck (`parkflaeche`) die neu platzierte Stellfläche (`stellplatz`)
    heraus und gibt eine Liste der übrigen Teil-Rechtecke zurück, 
    sofern sie größer als 15x15 sind.

    Args:
        parkflaeche: (x, y, breite, höhe) des verfügbaren Bereichs.
        stellplatz: (x, y, breite, höhe) des platzierten Objekts.
        min_size (int, optional): Minimale Breite und Höhe der neuen Rechtecke. Defaults to 15.
    """
    parkflaeche = available_spaces[best_fit_index]
    x_frei, y_frei, frei_breite, frei_hoehe = parkflaeche
    _, x_stell, y_stell, breite_stell, hoehe_stell, _ = stellplatz


    # Berechne leftover Platz nach rechts 
    leftover_right = frei_breite - breite_stell
    if leftover_right >= min_size:
        # Rechte Restfläche, gleiche y-Koordinate wie die platzierte Fläche, Höhe = platzierte Rechteckhöhe
        rect_right = (x_frei + breite_stell, y_frei, leftover_right, hoehe_stell)
        available_spaces.append(rect_right)

    # Berechne leftover Platz nach oben
    leftover_height = frei_hoehe - hoehe_stell
    if leftover_height >= min_size:
        # Obere Restfläche, selber x-Freiraum wie ursprünglich, y-Koordinate = y_frei + platzierte_Höhe, volle Breite wie die alte Fläche
        rect_oben = (x_frei, y_frei + hoehe_stell, frei_breite, leftover_height)
        available_spaces.append(rect_oben)

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



def visualisierung_own_bin_packing(placed_stellplaetze, fahrwege, width_bin, height_bin, session):
    
    stellplatz_without_drivinglane = []

    for stellplatz in placed_stellplaetze:
        # Entpacken 
        vehicle_type, x, y, width, height, isblock = stellplatz

        # eigentliche Stellfläche ohne Fahrweg ermitteln
        stellplatz_without_drivinglane.append((vehicle_type, x + driving_lane_width, y + driving_lane_width, width - max_driving_lane_width, height - max_driving_lane_width, isblock))

    angle = 315

    unique_vehicle_types = {entry[0] for entry in stellplatz_without_drivinglane}
    count_vehicle_types = len(unique_vehicle_types)    
    color_list = sns.color_palette("husl", count_vehicle_types)
    
    color_map = {}
    color_index = 0


    # Erstelle eine Figur und eine Achse
    fig, ax = plt.subplots()
    ax.set_xlim(0, width_bin)
    ax.set_ylim(0, height_bin)
    ax.set_aspect('equal')
    ax.set_title("Visualisierung der Depotauslegung")
    ax.set_xlabel("Breite")
    ax.set_ylabel("Höhe")
    
    # Zeichne den Container
    container = patches.Rectangle((0, 0), width_bin, height_bin, linewidth=1, edgecolor='black', facecolor='none')
    ax.add_patch(container)

    # Zeichnen der inneren Fahrbahnumrandung 
    for i, (x,y,width,height) in enumerate(fahrwege):
        rect = patches.Rectangle((x,y), width , height,linewidth=1, edgecolor = 'black', facecolor= 'lightgray', hatch = '/', alpha=0.3 )
        ax.add_patch(rect)

    # Zeichenen der Driving Lane der Stellflächen 
    for (_ , x, y, breite, hoehe, isblock) in placed_stellplaetze:
        driving_lane = patches.Rectangle((x, y), breite, hoehe, linewidth=1, edgecolor='black', facecolor='lightgray', hatch='/', alpha=0.6)
        ax.add_patch(driving_lane)

    # Stellplätze zeichenen(Frame)
    for i, (vehicle_type, x, y, width, height,isblock) in enumerate(stellplatz_without_drivinglane):

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

        # Nummerierung der Stellflächen 
        ax.text(x + width / 2, y + height / 2, f"{i+1}", ha='center', va='center', fontsize=8, color='black')

    legend_ = []
    for vehicle_types, color in color_map.items():
        legend_.append(patches.Patch(facecolor= color , edgecolor= 'black',label = vehicle_types))
        
    legend_.append(patches.Patch(facecolor='white', edgecolor='black', hatch = '|', label = "Blockparkplätze"))
    legend_.append(patches.Patch(facecolor='white', edgecolor='black', hatch = '/', label = "Directparkplätze"))

    ax.legend(handles=legend_, loc='center', bbox_to_anchor=(0.5, -0.15), title="Legende", ncol=2)

    # Achsenbeschriftung und Anzeige der Visualisierung
    plt.grid(visible=True, linestyle='--', linewidth=0.5)
    plt.show()


    
