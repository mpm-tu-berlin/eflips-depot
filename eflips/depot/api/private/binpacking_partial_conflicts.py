import math
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import sqlalchemy.orm
from typing import Dict, List, Tuple
from matplotlib.offsetbox import AnchoredText
import seaborn as sns
from eflips.model import VehicleType, Area, AreaType, Depot, Process
import eflips.depot.api

"""
Call function:
best_possible_packing_parcial(session, depot)
Parameters:
session: given database session
depot: given depot-Object
Returns:
placed_areas, driving_lanes, final_width, final_length, available_spaces
Additionally, the function visualizes the depot layout.
"""

# ----------------------------------------------------------
# Bin-Packing with partial confilcts based on Patrik Mundt
# ----------------------------------------------------------


def create_conflict_matrix():
    """
    Defines the conflict matrix with distance dimensions for the different types of parking areas.
    The width of the inner driveway border is also defined here.
    According to Mundt's specifications, the values are set to 8 meters.
    """
    return {
        ("block", "top"): 8,
        ("block", "bottom"): 8,
        ("block", "left"): 0,
        ("block", "right"): 0,
        ("dsr", "left"): 8,
        ("dsr", "right"): 0,
        ("dsr", "top"): 0,
        ("dsr", "bottom"): 0,
        ("inner_edge"): 8,
    }


# Checks whether two floating-point numbers are approximately equal within a specified tolerance.
# Used during the merging process to compare floating-point coordinates.
def approximatly_equal(a, b, epsilon=1e-6):
    return abs(a - b) < epsilon


def best_possible_packing_parcial(session, depot, bin_width=None, bin_height=None):
    """
    Three-step approach:
    1) Simultaneously reduce width and height.
    2) Continue reducing only the width.
    3) Continue reducing only the height.
    """
    # Step size, the number of meters by which the side lengths of the parking area decrease per loop iteration.
    reduction_step = 5

    # 1) First call of bin_packing
    if bin_width is None and bin_height is None:
        result = bin_packing(session, depot)
    else:
        result = bin_packing(session, depot, bin_width, bin_height)

    if not isinstance(result, tuple):
        print("Warnung: bin_packing hat keine gültigen Rückgabewerte geliefert!")
        return None, None, None, None, None

    # Unpacking
    placed_areas, driving_lanes, box_width, box_length, available_spaces = result

    # Store the initial values
    beginning_width = box_width
    beginning_length = box_length

    # ----------------------------------------------------------
    # 1) Simultaneous reduction of width and height
    # ----------------------------------------------------------

    # Local "last success"
    last_success = result

    counter_sim = 0

    while True:
        # Next candidate
        candidate_w = box_width - reduction_step
        candidate_h = box_length - reduction_step

        # Termination criterion
        if candidate_w <= 0 or candidate_h <= 0:
            break

        # Call with smaller dimensions
        candidate_result = bin_packing(session, depot, candidate_w, candidate_h)

        if candidate_result is None or not isinstance(candidate_result, tuple):
            # Failure -> stick with (box_width, box_length)
            # and terminate
            break
        else:
            # Success:adopt candidate_w, candidate_h
            box_width = candidate_w
            box_length = candidate_h
            last_success = candidate_result
            counter_sim += 1

    # Here ends the last working (box_width, box_length)
    # => "Simultaneously" reduced.

    # ----------------------------------------------------------
    # 2) Continue reducing only the width
    # ----------------------------------------------------------
    # Start from the "simultaneous" final solution
    # (box_width, box_length) - this was the previous "last_success"
    # Extract once again

    placed_areas, driving_lanes, box_width, box_length, _a = last_success

    counter_w = 0

    while True:
        candidate_w = box_width - reduction_step
        if candidate_w <= 0:
            break

        candidate_result = bin_packing(session, depot, candidate_w, box_length)
        if candidate_result is None or not isinstance(candidate_result, tuple):
            # Failure
            break
        else:
            # Success
            box_width = candidate_w
            last_success = candidate_result
            counter_w += 1

    # ----------------------------------------------------------
    # 3) Continue reducing only the height
    # ----------------------------------------------------------
    # Start from the "width-only" final solution

    placed_areas, driving_lanes, box_width, box_length, _a = last_success

    counter_h = 0

    while True:
        candidate_h = box_length - reduction_step
        if candidate_h <= 0:
            break

        candidate_result = bin_packing(session, depot, box_width, candidate_h)
        if candidate_result is None or not isinstance(candidate_result, tuple):
            # Failure
            break
        else:
            # Success
            box_length = candidate_h
            last_success = candidate_result
            counter_h += 1

    # ----------------------------------------------------------
    # Unpack and output the last successful result "last_success"
    # ----------------------------------------------------------
    (
        placed_areas,
        driving_lanes,
        final_width,
        final_length,
        available_spaces,
    ) = last_success

    print("Ergebnis:")
    print(
        f"Die Parkfläche wurde {counter_sim} Mal simultan um {reduction_step}x{reduction_step} reduziert"
    )
    print(
        f"Anschließend wurde die Breite um weitere {counter_w} Mal um {reduction_step} reduziert"
    )
    print(
        f"Abschließend wurde die Höhe um weitere {counter_h} Mal um {reduction_step} reduziert"
    )
    print(f"Ursprüngliche Breite x Länge: {beginning_width} x {beginning_length}")
    print(f"Endgültige Breite x Länge   : {final_width} x {final_length}")
    print(f"Parkfläche: {final_width * final_length} Quadratmeter")

    visualize_placements(
        placed_areas, driving_lanes, final_width, final_length, session
    )

    return placed_areas, driving_lanes, final_width, final_length, available_spaces


