
import sqlalchemy.orm
import math
import matplotlib.pyplot as plt
import matplotlib.patches as patches
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

'''
def fahrweg_pruefung(stellplatz,parkflaeche,fahrwege):
    # Entpacken 
    x,y,free_w,free_h = parkflaeche
    vehicle_type,breite_stell,hoehe_stell, isblock = stellplatz

    # Flags
    fahrweg_links_anliegend = False
    fahrweg_unten_anliegend = False 
    fahrweg_oben_anliegend = False
    hoehe_stell_gleich_hoehe_park = False 

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
            
            if fahrweg_oben_anliegend == True and fahrweg_unten_anliegend == True:
                # Prüfen, ob die Höhe der Stellflächen der Höhe der Parkfläche entspricht.
                # wichtig für den Fall, wenn sowohl oberhalb als auch unterhalb der Stellfläche ein Fahrweg liegt.
                # es muss weder ober- noch unterhalb der Stellfläche ein Fahrweg hinzugefügt werden. 
                if y+ hoehe_stell == y + free_h:
                    hoehe_stell_gleich_hoehe_park = True
                    break
        

    return {
        'Fahrweg_links_anliegend': fahrweg_links_anliegend,
        'Fahrweg_unten_anliegend': fahrweg_unten_anliegend,
        'Fahrweg_oben_anliegend' : fahrweg_oben_anliegend,
        'Block_oben_unten' : hoehe_stell_gleich_hoehe_park
    }
'''

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

'''
def place_vehicle_and_update(stellplatz,available_spaces,best_fit_index,fahrwege,drivinglane_necessary,platzierte_stellflaechen):
    # Entpacken
    vehicle_type, breite_stell, hoehe_stell, isblock = stellplatz
    parkflaeche = available_spaces[best_fit_index]
    x, y, free_w, free_h = parkflaeche

    rect_type = 'block' if isblock else 'dsr'

    fahrweg_links_anliegend = drivinglane_necessary['Fahrweg_links_anliegend']
    fahrweg_unten_anliegend = drivinglane_necessary['Fahrweg_unten_anliegend']
    fahrweg_oben_anliegend = drivinglane_necessary['Fahrweg_oben_anliegend']
    block_oben_unten = drivinglane_necessary['Block_oben_unten']

    # benötigte Fahrwegbreite ermitteln
    conflict_matrix = create_conflict_matrix()
    if not isblock:
        required_gap = conflict_matrix.get(('dsr', 'left'), 0)
    else:
        required_gap = conflict_matrix.get(('block', 'top'), 0) # vorausgesetzt, dass top und bottom-Gap gleich groß sind.
    

    # für den Fall, dass eine 'dsr'-Stellfläche oder 'block'-Stellfläche ohne zusätzlichen Fahrweg platziert werden kann
    if fahrweg_links_anliegend or block_oben_unten:
        stellplatz_und_fahrweg_kombination = (x, y, breite_stell, hoehe_stell)
        # 1) Platzierung der Stellfläche 
        platzierte_stellflaechen.append((vehicle_type, x , y ,breite_stell ,hoehe_stell, isblock))

        # 2) Liste der Parkflächen updaten
        new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination, available_spaces, best_fit_index)
        available_spaces.clear()
        available_spaces.extend(new_spaces)
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

     

    # für den Fall, dass ein Fahrweg sowohl oben als auch unten an der Parkfläche anliegt, die Höhe der 'block'-Stellfläche jedoch geringer ist als die der ausgewählten Parkfläche: 
    # nach der Bottom-Left-Fill Heuristik:
    # in diesem Fall wird der untere bereits existierende Fahrweg genutzt und ein neuer oberer hinzugefügt.
    # für den Fall, dass unterhalb der Parkfläche ein Fahrweg anliegt
    if rect_type == 'block' and fahrweg_unten_anliegend or (rect_type == 'block' and fahrweg_unten_anliegend and fahrweg_oben_anliegend):
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


    # für den Fall, dass oberhalb der Parkfläche ein Fahrweg anliegt und unterhalb der Stellfläche ein Fahrweg hinzugefügt wird.
    # neue Platzzierungslogik: --> Bottom Left Fill --> Alle Zuweisungen angepasst.
    if rect_type == 'block' and fahrweg_oben_anliegend:
        # 1) Neue Maße des Rechteckes, das aus Stellfläche und Fahrweg besteht und auf der Parkfläche platziert wird
        stellplatz_und_fahrweg_kombination = (x , y +(free_h - hoehe_stell - required_gap), breite_stell , hoehe_stell + required_gap)
        #stellplatz_und_fahrweg_kombination = (x , y , breite_stell , hoehe_stell + 2*required_gap)

        # 2) Liste der Parkflächen updaten
        new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination,available_spaces,best_fit_index)
        available_spaces.clear()
        available_spaces.extend(new_spaces)
    
        # 3) Platzierung der Stellfläche 
        platzierte_stellflaechen.append((vehicle_type, x , y +(free_h - hoehe_stell) ,breite_stell ,hoehe_stell , isblock))
        #platzierte_stellflaechen.append((vehicle_type, x , y + required_gap ,breite_stell ,hoehe_stell , isblock))

        # 4) Platzierung des Fahrweges 
        fahrweg = (x, y + (free_h - hoehe_stell - required_gap), breite_stell, required_gap)
        fahrwege.append(fahrweg)
        #fahrweg_unten = (x, y, breite_stell, required_gap)
        #fahrweg_oben = (x, y + required_gap + hoehe_stell, breite_stell, required_gap)
        #fahrwege.append(fahrweg_unten)
        #fahrwege.append(fahrweg_oben)

        return None 

        
    # für den Fall, dass kein Fahrweg an der Parkfläche anliegt und sowohl oberhalb als auch unterhalb der Stellfläche ein Fahrweg hinzugefügt wird.
    if rect_type == 'block' and not fahrweg_unten_anliegend and not fahrweg_oben_anliegend:
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
'''



