from dataclasses import dataclass
import math
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import sqlalchemy.orm
from typing import Dict,List, Tuple
from matplotlib.offsetbox import AnchoredText
import seaborn as sns
from eflips.model import VehicleType, Area, AreaType,Depot,Process
import eflips.depot.api
from rectpack import newPacker

"""
The DepotLayout dataclass encapsulates the functionality to determine and visualize 
an optimal depot layout. It calculates the best packing configuration for parking areas 
and generates an appropriate visualization.

Example usage:
    layout = DepotLayout(session=session, depot=depot, max_driving_lane_width=8)
    placed_areas, driving_lanes, final_width, final_height = layout.best_possible_packing()
    layout.visualize(placed_areas, driving_lanes, final_width, final_height)

Parameters:
    session (sqlalchemy.orm.session.Session): The database session to use for queries.
    depot: The depot object for which the layout is to be determined.
    max_driving_lane_width (int): The required width for the driving lane.

Returns (for best_possible_packing method):
    - placed_areas: The list of placed parking areas.
    - driving_lanes: The list of driving lane areas.
    - final_width (int): The optimized width of the parking area.
    - final_height (int): The optimized height of the parking area.

    Additionally, the visualize method creates a visual representation of the depot layout
"""
# ----------------------------------------------------------
# Bin-Packing with external solver: rectpack
# ----------------------------------------------------------