def retrieve_depot_capacities(
    session: sqlalchemy.orm.session.Session, depot: Depot
) -> Dict[VehicleType, Dict[str, int]]:
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
    charging_process = (
        session.query(Process)
        .filter(Process.name == "Charging", Process.scenario_id == depot.scenario_id)
        .one_or_none()
    )

    if charging_process is None:
        raise ValueError("Kein Charging-Prozess für das Scenario des Depots gefunden.")

    # Retrieve all areas of the depot that are linked to the Charging process
    areas = (
        session.query(Area)
        .join(Area.processes)
        .filter(Area.depot_id == depot.id, Process.id == charging_process.id)
        .all()
    )

    results: Dict[VehicleType, Dict[str, int]] = {}

    for area in areas:
        vehicletype = area.vehicle_type
        if vehicletype not in results:
            results[vehicletype] = {
                "line_capacity_sum": 0,
                "direct_capacity": 0,
                "line_length": 0,
            }
        # Distinguish areas based on their area_type
        if area.area_type == AreaType.LINE:
            results[vehicletype]["line_capacity_sum"] += area.capacity
            # It is assumed that all LINE areas share the same line_length value.
            # If not yet set, it will be assigned.
            if results[vehicletype]["line_length"] == 0:
                results[vehicletype]["line_length"] = area.capacity
        elif area.area_type == AreaType.DIRECT_ONESIDE:
            results[vehicletype][
                "direct_capacity"
            ] = (
                area.capacity
            )  # It is assumed that there is only one DIRECT area per VehicleType.

    return results