# Prüfen bezüglich des Koordinaten-Ursprungs:
def get_newfree_rectangles(stellplatz_und_fahrweg_kombination, available_spaces, best_fit_index, min_size=2):
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

'''
def enough_space_for_vehicle_and_drivinglanes(stellplatz,parkflaeche,drivinglane_necessary):
    
    # Entpacken 
    vehicle_type, breite_stell, hoehe_stell, isblock = stellplatz
    x, y, free_w, free_h = parkflaeche

    rect_type = 'block' if isblock else 'dsr'

    fahrweg_links_anliegend = drivinglane_necessary['Fahrweg_links_anliegend']
    fahrweg_unten_anliegend = drivinglane_necessary['Fahrweg_unten_anliegend']
    fahrweg_oben_anliegend = drivinglane_necessary['Fahrweg_oben_anliegend']
    #block_oben_unten = drivinglane_necessary['Block_oben_unten']


    # benötigte Fahrwegbreite ermitteln
    conflict_matrix = create_conflict_matrix()
    if not isblock:
        required_gap = conflict_matrix.get((rect_type, 'left'), 0)
    else:
        required_gap = conflict_matrix.get((rect_type, 'top'), 0) # vorausgesetzt, dass top- und bottom-Gap gleich groß sind.


    # für den Fall, dass ein zusätzlicher Fahrweg links von der 'dsr'- Stellfläche hinzugefügt werden muss
    if rect_type == 'dsr' and not fahrweg_links_anliegend:
        if free_h >= hoehe_stell and free_w >= breite_stell + required_gap:

            return True 
        
    # für den Fall, dass kein Fahrweg, weder oberhalb noch unterhalb der Stellfläche, vorhanden ist.
    # in diesem Fall muss sowohl ein Fahrweg am unteren Rand als auch am oberen Rand der Stellfläche hinzugefügt werden.
    if rect_type == 'block' and not fahrweg_unten_anliegend and not fahrweg_oben_anliegend:
        if free_w >= breite_stell and free_h >= hoehe_stell + 2*required_gap:

            return True 
        
    # für den Fall, dass ein zusätzlicher Fahrweg unterhalb der 'block'-Stellfläche hinzugefügt werden muss.
    if rect_type == 'block' and fahrweg_oben_anliegend and not fahrweg_unten_anliegend:
        if free_w >= breite_stell and free_h >= hoehe_stell + required_gap:

            return True 
        
    # für den Fall, dass ein zusätzlicher Fahrweg oberhalb der 'block'-Stellfläche hinzugefügt werden muss.
    if rect_type == 'block' and fahrweg_unten_anliegend and not fahrweg_oben_anliegend:
        if free_w >= breite_stell and free_h >= hoehe_stell + required_gap:

            return True 
        
    return False 
'''

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