@dataclass
class DepotLayout:
    session: sqlalchemy.orm.session.Session
    depot: Depot
    max_driving_lane_width: int
    bin_width: int = None
    bin_height: int = None

    @property
    def driving_lane_width(self) -> int:
        """Calculates the actual utilized driving lane width (e.g., half of the maximum driving lane width)."""
        return math.ceil(self.max_driving_lane_width / 2)

    def retrieve_depot_capacities(self) -> Dict[VehicleType, Dict[str, int]]:
        """
        Retrieve capacity data for a given depot.

        Only areas linked to the "Charging" process are considered for the given depot.
        For each VehicleType, the following values are retrieved:
        - The total capacity of all LINE areas ('line_capacity_sum')
        - The capacity of the DIRECT area ('direct_capacity', if available)
        - The 'line_length' value from a LINE area (assuming all LINE areas share the same value)

        Parameters:
        - session: An open SQLAlchemy session.
        - depot: The depot object for which capacities should be retrieved.

        Returns:
        - dict: A dictionary mapping each VehicleType to another dictionary with the keys:
                'line_capacity_sum', 'direct_capacity', and 'line_length' (all as integers).
        """
        # Load the Charging process to consider only the relevant areas
        charging_process = self.session.query(Process).filter(Process.name == "Charging",Process.scenario_id == self.depot.scenario_id).one_or_none()
        
        if charging_process is None:
            raise ValueError("Kein Charging-Prozess für das Scenario des Depots gefunden.")
        
        # Retrieve all areas of the depot that are linked to the Charging process
        areas = (self.session.query(Area).join(Area.processes).filter(Area.depot_id == self.depot.id,Process.id == charging_process.id).all())
        
        results: Dict[VehicleType, Dict[str, int]] = {}
        
        for area in areas:
            vehicletype = area.vehicle_type  
            if vehicletype not in results:
                results[vehicletype] = {
                    "line_capacity_sum": 0,
                    "direct_capacity": 0,
                    "line_length": 0
                }
            # Distinguish areas based on their area_type
            if area.area_type == AreaType.LINE:
                results[vehicletype]["line_capacity_sum"] += area.capacity
                # It is assumed that all LINE areas share the same line_length value.
                # If not yet set, it will be assigned.
                if results[vehicletype]["line_length"] == 0:
                    results[vehicletype]["line_length"] = area.capacity
            elif area.area_type == AreaType.DIRECT_ONESIDE:
                results[vehicletype]["direct_capacity"] = area.capacity  # Es wird angenommen, dass es nur eine DIRECT Area pro VehicleType gibt.
        
        return results
    


    def create_rectangles(self) -> Tuple[List[Tuple], int]:
        """
        This function extracts the number and type of parking spaces from the provided dictionary
        and creates the corresponding rectangles that need to be placed.
        The rectangles consist of the parking area and the driveways that surround it.
        Parameters:
        - session: The database session used to query vehicle type dimensions.
        - dict_results: A dictionary containing the number and type of parking spaces for each vehicle type.
        - standard_length_linerow: The standard number of parking spaces in one row (used to determine block layout).
        """
        areas_withframe = []

        # Get a dictionary of capacity data per VehicleType for the depot,
        # including line capacity, direct capacity, and line length (from LINE areas linked to the Charging process)
        dict_results = self.retrieve_depot_capacities()

        for vehicle_type, estimate in dict_results.items():
            vehicle_type = vehicle_type
            line_parking_slots = estimate["line_capacity_sum"]
            direct_parking_slots = estimate["direct_capacity"]
            standard_length_linerow = estimate["line_length"]

            # Query and store the length and width of the VehicleType from the database
            bus_length = self.session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() 
            bus_width = self.session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() 
            if bus_length is None or bus_width is None:
                raise ValueError(f"Keine Länge für Fahrzeugtyp {vehicle_type} gefunden.")
            
            is_line = True 

            # --- LINE-AREA ---
            if line_parking_slots > 0:
                # Determine the number of rows in Line parking arrangement
                line_rows = math.ceil(line_parking_slots / standard_length_linerow)

                # Height and width of the parking space for the line parking area of the VehicleType
                # Add the created line parking area to the list of all parking areas to be placed
                area_height = standard_length_linerow * bus_length
                area_width = line_rows * bus_width
                flaeche_block = (vehicle_type, area_height * area_width)
                line_area = (vehicle_type, area_width, area_height, is_line)
                # Line parking area with surrounding driveway
                line_area_withframe = (vehicle_type, area_width + self.max_driving_lane_width, area_height + self.max_driving_lane_width, is_line)

                areas_withframe.append(line_area_withframe)
        


            # --- DIRECT-Area ---
            if direct_parking_slots > 0:
                # Height and width of the parking space for the direct parking spaces of the VehicleType
                sin_45 = math.sin(math.radians(45))
                cos_45 = math.cos(math.radians(45))
                width_of_direct_area = bus_length * sin_45 + bus_width * sin_45
                length_of_direct_area = bus_length * sin_45 + bus_width * sin_45 + (direct_parking_slots-1) * bus_width/cos_45
                direct_flaeche = (vehicle_type,width_of_direct_area*length_of_direct_area)
                direct_area = (vehicle_type, width_of_direct_area, length_of_direct_area, not is_line)
                # Direct parking area with surrounding driveway
                direct_area_with_frame = (vehicle_type, width_of_direct_area+ self.max_driving_lane_width, length_of_direct_area + self.max_driving_lane_width, not is_line)

                areas_withframe.append(direct_area_with_frame)
        
        return  areas_withframe, standard_length_linerow
    

    
    def create_bin(self, areas_withframe: List[Tuple], candidate_w: int = None, candidate_h: int = None) -> Tuple[List[Tuple], int, int]:
        """
        This function creates the bin in which the generated rectangles (including surrounding driveways) are to be placed.
        If no external dimensions for the bin are provided, a sufficiently large square bin is automatically generated.
        Additionally, driveways along the inner edges of the bin are created.

        Parameters:
        - areas_withframe: List of rectangles to be placed, each including surrounding driveway space.
        - bin_height (optional): The height of the bin. If None, it will be calculated automatically.
        - bin_width (optional): The width of the bin. If None, it will be calculated automatically.
        """

        dlw = self.driving_lane_width

        # Bestimme die outer‑Dimensionen:
        if candidate_w is not None and candidate_h is not None:
            outer_width = candidate_w
            outer_height = candidate_h
        else:
            area_in_square_meters = sum(width * length for _, width, length, _ in areas_withframe)
            if self.bin_width is None or self.bin_height is None:
                max_height_val = max(item[2] for item in areas_withframe)
                self.bin_width = math.ceil(math.sqrt(area_in_square_meters) * 1.4)
                self.bin_height = math.ceil(max_height_val * 1.5)
                print("Es wurde eine Parkfläche von ausreichender Größe erzeugt")
            outer_width = self.bin_width
            outer_height = self.bin_height

        '''
        # Preliminary check:
        # Check whether the container's area is sufficient for the total area of all parking spaces (including driveways)
        if self.bin_width is not None and self.bin_height is not None:
            bin_area = self.bin_width * self.bin_height
            if bin_area < area_in_square_meters:
               raise ValueError("Die Übergebene Parkfläche reicht nicht vom Flächeninhalt, um alle Stellplätze zu plazieren.")
        '''


        # Second preliminary check according to Mundt:
        # Check if any of the parking space dimensions exceed the dimensions of the parking area.
        max_width = max(item[1] for item in areas_withframe)
        max_height_val = max(item[2] for item in areas_withframe)
        if max_width > outer_width:
            raise ValueError("Die Breite einer Stellfläche übersteigt die Breite der Parkfläche")
        if max_height_val > outer_height:
            raise ValueError("Die Höhe einer Stellfläche übersteigt die Höhe der Parkfläche")


        # Driveway border of the parking area for visualization
        driveways = []
        left_edge   = (0, 0, dlw, outer_height)
        upper_edge  = (0, outer_height - dlw, outer_width, dlw)
        right_edge  = (outer_width - dlw, 0, dlw, outer_height)
        lower_edge  = (0, 0, outer_width, dlw)
        driveways.extend([left_edge, upper_edge, right_edge, lower_edge])
        

        return driveways, outer_width, outer_height
    

    
    def solver(self, areas_withframe: List[Tuple], bin_width: int, bin_height: int) -> List[Tuple]:
        """
        This function passes the list of rectangles to be placed, along with the bin dimensions, to the external solver.

        Parameters:
        - areas_withframe: List of rectangles (including driveway margins) to be placed.
        - bin_width: The width of the bin.
        - bin_height: The height of the bin.
        """

        bin_width = bin_width - self.max_driving_lane_width
        bin_height = bin_height - self.max_driving_lane_width

        packer = newPacker(rotation = False)

        # Add the rectangles to packing queue
        for vehicle_type, width, height, is_line in areas_withframe:
            packer.add_rect(width, height, rid=(vehicle_type,is_line))

        container = [(bin_width, bin_height)]

        # Add the bin where the rectangles will be placed
        for b in container:
            packer.add_bin(*b)

        # Start packing
        packer.pack()
        if len(packer.rect_list()) != len(areas_withframe):
            raise ValueError("Bin-Packing fehlgeschlagen: Es konnten nicht alle Stellplätze in dem Behälter platziert werden.")

        placed_areas = []
        for b,x,y,w,h,rid in packer.rect_list():
            placed_areas.append((x,y,w,h,rid))

        return placed_areas
    


    def visualize(self, placed_areas: List[Tuple], driveways: List[Tuple], bin_width: int, bin_height: int, save_path: str = None):

        dlw = self.driving_lane_width 

        # Angle of dsr-area
        angle = 315
        
        areas_without_drivinglane = []

        for area in placed_areas:
            # Unpacking
            x, y, width, height, rid = area 
            vehicle_type, is_line = rid

            # Determine the actual parking space without the driveway 
            areas_without_drivinglane.append((vehicle_type, x + 2*dlw, y + 2*dlw, width - self.max_driving_lane_width, height - self.max_driving_lane_width, is_line))


        unique_vehicle_types = {entry[0] for entry in areas_without_drivinglane}
        count_vehicle_types = len(unique_vehicle_types)    
        color_list = sns.color_palette("husl", count_vehicle_types)
        
        color_map = {}
        color_index = 0


        # Create a figure and an axis
        fig, ax = plt.subplots(figsize = (5,5))
        ax.set_xlim(0, bin_width)
        ax.set_ylim(0, bin_height)
        ax.set_aspect('equal')
        ax.set_xlabel("Breite")
        ax.set_ylabel("Höhe")
        
        # Draw the bin
        container = patches.Rectangle((0, 0),bin_width, bin_height, linewidth=1, edgecolor='black', facecolor='none')
        ax.add_patch(container)

        # Draw the inner driveway surrounding the parking area
        for i, (x,y,width,height) in enumerate(driveways):
            rect = patches.Rectangle((x,y), width , height,linewidth=1, edgecolor = 'black', facecolor= 'black', hatch = '/', alpha=0.3 )
            ax.add_patch(rect)

        # Draw the driveways of the parking spaces
        for ( x, y, breite, hoehe, _ )in placed_areas:
            driving_lane = patches.Rectangle((x+dlw, y+ dlw), breite, hoehe, linewidth=1, edgecolor='black', facecolor='black', alpha=0.3)
            ax.add_patch(driving_lane)

        # Draw the parking areas 
        for i, (vehicle_type, x, y, width, height,is_line) in enumerate(areas_without_drivinglane):

            # Query and store the length and width of the VehicleType
            bus_length = self.session.query(VehicleType.length).filter(VehicleType.id == vehicle_type.id).scalar() 
            bus_width = self.session.query(VehicleType.width).filter(VehicleType.id==vehicle_type.id).scalar() 

            if vehicle_type not in color_map:
                color_map[vehicle_type] = color_list[color_index]
                color_index = color_index +1

            color = color_map[vehicle_type]

            # Draw their rectangle frame first
            rect = patches.Rectangle((x, y), width, height, linewidth=1, edgecolor = 'black', facecolor= color, alpha=0.3)
            ax.add_patch(rect)
            
            # Draw the vehicles-spots in their frame 
            # DSR-parking-spaces
            if not is_line:
                direct = []
                # Determine the number of dsr-parking-spaces
                direct_parking_slots = math.ceil(1 + (height - ((bus_length + bus_width) * math.sqrt(2) / 2)) / (bus_width * math.sqrt(2)))
                for i in range(direct_parking_slots):
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
                # Draw line-parking spaces 
                # Determine the number of vehicles parked in a row (this should usually correspond to standard_length_linerow)
                bus_number_in_row = math.ceil(height/bus_length)
                number_of_rows = math.ceil(width/bus_width)

                for row in range(number_of_rows):
                    for bus in range(bus_number_in_row):
                        rect_x = x + row*bus_width
                        rect_y = y + bus* bus_length
                        rect = patches.Rectangle((rect_x,rect_y), bus_width, bus_length,edgecolor= 'black', fill = False, linewidth = 0.3)
                        ax.add_patch(rect)

            # Numbering of the parking areas
            ax.text(x + width / 2, y + height / 2, f"{i+1}", ha='center', va='center', fontsize=8, color='black')

        # Axis labeling and display of the visualization
        legend_ = []
        for vehicle_types, color in color_map.items():
            legend_.append(patches.Patch(facecolor= color , edgecolor= 'black',label = vehicle_types.name))
            
        legend_.append(patches.Patch(facecolor='black', alpha = 0.3, edgecolor='black', hatch = '/', label = "Fahrwege"))
        ax.legend(handles=legend_, loc='center', bbox_to_anchor=(0.5, -0.25), title="Legende", ncol=2)

        info_text = f"Breite: {bin_width} m\nHöhe: {bin_height} m\nFläche: {bin_width*bin_height} m²"
        info_box = AnchoredText(info_text,loc='lower center',bbox_to_anchor=(0.5, -0.5),bbox_transform=ax.transAxes,frameon=True,prop=dict(size=10))
        ax.add_artist(info_box)
        plt.subplots_adjust(bottom=0.7)
      
        # Save figure if save_path is provided, else show the plot.
        if save_path:
            plt.savefig(save_path)
        else:
            plt.show()


    def best_possible_packing(self) -> Tuple[List[Tuple], List[Tuple], int, int]:
        """
        Three-step approach:
        1) Simultaneously reduce width and height.
        2) Continue reducing only the width.
        3) Continue reducing only the height.
        """
        # Definition of: Reduction step size 
        reduction_step = 5

        areas_withframe, standard_length_linerow = self.create_rectangles()
        driveways, bin_width, bin_height = self.create_bin(areas_withframe)

        beginning_width = bin_width
        beginning_height = bin_height

        result = self.solver(areas_withframe,bin_width,bin_height)
        if not result:
            raise ValueError("Initiales Bin-Packing ist fehlgeschlagen.")
        
        last_success = (result, driveways, bin_width, bin_height)
        current_width, current_height = bin_width, bin_height


        # ----------------------------------------------------------
        # 1) Simultaneous reduction of width and height
        # ----------------------------------------------------------
        counter_sim = 0
        
        while True:
            # Next candidate
            candidate_w = current_width - reduction_step
            candidate_h = current_height - reduction_step

            # Termination criterion
            if candidate_w <= 0 or candidate_h <= 0:
                break

            try:
                driveways, bin_width, bin_height = self.create_bin(areas_withframe,candidate_w,candidate_h)
                candidate_result = self.solver(areas_withframe, bin_width, bin_height)
                current_width, current_height = bin_width, bin_height
                last_success = (candidate_result, driveways, current_width, current_height)
                counter_sim += 1
            # Failure -> stick with (current_width, current_height)
            except ValueError:
                break    

        # Here ends the last working (current_width, current_height)
        # => "Simultaneously" reduced.

        # ----------------------------------------------------------
        # 2) Continue reducing only the width
        # ----------------------------------------------------------
        # Start from the "simultaneous" final solution
        # (current_width, current_height) - this was the previous "last_success"
        # Extract once again

        placed_areas, driveways, current_width, current_height = last_success

        counter_w = 0

        while True:
            candidate_w = current_width - reduction_step
            if candidate_w <= 0:
                break
            
            try:
                driveways, bin_width, bin_height = self.create_bin(areas_withframe,candidate_w,current_height)
                candidate_result = self.solver(areas_withframe, bin_width, bin_height)
                current_width, current_height = bin_width, bin_height
                last_success = (candidate_result, driveways, current_width, current_height)
                counter_w += 1
            except ValueError:
                break
                
        # ----------------------------------------------------------
        # 3) Continue reducing only the height
        # ----------------------------------------------------------
        # Start from the "width-only" final solution

        placed_areas, driveways, current_width, current_height = last_success

        counter_h = 0

        while True:
            candidate_h = current_height - reduction_step
            if candidate_h <= 0:
                break
            try:
                driveways, bin_width, bin_height = self.create_bin(areas_withframe,current_width,candidate_h)
                candidate_result = self.solver(areas_withframe, bin_width, bin_height)
                current_width, current_height = bin_width, bin_height
                last_success = (candidate_result, driveways, current_width, current_height)
                counter_h += 1
            except ValueError:
                break
              
        # ----------------------------------------------------------
        # Unpack and output the last successful result "last_success"
        # ----------------------------------------------------------
        placed_areas, driveways,final_width,final_height = last_success

        print("Ergebnis:")
        print(f"Die Parkfläche wurde {counter_sim} Mal simultan um {reduction_step}x{reduction_step} reduziert")
        print(f"Anschließend wurde die Breite um weitere {counter_w} Mal um {reduction_step} reduziert")
        print(f"Abschließend wurde die Höhe um weitere {counter_h} Mal um {reduction_step} reduziert")
        print(f"Ursprüngliche Breite x Länge: {beginning_width} x {beginning_height}")
        print(f"Endgültige Breite x Länge   : {final_width} x {final_height}")
        print(f"Parkfläche: {final_width * final_height} Quadratmeter")

        return placed_areas, driveways, final_width, final_height
    