def bin_packing(session, depot, bin_width=None, bin_height=None):
    # List of parking areas to be placed
    parking_areas = []

    # Get a dictionary of capacity data per VehicleType for the depot,
    # including line capacity, direct capacity, and line length (from LINE areas linked to the Charging process)
    dict_results = retrieve_depot_capacities(session, depot)

    # Create the parking-slots as rectangles
    for vehicle_type, estimate in dict_results.items():
        vehicle_type = vehicle_type
        block_parking_slots = estimate["line_capacity_sum"]
        direct_parking_slots = estimate["direct_capacity"]
        standard_block_length = estimate["line_length"]

        # Check if an extra line exists
        extra_line_length = block_parking_slots % standard_block_length
        block_lines = block_parking_slots // standard_block_length
        if extra_line_length > 0:
            block_lines += 1

        # Query and store the length and width of the VehicleType
        x = (
            session.query(VehicleType.length)
            .filter(VehicleType.id == vehicle_type.id)
            .scalar()
        )  # länge
        z = (
            session.query(VehicleType.width)
            .filter(VehicleType.id == vehicle_type.id)
            .scalar()
        )  # breite

        # Height and width of the parking space for the block parking spaces of the VehicleType
        block_height = standard_block_length * x
        block_width = block_lines * z
        flaeche_block = (vehicle_type, block_height * block_width)
        is_block = True
        block_areas = (vehicle_type, block_width, block_height, is_block)

        # Height and width of the parking area for the direct parking spaces of the VehicleType
        direct_areas = None
        if direct_parking_slots > 0:
            width = x * math.sin(math.radians(45)) + z * math.sin(math.radians(45))
            height = (
                x * math.sin(math.radians(45))
                + z * math.sin(math.radians(45))
                + (direct_parking_slots - 1) * z / math.cos(math.radians(45))
            )

            direct_flaeche = (vehicle_type, width * height)
            direct_areas = (vehicle_type, width, height, not is_block)

        # If direct parking spaces exist, they are added to the list of parking spaces to be placed
        if direct_areas:
            parking_areas.extend([block_areas, direct_areas])
        else:
            parking_areas.append(block_areas)

    # Calculate the areas of all parking spaces
    area_without_dirveways = sum(
        width * height for _, width, height, _ in parking_areas
    )

    # Preliminary check according to Mundt:
    # Check whether the container's area is sufficient for the parking spaces' areas (excluding driveways)
    if bin_width is not None and bin_height is not None:
        bin_area = bin_width * bin_height
        if bin_area < area_without_dirveways:
            print(
                f"Fläche der Stellplätze:{area_without_dirveways}\nFläche der Parkfläche:{bin_area}\nDie Übergebene Parkfläche reicht nicht vom Flächeninhalt, um alle Stellplätze zu plazieren."
            )
            return None

    # According to Mundt's Best-Fit-Decreasing heuristic
    # Sort parking spaces in decreasing order, primarily by x and secondarily by y
    # areas_sorted = sorted(list, key=lambda r: (-r[1], -r[2]))
    # Custom new sorting:
    # Differentiates between the sorting of dsr parking spaces and block parking spaces
    # dsr: Sorted according to Mundt's heuristic, primarily by x and secondarily by y
    # block: Primarily by y and secondarily by x
    # DSR parking spaces come before block parking spaces
    areas_sorted = sorted(
        parking_areas,
        key=lambda r: (0, -r[1], -r[2]) if not r[3] else (1, -r[2], -r[1]),
    )

    # If no depot area is specified, a square area is generated here
    max_width = max(width[1] for width in areas_sorted)
    max_height = max(length[2] for length in areas_sorted)
    if bin_width is None and bin_height is None:
        bin_width = math.ceil(math.sqrt(area_without_dirveways) * 1.5)
        bin_height = math.ceil(max_height * 1.5)
        print("Es wurde eine ausreichend große Parkfläche erzeugt")

    # Second preliminary check according to Mundt:
    # Check if any of the parking space dimensions exceed the dimensions of the parking area.
    if max_width > bin_width:
        print("Die Breite einer Stellfläche übersteigt die Breite der Parkfläche")
        return None
    if max_height > bin_height:
        print(
            f"Die Höhe einer Stellfläche übersteigt die Höhe der Parkfläche\nDie maximale Höhe einer Stellfläche beträgt{max_height}\nDie Höhe der Parkfläche beträgt{bin_height}"
        )
        return None

    # Read the value for the inner driveway edge from the conflict matrix with distance dimensions
    conflict_matrix = create_conflict_matrix()
    inner_edge = conflict_matrix["inner_edge"]

    # Available space in the container, considering the driveway at the inner edge of the parking area
    available_spaces = [
        (
            inner_edge,
            inner_edge,
            bin_width - 2 * inner_edge,
            bin_height - 2 * inner_edge,
        )
    ]  # (x,y,breite,hoehe)
    placed_areas = []
    driveways = []

    # Driveway at the inner edge of the parking area
    left_edge = (0, 0, inner_edge, bin_height)
    upper_edge = (0, bin_height - inner_edge, bin_width, inner_edge)
    right_edge = (bin_width - inner_edge, 0, inner_edge, bin_height)
    lower_edge = (0, 0, bin_width, inner_edge)

    driveways.extend([left_edge, upper_edge, right_edge, lower_edge])

    # Placement function
    # Iterate over all parking spaces in sorted order
    for area in areas_sorted:
        # The function searches for a suitable parking area for the selected parking space
        # and places both the parking spaces and the associated driveways.
        # The lists 'available_spaces', 'placed_parking_spaces', and 'driveways' are updated with each call.
        # If a parking space cannot be placed, the entire function terminates.
        # A non-placed parking space is a termination criterion.

        placed = placing_areas_on_parking_space(
            area, available_spaces, driveways, placed_areas
        )

        if not placed:
            print(
                f"{area} konnte nicht platziert werden. Abbruch des 'Bin Packing'-Algorithmus"
            )
            return None

    return placed_areas, driveways, bin_width, bin_height, available_spaces