# Version vor BugFix aus test_danial.





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

    # Prüfen, ob bin_packing überhaupt Erfolg hatte
    if not isinstance(result, tuple):
        raise ValueError("bin_packing hat keine gültigen Rückgabewerte geliefert!")

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

    return platzierte_stellflaechen, fahrwege, final_width, final_length, available_spaces


def bin_packing(session,ergebnisse_gesamt,breite_behaelter=None,hoehe_behaelter=None):
    # Liste der zu platzierenden Parkflächen 
    liste = []

    # Berechnen der Flächeninhalte aller Stellplätze 
    flaeche_ohne_fahrwege = 0
    for key, value in ergebnisse_gesamt.items():
        fleache = value["Fläche"]
        flaeche_ohne_fahrwege += fleache
    
    # Vorprüfung nach Mundt:
    # prüfen, ob Flächeninhalt des Behälters ausreicht für Flächen der Stellplätze (exklusive der Fahrwege)
    if breite_behaelter is not None and hoehe_behaelter is not None:
        behaelter_flaeche = breite_behaelter*hoehe_behaelter
        if behaelter_flaeche < flaeche_ohne_fahrwege:
            print("Die Übergebene Parkfläche reicht nicht vom Flächeninhalt, um alle Stellplätze zu plazieren.")
            return None 

    # Falls keine Depotfläche vorgegeben ist, wird hier eine quadratische Fläche erzeugt 
    if breite_behaelter is None and hoehe_behaelter is None:     
        breite_behaelter = math.ceil(math.sqrt(flaeche_ohne_fahrwege))+400
        hoehe_behaelter =  math.ceil(math.sqrt(flaeche_ohne_fahrwege))+400
        print("Es wurde eine ausreichend große und quadratische Parkfläche erzeugt")

    
    for key, value in ergebnisse_gesamt.items():
        vehicle_type = value["VehicleType"]
        block_parking_slots = value["Block Parkplätze"]
        direct_parking_slots = value["Direct Parkplätze"]
        
        # Anzahl der Reihen in Blockabstellung ermitteln
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

        '''
        # Falls eine Extraline existiert verringert sich der Flächeninhalt 
        if extra_line_length > 0:                                  
            verlust = standard_block_length - extra_line_length
            verlust_flaeche = verlust*(x*z)
            flaeche_block = (flaeche_block[0],  (block_hohe*block_breite) - verlust_flaeche)
        '''
        # Höhe und Breite der Stellfläche für die Directparkplätze des VehicleTypes
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
    stellplaetze_sortiert = sorted(liste, key = lambda r: (-r[1],-r[2])) 

    # zweite Vorprüfung nach Mundt:
    # prüfen, ob eine der Seitenlängen der Stellplätze größer als die Seitenlängen der Parkfläche ist.
    max_breite = max(width[1] for width in stellplaetze_sortiert)
    max_hoehe = max(length[2] for length in stellplaetze_sortiert)
    if max_breite > breite_behaelter:
        print("Die Breite einer Stellfläche übersteigt die Breite der Parkfläche")
        return None 
    if max_hoehe > hoehe_behaelter:
        print("Die Höhe einer Stellfläche übersteigt die Höhe der Parkfläche")
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
        # falls ein Stellplatz nicht nicht platziert werden kann bricht die gesamte Funktion ab
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
                # wenn ein ausreichend großer Fahrweg existiert, kann die Stellfläche ohne zusätzlichen Fahrweg platziert werden.
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
    hoehe_stell_gleich_hoehe_park = False 

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
            
            if fahrweg_oben_anliegend == True and fahrweg_unten_anliegend == True:
                # Prüfen, ob die Höhe der Stellflächen der Höhe der Parkfläche entspricht.
                # wichtig für den Fall, wenn sowohl oberhalb als auch unterhalb der Stellfläche ein Fahrweg liegt.
                # es muss weder ober- noch unterhalb der Stellfläche ein Fahrweg hinzugefügt werden. 
                if y+ hoehe_stell == y + free_h:
                    hoehe_stell_gleich_hoehe_park = True
                    break
        

    return {
        'Fahrweg_links_anliegend': fahrweg_links_anliegend,
        'Fahrweg_unten_anliegend': fahrweg_unten_anliegend,
        'Fahrweg_oben_anliegend' : fahrweg_oben_anliegend,
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
    block_oben_unten = drivinglane_necessary['Block_oben_unten']

    # benötigte Fahrwegbreite ermitteln
    conflict_matrix = create_conflict_matrix()
    if not isblock:
        required_gap = conflict_matrix.get(('dsr', 'left'), 0)
    else:
        required_gap = conflict_matrix.get(('block', 'top'), 0) # vorausgesetzt, dass top und bottom-Gap gleich groß sind.

    # für den Fall, dass eine 'dsr'-Stellfläche oder 'block'-Stellfläche ohne zusätzlichen Fahrweg platziert werden kann
    if fahrweg_links_anliegend or block_oben_unten:
        stellplatz_und_fahrweg_kombination = (x, y, breite_stell, hoehe_stell)
        # 1) Platzierung der Stellfläche 
        platzierte_stellflaechen.append((vehicle_type, x , y ,breite_stell ,hoehe_stell, isblock))

        # 2) Liste der Parkflächen updaten
        new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination, available_spaces, best_fit_index)
        available_spaces.clear()
        available_spaces.extend(new_spaces)

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

     

    # für den Fall, dass ein Fahrweg sowohl oben als auch unten an der Parkfläche anliegt, die Höhe der 'block'-Stellfläche jedoch geringer ist als die der ausgewählten Parkfläche: 
    # in diesem Fall wird der untere bereits existierende Fahrweg genutzt und ein neuer oberer hinzugefügt.
    # für den Fall, dass unterhalb der Parkfläche ein Fahrweg anliegt
    if rect_type == 'block' and fahrweg_unten_anliegend or (rect_type == 'block' and fahrweg_unten_anliegend and fahrweg_oben_anliegend):
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


    # für den Fall, dass oberhalb der Parkfläche ein Fahrweg anliegt und unterhalb der Stellfläche ein Fahrweg hinzugefügt wird.
    if rect_type == 'block' and fahrweg_oben_anliegend:
        # 1) Neue Maße des Rechteckes, das aus Stellfläche und Fahrweg besteht und auf der Parkfläche platziert wird
        stellplatz_und_fahrweg_kombination = (x , y +(free_h - hoehe_stell - required_gap), breite_stell , hoehe_stell + required_gap)

        # 2) Liste der Parkflächen updaten
        new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination,available_spaces,best_fit_index)
        available_spaces.clear()
        available_spaces.extend(new_spaces)

        # 3) Platzierung der Stellfläche 
        platzierte_stellflaechen.append((vehicle_type, x , y +(free_h - hoehe_stell) ,breite_stell ,hoehe_stell , isblock))

        # 4) Platzierung des Fahrweges 
        fahrweg = (x, y + (free_h - hoehe_stell - required_gap), breite_stell, required_gap)
        fahrwege.append(fahrweg)

        return None 

        
    # für den Fall, dass kein Fahrweg an der Parkfläche anliegt und sowohl oberhalb als auch unterhalb der Stellfläche ein Fahrweg hinzugefügt wird.
    if rect_type == 'block' and not fahrweg_unten_anliegend and not fahrweg_oben_anliegend:
        # 1) Neue Maße des Rechteckes, das aus Stellfläche und Fahrwegen besteht und auf der Parkfläche platziert wird
        stellplatz_und_fahrweg_kombination = (x , y , breite_stell , hoehe_stell + 2*required_gap)

        # 2) Liste der Parkflächen updaten
        new_spaces = get_newfree_rectangles(stellplatz_und_fahrweg_kombination,available_spaces,best_fit_index)
        available_spaces.clear()
        available_spaces.extend(new_spaces)

        # 3) Platzierung der Stellfläche 
        platzierte_stellflaechen.append((vehicle_type, x , y + required_gap ,breite_stell ,hoehe_stell , isblock))

        # 4) Platzierung des Fahrweges 
        fahrweg_unten = (x, y , breite_stell, required_gap)
        fahrweg_oben = (x, y + required_gap + hoehe_stell, breite_stell, required_gap)
        fahrwege.append(fahrweg_unten)
        fahrwege.append(fahrweg_oben)

        return None 

       
    return None 