def placing_areas_on_parking_space(area, available_spaces, driveways, placed_areas):
    # 1) Unpacking
    vehicle_type, area_w, area_h, is_line = area

    # 2) Iterate over the available containers in appropriate order
    for i, available in enumerate(available_spaces):
        x, y, free_w, free_h = available

        # Check if the parking space fits into the selected parking area
        if free_h >= area_h and free_w >= area_w:
            best_fit_index = i
            parking_area = available_spaces[i]

            # If a suitable container is found, check whether a driveway of sufficient size is adjacent to it.
            # For a 'dsr' parking space, a driveway of sufficient height to the left of the parking area is sufficient.
            # For a 'block' parking space, there must be a driveway both above and below the parking area.
            # Additionally, the height of the parking space must match that of the parking area so that no additional driveway is needed.
            drivinglane_necessary = driveway_check(area, parking_area, driveways)
            if (
                drivinglane_necessary["left_driveway"] == True
                or drivinglane_necessary["same_height"] == True
            ):
                # If a sufficiently large driveway exists, the parking space can be placed
                place_area_and_update(
                    area,
                    available_spaces,
                    best_fit_index,
                    driveways,
                    drivinglane_necessary,
                    placed_areas,
                )

                return True

            # In case no driveway is adjacent
            else:
                if enough_space_for_area_and_driveway(
                    area, parking_area, drivinglane_necessary
                ):
                    # If the parking area is large enough for the parking space including driveway, both are placed.
                    place_area_and_update(
                        area,
                        available_spaces,
                        best_fit_index,
                        driveways,
                        drivinglane_necessary,
                        placed_areas,
                    )
                    return True

                else:
                    # The selected parking area is not large enough to place the parking space including the required driveway.
                    # --> move on to the next parking area
                    continue
        else:
            # Parking space is too large for the selected parking area
            # --> move on to the next parking area
            continue

    # No area was suitable
    return False


def driveway_check(area, parking_area, driveways):
    # Unpacking
    x, y, free_w, free_h = parking_area
    vehicle_type, area_w, area_h, is_line = area

    # Flags
    left_driveway = False
    lower_driveway = False
    upper_driveway = False
    upper_driveway_used = False
    upper_driveway_not_used = False
    same_height = False

    rect_type = "line" if is_line else "dsr"

    # Determine the required driveway width
    conflict_matrix = create_conflict_matrix()
    if not is_line:
        required_gap = conflict_matrix.get((rect_type, "left"), 0)
    else:
        required_gap = conflict_matrix.get(
            (rect_type, "top"), 0
        )  # Assuming that top and bottom gaps are of equal size

    # Conditional check whether it is a 'dsr' parking space or a 'line' parking space
    # First check for 'dsr' parking spaces
    if not is_line:
        # Iterate through all driveways to check if a driveway is adjacent on the left
        for driveway in driveways:
            driveway_x, driveway_y, driveway_w, driveway_h = driveway

            # Check if the driveway is directly adjacent to the left of the parking space and if the height is sufficient
            if (
                (driveway_x + driveway_w == x)
                and (driveway_y <= y)
                and (driveway_y + driveway_h >= y + area_h)
            ):
                left_driveway = True
                break

    # Second check for 'line' parking spaces
    else:
        # Iteriere durch alle Fahrwege, um zu überprüfen, ob Fahrwege an der Stellfläche anliegen.
        for driveway in driveways:
            driveway_x, driveway_y, driveway_w, driveway_h = driveway

            # Check if the driveway is directly above the parking area and if the width is sufficient
            if (
                (driveway_y == y + free_h)
                and (driveway_x <= x)
                and (driveway_x + driveway_w >= x + area_w)
            ):
                upper_driveway = True

            # Check if a driveway is adjacent below the parking space and if the width is sufficient
            if (
                (driveway_y + driveway_h == y)
                and (driveway_x <= x)
                and (driveway_x + driveway_w >= x + area_w)
            ):
                lower_driveway = True

            # Check if the height of the parking space matches the height of the parking area.
            # This is important in cases where both above and below the parking space a driveway exists.
            # In such cases, no additional driveway needs to be added above or below the parking space.
            if upper_driveway and lower_driveway:
                if math.isclose(y + area_h, y + free_h, rel_tol=1e-6):
                    same_height = True
                    break

        # In case there is only a driveway above the parking area.
        # Check whether it will be used after placement according to BLF or not.
        if upper_driveway and not lower_driveway:
            if math.isclose(area_h + required_gap, free_h, rel_tol=1e-6):
                upper_driveway_used = True
            else:
                upper_driveway_not_used = True

    return {
        "left_driveway": left_driveway,
        "lower_driveway": lower_driveway,
        "upper_driveway": upper_driveway,
        "upper_driveway_used": upper_driveway_used,
        "upper_driveway_not_used": upper_driveway_not_used,
        "same_height": same_height,
    }


def place_area_and_update(
    area,
    available_spaces,
    best_fit_index,
    driveways,
    drivinglane_necessary,
    placed_areas,
):
    # Unpacking
    vehicle_type, area_w, area_h, is_line = area
    parkflaeche = available_spaces[best_fit_index]
    x, y, free_w, free_h = parkflaeche

    rect_type = "line" if is_line else "dsr"

    left_driveway = drivinglane_necessary["left_driveway"]
    lower_driveway = drivinglane_necessary["lower_driveway"]
    upper_driveway = drivinglane_necessary["upper_driveway"]
    upper_driveway_used = drivinglane_necessary["upper_driveway_used"]
    upper_driveway_not_used = drivinglane_necessary["upper_driveway_not_used"]
    same_height = drivinglane_necessary["same_height"]

    # Determine the required driveway width
    conflict_matrix = create_conflict_matrix()
    if not is_line:
        required_gap = conflict_matrix.get(("dsr", "left"), 0)
    else:
        required_gap = conflict_matrix.get(
            ("block", "top"), 0
        )  # Assuming that top and bottom gaps are of equal size

    # In case a 'dsr' or 'block' parking space can be placed without an additional driveway
    if left_driveway or same_height:
        # 1) Dimensions of the rectangle to be placed on the parking area
        area_and_driveway_combination = (x, y, area_w, area_h)

        # 2) Update the list of parking areas
        new_spaces = get_newfree_rectangles(
            area_and_driveway_combination, available_spaces, best_fit_index
        )
        available_spaces.clear()
        available_spaces.extend(new_spaces)

        # 3) Placement of the parking space
        placed_areas.append((vehicle_type, x, y, area_w, area_h, is_line))

        return None

    # In case an additional driveway needs to be added to the left of the 'dsr' parking space
    if rect_type == "dsr" and not left_driveway:
        # 1) New dimensions of the rectangle consisting of the parking space and the driveway to be placed on the parking area
        area_and_driveway_combination = (x, y, area_w + required_gap, area_h)

        # 2) Update the list of parking areas
        new_spaces = get_newfree_rectangles(
            area_and_driveway_combination, available_spaces, best_fit_index
        )
        available_spaces.clear()
        available_spaces.extend(new_spaces)

        # 3) Placement of the parking space considering the x-coordinate shift
        placed_areas.append(
            (vehicle_type, x + required_gap, y, area_w, area_h, is_line)
        )

        # 4) Placement of the driveway
        driveway = (x, y, required_gap, area_h)
        driveways.append(driveway)

        return None

    if rect_type == "line":
        # In case a driveway is adjacent both above and below the parking area,
        # but the height of the 'block' parking space is smaller than the selected parking area's height:
        # according to the Bottom-Left-Fill heuristic:
        # in this case, the lower existing driveway is used and a new upper driveway is added.
        if lower_driveway:
            # 1) New dimensions of the rectangle consisting of the parking space and the driveway to be placed on the parking area
            area_and_driveway_combination = (x, y, area_w, area_h + required_gap)

            # 2) Update the list of parking areas
            new_spaces = get_newfree_rectangles(
                area_and_driveway_combination, available_spaces, best_fit_index
            )
            available_spaces.clear()
            available_spaces.extend(new_spaces)

            # 3) Placement of the parking space
            placed_areas.append((vehicle_type, x, y, area_w, area_h, is_line))

            # 4) Placement of the driveway
            driveway = (x, y + area_h, area_w, required_gap)
            driveways.append(driveway)

            return None

        # In case a driveway is only adjacent above the parking area:
        # Case 1:
        # According to the BLF heuristic and placement of the parking space in the bottom-left corner of the parking area,
        # the height of the parking space including the lower driveway matches the height of the parking area,
        # and the driveway above is used
        elif upper_driveway_used:
            # 1) New dimensions of the rectangle consisting of the parking space and the driveway to be placed on the parking area
            area_and_driveway_combination = (x, y, area_w, area_h + required_gap)

            # 2) Update the list of parking areas
            new_spaces = get_newfree_rectangles(
                area_and_driveway_combination, available_spaces, best_fit_index
            )
            available_spaces.clear()
            available_spaces.extend(new_spaces)

            # 3) Placement of the parking space
            placed_areas.append(
                (vehicle_type, x, y + required_gap, area_w, area_h, is_line)
            )

            # 4) Placement of the driveway
            driveway = (x, y, area_w, required_gap)
            driveways.append(driveway)

            return None

        # In case only one driveway is adjacent above the parking area:
        # Case 2:
        # According to the BLF heuristic and placement of the parking space in the bottom-left corner of the parking area,
        # the height of the parking space including the lower driveway does not match the height of the parking area,
        # and the driveway above cannot be used
        elif upper_driveway_not_used:
            # 1) New dimensions of the rectangle consisting of the parking space and the driveway to be placed on the parking area
            area_and_driveway_combination = (x, y, area_w, area_h + 2 * required_gap)

            # 2) Update the list of parking areas
            new_spaces = get_newfree_rectangles(
                area_and_driveway_combination, available_spaces, best_fit_index
            )
            available_spaces.clear()
            available_spaces.extend(new_spaces)

            # 3) Placement of the parking space
            placed_areas.append(
                (vehicle_type, x, y + required_gap, area_w, area_h, is_line)
            )

            # 4) Placement of the driveway
            l_driveway = (x, y, area_w, required_gap)
            u_driveway = (x, y + required_gap + area_h, area_w, required_gap)
            driveways.append(l_driveway)
            driveways.append(u_driveway)

            return None

        # In case no driveway is adjacent to the parking area and a driveway is added both above and below the parking space
        elif not lower_driveway and not upper_driveway:
            # 1) New dimensions of the rectangle consisting of the parking space and the driveway to be placed on the parking area
            area_and_driveway_combination = (x, y, area_w, area_h + 2 * required_gap)

            # 2) Update the list of parking areas
            new_spaces = get_newfree_rectangles(
                area_and_driveway_combination, available_spaces, best_fit_index
            )
            available_spaces.clear()
            available_spaces.extend(new_spaces)

            # 3) Placement of the parking space
            placed_areas.append(
                (vehicle_type, x, y + required_gap, area_w, area_h, is_line)
            )

            # 4) Placement of the driveway
            l_driveway = (x, y, area_w, required_gap)
            u_driveway = (x, y + required_gap + area_h, area_w, required_gap)
            driveways.append(l_driveway)
            driveways.append(u_driveway)

            return None

    return None