# Prüfen bezüglich des Koordinaten-Ursprungs:
def get_newfree_rectangles(stellplatz_und_fahrweg_kombination, available_spaces, best_fit_index, min_size=2):
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

def enough_space_for_vehicle_and_drivinglanes(stellplatz,parkflaeche,drivinglane_necessary):
    '''
    Funktion zum Prüfen, ob die zuvor ausgewälte Parkfläche, groß genug für Stellfläche inklusiive benötigter Fahrwege ist.
    '''
    # Entpacken 
    vehicle_type, breite_stell, hoehe_stell, isblock = stellplatz
    x, y, free_w, free_h = parkflaeche

    rect_type = 'block' if isblock else 'dsr'

    fahrweg_links_anliegend = drivinglane_necessary['Fahrweg_links_anliegend']
    fahrweg_unten_anliegend = drivinglane_necessary['Fahrweg_unten_anliegend']
    fahrweg_oben_anliegend = drivinglane_necessary['Fahrweg_oben_anliegend']
    block_oben_unten = drivinglane_necessary['Block_oben_unten']


    # benötigte Fahrwegbreite ermitteln
    conflict_matrix = create_conflict_matrix()
    if not isblock:
        required_gap = conflict_matrix.get((rect_type, 'left'), 0)
    else:
        required_gap = conflict_matrix.get((rect_type, 'top'), 0) # vorausgesetzt, dass top- und bottom-Gap gleich groß sind.


    # für den Fall, dass ein zusätzlicher Fahrweg links von der 'dsr'- Stellfläche hinzugefügt werden muss
    if rect_type == 'dsr' and not fahrweg_links_anliegend:
        if free_h >= hoehe_stell and free_w >= breite_stell + required_gap:

            return True 
        
    # für den Fall, dass kein Fahrweg, weder oberhalb noch unterhalb der Stellfläche, vorhanden ist.
    # in diesem Fall muss sowohl ein Fahrweg am unteren Rand als auch am oberen Rand der Stellfläche hinzugefügt werden.
    if rect_type == 'block' and not fahrweg_unten_anliegend and not fahrweg_oben_anliegend:
        if free_w >= breite_stell and free_h >= hoehe_stell + 2*required_gap:

            return True 
        
    # für den Fall, dass ein zusätzlicher Fahrweg unterhalb der 'block'-Stellfläche hinzugefügt werden muss.
    if rect_type == 'block' and fahrweg_oben_anliegend and not fahrweg_unten_anliegend:
        if free_w >= breite_stell and free_h >= hoehe_stell + required_gap:

            return True 
        
    # für den Fall, dass ein zusätzlicher Fahrweg oberhalb der 'block'-Stellfläche hinzugefügt werden muss.
    if rect_type == 'block' and fahrweg_unten_anliegend and not fahrweg_oben_anliegend:
        if free_w >= breite_stell and free_h >= hoehe_stell + required_gap:

            return True 
        
    return False 


def approximatly_equal(a,b,epsilon = 1e-6):
    return abs(a - b)< epsilon


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














    