def get_newfree_rectangles(
    area_and_driveway_combination, available_spaces, best_fit_index, min_size=2
):
    """
    Cuts the newly placed parking space (Stellplatz+Fahrwege) out of the free rectangle (Parkfläche)
    and returns a list of the remaining sub-rectangles.
    Args:
    Parkfläche: (x, y, width, height) of the available_spaces.
    Stellplatz+Fahrweg(e): (x, y, width, height) of the placed object.
    """

    # Unpacking
    parking_area = available_spaces[best_fit_index]
    free_x, free_y, free_w, free_h = parking_area
    area_x, area_y, area_w, area_h = area_and_driveway_combination

    # Calculate the bottom-right corner of both rectangles
    free_x_right = free_x + free_w
    free_y_low = free_y + free_h
    area_x_right = area_x + area_w
    area_y_low = area_y + area_h

    # List for the new free rectangles
    free_rects = []

    # Check whether there is any overlap at all
    if not (
        area_x_right <= free_x
        or area_x >= free_x_right
        or area_y_low <= free_y
        or area_y >= free_y_low
    ):
        # Top rectangle
        if area_y > free_y:
            neue_hoehe = area_y - free_y
            if neue_hoehe >= min_size:
                free_rects.append((free_x, free_y, free_w, neue_hoehe))

        # Left rectangle
        if area_x > free_x:
            neue_breite = area_x - free_x
            if neue_breite >= min_size:
                free_rects.append(
                    (free_x, area_y, neue_breite, min(area_h, free_y_low - area_y))
                )

        # Right rectangle
        if area_x_right < free_x_right:
            neue_breite = free_x_right - area_x_right
            if neue_breite >= min_size:
                free_rects.append(
                    (
                        area_x_right,
                        area_y,
                        neue_breite,
                        min(area_h, free_y_low - area_y),
                    )
                )

        # Bottom rectangle
        if area_y_low < free_y_low:
            neue_hoehe = free_y_low - area_y_low
            if neue_hoehe >= min_size:
                free_rects.append((free_x, area_y_low, free_w, neue_hoehe))

    available_spaces.extend(free_rects)

    # Remove the used area from the list of available areas in the container
    del available_spaces[best_fit_index]

    # Check the new list of free areas for possible merging of adjacent areas
    merge_rectangles_in_available_spaces(available_spaces)

    # Sort the free areas primarily by height (Y) and secondarily by width (X) according to Patrick Mundt
    available_spaces = sorted(available_spaces, key=lambda s: (s[3], s[2]))

    return available_spaces


def merge_rectangles_in_available_spaces(available_spaces):
    def can_merge(rect1, rect2):
        """
        Checks whether two rectangles can be merged.
        """
        x1, y1, w1, h1 = rect1
        x2, y2, w2, h2 = rect2

        # Check whether two free rectangles lie vertically next to each other
        if approximatly_equal(x1, x2) and approximatly_equal(w1, w2):
            if approximatly_equal(y1 + h1, y2) or approximatly_equal(y2 + h2, y1):
                return "vertical"

        # Check whether two free rectangles lie horizontally next to each other
        if approximatly_equal(y1, y2) and approximatly_equal(h1, h2):
            if approximatly_equal(x1 + w1, x2) or approximatly_equal(x2 + w2, x1):
                return "horizontal"

        return False

    def merge_rectangles(rect1, rect2, direction):
        """
        Merges two mergeable rectangles.
        """
        x1, y1, w1, h1 = rect1
        x2, y2, w2, h2 = rect2

        if direction == "horizontal":
            new_x = min(x1, x2)
            new_y = y1  # y1 == y2
            new_w = w1 + w2
            new_h = h1
        elif direction == "vertical":
            new_x = x1
            new_y = min(y1, y2)
            new_w = w1
            new_h = h1 + h2
        else:
            return None

        return (new_x, new_y, new_w, new_h)

    merged = True
    while merged:
        merged = False
        n = len(available_spaces)
        for i in range(n):
            rect1 = available_spaces[i]
            for j in range(i + 1, n):
                rect2 = available_spaces[j]
                direction = can_merge(rect1, rect2)
                if direction:
                    new_rect = merge_rectangles(rect1, rect2, direction)

                    # Remove the two free rectangles from the list and add the new merged rectangle
                    available_spaces.pop(j)
                    available_spaces.pop(i)
                    available_spaces.append(new_rect)
                    merged = True
                    break
            if merged:
                break

    return None


def enough_space_for_area_and_driveway(area, parking_area, drivinglane_necessary):
    """
    In the case where it has already been checked whether direct placement without an additional driveway is possible,
    and the result indicates that an additional driveway is required,
    this function checks whether the selected parking area is large enough for the parking space including the necessary driveways.
    """
    # Unpacking
    vehicle_type, area_w, area_h, is_line = area
    x, y, free_w, free_h = parking_area

    rect_type = "line" if is_line else "dsr"

    left_driveway = drivinglane_necessary["left_driveway"]
    lower_driveway = drivinglane_necessary["lower_driveway"]
    upper_driveway = drivinglane_necessary["upper_driveway"]
    upper_driveway_used = drivinglane_necessary["upper_driveway_used"]
    upper_driveway_not_used = drivinglane_necessary["upper_driveway_not_used"]

    # Determine the required driveway width
    conflict_matrix = create_conflict_matrix()
    if not is_line:
        required_gap = conflict_matrix.get((rect_type, "left"), 0)
    else:
        required_gap = conflict_matrix.get(
            (rect_type, "top"), 0
        )  # Assuming that top and bottom gaps are of equal size

    # In case an additional driveway needs to be added to the left of the 'dsr' parking space
    if not is_line:
        if not left_driveway:
            if free_h >= area_h and free_w >= area_w + required_gap:
                return True

    else:
        # In case no driveway is present, neither above nor below the parking space.
        # In this case, a driveway must be added both at the bottom and top edge of the parking space.
        if not upper_driveway and not lower_driveway:
            if free_w >= area_w and free_h >= area_h + 2 * required_gap:
                return True

        # In case only a driveway is present above, and it can be used with a BLF placement.
        if upper_driveway_used:
            if free_w >= area_w and math.isclose(
                free_h, area_h + required_gap, rel_tol=1e-6
            ):
                return True

        # In case only a driveway is present above, but it cannot be used, and two driveways need to be placed.
        if upper_driveway_not_used:
            if free_w >= area_w and free_h >= area_h + 2 * required_gap:
                return True

        # In case a driveway is present below the parking space/area, but one needs to be created above.
        # Also applies if an unused driveway is present above
        if lower_driveway:
            if free_w >= area_w and free_h >= area_h + required_gap:
                return True

    return False


def visualize_placements(
    placements, driveways, container_width, container_height, session
):
    """
    Visualize the placement of rectangles within a container.

    Parameters:
    - placements (list of tuples): List of placed rectangles with their coordinates (x, y, width, height).
    - container_width (float): Width of the container.
    - container_height (float): Height of the container.
    """
    angle = 315

    unique_vehicle_types = {entry[0] for entry in placements}
    count_vehicle_types = len(unique_vehicle_types)
    color_list = sns.color_palette("husl", count_vehicle_types)

    color_map = {}
    color_index = 0

    # Create a figure and an axis
    fig, ax = plt.subplots()
    ax.set_xlim(0, container_width)
    ax.set_ylim(0, container_height)
    ax.set_aspect("equal")
    ax.set_xlabel("Breite")
    ax.set_ylabel("Höhe")

    # Draw the container
    container = patches.Rectangle(
        (0, 0),
        container_width,
        container_height,
        linewidth=1,
        edgecolor="black",
        facecolor="none",
    )
    ax.add_patch(container)

    # Draw the placed parking space
    for i, (vehicle_type, x, y, width, height, isblock) in enumerate(placements):
        # Query and store the length and width of the VehicleType
        bus_length = (
            session.query(VehicleType.length)
            .filter(VehicleType.id == vehicle_type.id)
            .scalar()
        )
        bus_width = (
            session.query(VehicleType.width)
            .filter(VehicleType.id == vehicle_type.id)
            .scalar()
        )

        if vehicle_type not in color_map:
            color_map[vehicle_type] = color_list[color_index]
            color_index = color_index + 1

        color = color_map[vehicle_type]

        # Draw the frame of the parking space
        rect = patches.Rectangle(
            (x, y),
            width,
            height,
            linewidth=1,
            edgecolor="black",
            facecolor=color,
            alpha=0.3,
        )
        ax.add_patch(rect)

        # Draw vehicle-spots in their frame
        # Draw DSR-parking spaces
        if not isblock:
            direct = []
            # Determine the number of dsr-parking-spaces
            direct_parking_slots = 1 + (
                height - ((bus_length + bus_width) * math.sqrt(2) / 2)
            ) / (bus_width * math.sqrt(2))
            for i in range(round(direct_parking_slots)):
                if i == 0:
                    bus = patches.Rectangle(
                        (x, y + bus_width * math.cos(math.radians(45))),
                        bus_width,
                        bus_length,
                        angle=angle,
                        edgecolor="black",
                        fill=False,
                        linewidth=0.3,
                    )
                    ax.add_patch(bus)
                    direct.append(bus)
                else:
                    prev_bus = direct[-1]
                    new_y = prev_bus.get_y() + bus_width / (math.cos(math.radians(45)))
                    next_bus = patches.Rectangle(
                        (x, new_y),
                        bus_width,
                        bus_length,
                        angle=angle,
                        edgecolor="black",
                        fill=False,
                        linewidth=0.3,
                    )
                    ax.add_patch(next_bus)
                    direct.append(next_bus)
        else:
            # Draw line-parking spaces
            # Determine the number of vehicles parked in a row (this should usually correspond to standard_length_linerow)
            anzahl_in_reihe = round(height / bus_length)
            anzahl_der_reihen = round(width / bus_width)

            for reihe in range(anzahl_der_reihen):
                for bus in range(anzahl_in_reihe):
                    rect_x = x + reihe * bus_width
                    rect_y = y + bus * bus_length
                    rect = patches.Rectangle(
                        (rect_x, rect_y),
                        bus_width,
                        bus_length,
                        edgecolor="black",
                        fill=False,
                        linewidth=0.3,
                    )
                    ax.add_patch(rect)

        # Numbering of the parking areas
        ax.text(
            x + width / 2,
            y + height / 2,
            f"{i+1}",
            ha="center",
            va="center",
            fontsize=8,
            color="black",
        )

    for i, (x, y, width, height) in enumerate(driveways):
        rect = patches.Rectangle(
            (x, y),
            width,
            height,
            linewidth=1,
            edgecolor="black",
            facecolor="black",
            hatch="/",
            alpha=0.3,
        )
        ax.add_patch(rect)

    legend_ = []
    for vehicle_types, color in color_map.items():
        legend_.append(
            patches.Patch(facecolor=color, edgecolor="black", label=vehicle_types.name)
        )

    legend_.append(
        patches.Patch(
            facecolor="black", edgecolor="black", hatch="/", alpha=0.3, label="Fahrwege"
        )
    )
    ax.legend(
        handles=legend_,
        loc="center",
        bbox_to_anchor=(0.5, -0.25),
        title="Legende",
        ncol=2,
    )

    info_text = f"Breite: {container_width} m\nHöhe: {container_height} m\nFläche: {container_height*container_width} m²"
    info_box = AnchoredText(
        info_text,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.5),
        bbox_transform=ax.transAxes,
        frameon=True,
        prop=dict(size=10),
    )
    ax.add_artist(info_box)
    plt.subplots_adjust(bottom=0.7)

    # Axis labeling and display of the visualization
    plt.show()
