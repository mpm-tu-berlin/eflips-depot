from dataclasses import dataclass
from math import ceil
from typing import Dict, List
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
from sqlalchemy import func, and_

from eflips.depot import UnstableSimulationException, DelayedTripException

# TODO consider moving all capacity related methods into a separate file
try:
    from eflips.eval.output.prepare import power_and_occupancy
except ImportError:
    print(
        "ImportError: eflips.eval.output.prepare module not found. eflips-eval package is needed for evaluation."
    )


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
    standard_block_length: int = 6,
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
        amount_of_lines = area_capacity / standard_block_length

        # The Area-Length including driving-lanes for entering and exiting.
        parking_area_length = (
            standard_block_length * vehicle_length
            + (standard_block_length - 1) * front_to_back
            + line_area_top
            + line_area_bottom
        )

        # The Area-Width inculding a spacing of `side_by_side` for the two outermost rows.
        parking_area_width = (
            amount_of_lines * vehicle_width + (amount_of_lines + 1) * side_by_side
        )

        parking_area = parking_area_length * parking_area_width

    elif area.area_type is AreaType.DIRECT:
        # The Area-Width including driving-lanes to the left and to the right for entering and exiting.
        parking_area_width = (
            vehicle_length * math.sin(math.radians(45))
            + vehicle_width * math.sin(math.radians(45))
            + direct_area_left
            + direct_area_right
        )
        # The Area-Length including a spacing of `side_by_side` between each parkingslot.
        parking_area_length = (
            vehicle_length * math.sin(math.radians(45))
            + vehicle_width * math.sin(math.radians(45))
            + (area_capacity - 1) * vehicle_width / math.cos(math.radians(45))
            + (area_capacity - 1) * side_by_side * math.sin(math.radians(45))
        )

        parking_area = parking_area_length * parking_area_width

    else:
        raise NotImplementedError("This AreaType is not supported.")

    return parking_area


# ----------------------------------------------------------------
# Below are the utility functions for capacity estimation
def create_charging_area(
    session, scenario, depot, name, area_type, capacity, vehicle_type, processes
):
    if not isinstance(processes, list) or not processes:
        raise ValueError(
            "Der Parameter 'processes' muss eine nicht leere Liste mit Objekten sein."
        )

    area = Area(
        scenario=scenario,
        name=name,
        depot=depot,
        area_type=area_type,
        capacity=capacity,
        vehicle_type=vehicle_type,
    )
    session.add(area)

    for process in processes:
        area.processes.append(process)

    return area


# Function to query the necessary processes for the simulation
def necessary_processes_query(session, scenario, depot):
    plan = (
        session.query(Plan)
        .filter(Plan.scenario_id == scenario.id, Plan.id == depot.default_plan_id)
        .first()
    )

    # Query with name is not a good practice
    # clean = (
    #     session.query(Process)
    #     .filter(Process.scenario_id == scenario.id, Process.name == "Clean")
    #     .one()
    # )
    # charging = (
    #     session.query(Process)
    #     .filter(Process.scenario_id == scenario.id, Process.name == "Charging")
    #     .one()
    # )
    # standby_departure = (
    #     session.query(Process)
    #     .filter(
    #         Process.scenario_id == scenario.id, Process.name == "Standby Departure"
    #     )
    #     .one()
    # )

    # TODO this is also not a good practice. How/If we should distinguish between cleaning and shunting?
    list_of_processes = plan.processes
    clean = list_of_processes[1]
    charging = list_of_processes[3]
    standby_departure = list_of_processes[4]
    return plan, clean, charging, standby_departure


# Function to link the Assocs before each simulation
def associate_plan_with_processes(
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
    # Shuyao: this returns incorrect results because both charging and standby_dep events should be regarded.
    # Only charging (especially before applying smart charging) can be really short.
    raise ValueError("deprecated")

    result_by_area = {}

    # Ensure the charging_areas variable is a list so we can iterate uniformly.

    if not isinstance(charging_areas, list):
        charging_areas = [charging_areas]

    # Process each charging area individually.
    for charging_area in charging_areas:
        # Step 1: Load all relevant events for the current charging area
        charging_events = (
            session.query(Event)
            .filter(
                Event.scenario_id == charging_area.scenario_id,
                Event.area_id == charging_area.id,
                Event.event_type == EventType.CHARGING_DEPOT,
            )
            .all()
        )

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
                time_points.append((event.time_start, "start"))
                time_points.append((event.time_end, "end"))

            # Sort the time points
            time_points.sort()

            # Iterate over all time points to calculate the maximum number of simultaneous charging events.
            #
            # TODO Is that correct? considering using eflips.eval

            for time, point_type in time_points:
                if point_type == "start":
                    current_count += 1
                    cur_direct_peak = max(cur_direct_peak, current_count)
                elif point_type == "end":
                    current_count -= 1

        # Query the number of vehicles in the scenario for the given vehicle type associated with the charging area.
        vehicle_count_by_type = (
            session.query(func.count(Vehicle.id))
            .filter(
                Vehicle.vehicle_type_id == charging_area.vehicle_type_id,
                Vehicle.scenario_id == scenario.id,
            )
            .scalar()
        )

        # Get the vehicle type for the current charging area
        vehicle_type = (
            session.query(VehicleType)
            .filter(VehicleType.id == charging_area.vehicle_type_id)
            .first()
        )

        # Store the results for this charging area in the output dictionary.
        result_by_area[charging_area.name] = {
            "peak_usage": cur_direct_peak,
            "vehicle_count": vehicle_count_by_type,
            "vehicle_type": vehicle_type,
        }

    return result_by_area


# Function to determine the required rows of line-parking-spaces for current VehicleType
def calc_num_of_line_parking_spaces(
    session, peak_count, vehicle_type, standard_block_length
):
    # TODO i'll just understand as it returns the number of lines packed in the same area of a direct area with peak number of buses
    # Query length and width for current VehicleType
    # TODO Shuyao: I think we should use the maximum vehicle count to estimate line num, not the equivalent area.
    # In each loop the areas are compared.
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
        # Area calculated for the Direct-Area divided by the are of Line-parking-spaces = maximum number of Line-parking-spaces
        # TODO area, when every bus packed in direct area
        width = x * math.sin(math.radians(45)) + z * math.sin(math.radians(45))
        length = (
            x * math.sin(math.radians(45))
            + z * math.sin(math.radians(45))
            + (peak_count - 1) * z / math.cos(math.radians(45))
        )

        # TODO dont understand that
        max_line_buses = math.floor((width * length) / (x * z))

        # For given row length
        # How many rows for the amount of Line-parking-spaces
        max_row_count = int(max_line_buses / standard_block_length)

        # Is an additional Line-row needed?
        extra_line_length = 0
        # ChargingArea from AreaType Line with capacity: 1 not possible
        if max_line_buses % standard_block_length not in (1, 0):
            max_row_count += 1
            extra_line_length = max_line_buses % standard_block_length
            extra_line = True
            print(
                f"Es wird {max_row_count} Iterationen geben. Davon ist eine, eine Extra-Line mit der Kapazität von {extra_line_length} Parkplätzen"
            )
        else:
            extra_line = False
            max_row_count = max_row_count

            print(f"Es wird {max_row_count} Iterationen geben")

        return max_row_count, extra_line, extra_line_length
    else:
        print(f"Keine Länge oder Breite für VehicleType{vehicle_type} gefunden")
        return None


# Function to determine the required area for iteration i for current VehicleType
def calculate_area_demand(
    session,
    num_line_area,
    cur_direct_peak,
    extra_line,
    extra_line_length,
    max_line_count,
    vehicle_type,
    standard_block_length,
):
    """

    Calculate the area demand for a given vehicle type and configuration. The returned area is the total area of all line slots plus
    the area of a direct area with slots number equal to the peak number of vehicles in direct area.
    :param session:
    :param num_line_area:
    :param cur_direct_peak:
    :param extra_line:
    :param extra_line_length:
    :param max_line_count:
    :param vehicle_type:
    :param standard_block_length:
    :return:
    """
    # Query length and width for current VehicleType
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

    area = 0
    line_parking_slots = 0
    direct_parking_slots = 0
    simulation_with_extra_line = False

    # Check if a ExtraLine exists
    # Calculate the area for Line-parking-spaces
    # Determine the number of Line-parking-spaces
    if num_line_area == max_line_count and extra_line:
        area += (num_line_area - 1) * standard_block_length * (x * z)
        area += extra_line_length * (x * z)
        line_parking_slots = (
            num_line_area - 1
        ) * standard_block_length + extra_line_length
        simulation_with_extra_line = True
    else:
        area += (num_line_area * standard_block_length) * (x * z)
        line_parking_slots = num_line_area * standard_block_length

    # Calculate the area of Direct-parking-spaces
    # Determine the number of Direct-parking-spaces
    if cur_direct_peak > 0:
        width = x * math.sin(math.radians(45)) + z * math.sin(math.radians(45))
        length = (
            x * math.sin(math.radians(45))
            + z * math.sin(math.radians(45))
            + (cur_direct_peak - 1) * z / math.cos(math.radians(45))
        )

        area += width * length
        direct_parking_slots = cur_direct_peak
    elif cur_direct_peak == 0:
        area += 0
        cur_direct_peak = 0

    return (
        round(area, 2),
        line_parking_slots,
        direct_parking_slots,
        simulation_with_extra_line,
    )


def replace_charging_areas(
    session: "Session",
    scenario: Scenario,
    depot: Depot,
    rotations_by_vehicle_types: Dict[VehicleType, List[Rotation]],
    new_areas=None,
):
    """
    This function replaces the old charging areas with new ones for each vehicle type.
    :param session:
    :param scenario:
    :param depot:
    :param rotations_by_vehicle_types:
    :return:
    """

    areas_to_delete = (
        session.query(Area)
        .filter(Area.depot_id == depot.id)
        .filter(
            Area.processes.any(
                and_(Process.electric_power.isnot(None), Process.duration.is_(None))
            )
        )
        .all()
    )

    all_vehicle_types = list(rotations_by_vehicle_types.keys())

    if new_areas is None:
        for old_area in areas_to_delete:
            processes = old_area.processes
            vehicle_type_this_area = old_area.vehicle_type
            if vehicle_type_this_area in all_vehicle_types:
                new_area = Area(
                    scenario=scenario,
                    name=old_area.name,
                    depot=depot,
                    area_type=AreaType.DIRECT_ONESIDE,
                    capacity=len(rotations_by_vehicle_types[old_area.vehicle_type]),
                    vehicle_type=old_area.vehicle_type,
                )
                session.add(new_area)
                new_area.processes = processes

                all_vehicle_types.remove(vehicle_type_this_area)

            session.delete(old_area)
    else:
        # TODO for the replace, either this should be compatible with multiple new areas for one vehicle type
        #  (the mix of line and direct), or we should write another one. new_areas are placeholder new area configurations
        raise NotImplementedError(
            "The function replace_charging_areas is not implemented for the new_areas parameter."
        )

    session.flush()


# ----------------------------------------------------------
# Simulations
# ----------------------------------------------------------


def first_simulation_run(
    session: "Session",
    scenario: Scenario,
    depot: Depot,
    rotations_by_vehicle_types: Dict[VehicleType, List[Rotation]],
) -> Dict[Area, int]:
    """
    This function runs the first simulation run for a given depot and scenario.
    It clears the previous vehicle and event data, runs the simulation, and determines the peak usage of
    Direct-Parking-Spaces for each ChargingArea.
    :param session: The database session to use.
    :param scenario: The scenario to simulate.
    :param depot: The depot to simulate.
    :param rotations_by_vehicle_types: A dictionary mapping vehicle types to their respective rotations.
    :return: A dictionary mapping area names to their peak usage and vehicle count.

    """

    vehicle_types_this_depot = list(rotations_by_vehicle_types.keys())

    for vehicle_type in vehicle_types_this_depot:
        print(
            f"Vehicle type {vehicle_type.name} (ID {vehicle_type.id}) detected in depot {depot.name}."
        )

    # Clear previous vehicle and event data
    session.query(Rotation).filter(Rotation.scenario_id == scenario.id).update(
        {"vehicle_id": None}
    )
    session.query(Event).filter(Event.scenario == scenario).delete()
    session.query(Vehicle).filter(Vehicle.scenario == scenario).delete()

    # TODO temporarily assign a constant energy consumption to all vehicles
    vehicle_types = session.query(VehicleType).filter(
        VehicleType.scenario_id == scenario.id
    )
    for vehicle_type in vehicle_types:
        vehicle_type.consumption = 1.0

    # Run the simulation
    try:
        eflips.depot.api.simple_consumption_simulation(
            scenario, initialize_vehicles=True
        )

        # TODO the repetition period should be carefully chosen
        eflips.depot.api.simulate_scenario(scenario)
        session.flush()
        session.expire_all()
        eflips.depot.api.simple_consumption_simulation(
            scenario, initialize_vehicles=False
        )

    except AssertionError:
        print(
            "SoC of a vehicle is negative. This is not allowed. Please check the simulation configuration."
        )
        session.rollback()
        return None

    # TODO maybe it's not a good exception handling
    # Catch Unstable and delay here with roll-back handling, others with enforced exit for debugging
    except UnstableSimulationException as e:
        print(f"This depot configuration leads to an unstable simulation: {e}")
        session.rollback()
        return None
    except DelayedTripException as e:
        print(f"This depot configuration leads to delayed trips: {e}")
        session.rollback()
        return None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        session.rollback()
        return None

    # Determine peak usage of Direct-Parking-Spaces for each ChargingArea

    # TODO it seems that the charging_areas now in the session have different ids as they were created

    current_charging_areas = (
        session.query(Area)
        .filter(Area.depot_id == depot.id)
        .filter(
            Area.processes.any(
                and_(Process.electric_power.isnot(None), Process.duration.is_(None))
            )
        )
        .all()
    )

    result_by_area: Dict[Area, int] = {}
    for area in current_charging_areas:
        area_peak_occupancy = power_and_occupancy(area.id, session)["occupancy"].max()

        result_by_area[area] = int(area_peak_occupancy)

    # TODO: I want to "save" the database state here. I guess flush works...
    session.flush()

    return result_by_area


def simulations_loop(result_by_area, session, scenario, depot, standard_block_length):
    """
    This function runs the depot simulation in a loop, where a block parking line is added in each iteration.
    In the end, the parking configuration with the smallest area is chosen for each VehicleType.
    """

    # Check whether the provided results are not faulty
    if not result_by_area:
        print("Die übergebenen Ergebnisse sind fehlerhalft.")
        return None

    # List for the results of all VehicleTypes
    total_results = {}

    # Iteration over the results from the first simulation run for each VehicleType
    for area, area_peak_count in result_by_area.items():
        vehicle_type = area.vehicle_type
        vehicle_count_by_type = (
            session.query(Event.vehicle_id)
            .filter(
                Event.scenario_id == scenario.id,
                Event.vehicle_type_id == vehicle_type.id,
            )
            .distinct()
            .count()
        )

        print(f"Simulation für den Bus-Type{vehicle_type}")

        # List for the result of the current VehicleType
        results = []

        # Calculation of how many line parking spaces are still smaller than the required direct parking spaces for a VehicleType
        num_of_line_parking_spaces = calc_num_of_line_parking_spaces(
            session, area_peak_count, vehicle_type, standard_block_length
        )

        print(num_of_line_parking_spaces)
        if num_of_line_parking_spaces is None:
            print("Keine Werte für Breite oder Länge in VehicleType Objekt gefunden")
            return None
        else:
            (
                max_line_count,
                extra_line,
                extra_line_length,
            ) = num_of_line_parking_spaces

        # TODO for each loop:

        # Loop to determine the parking configuration with the smallest area for each VehicleType
        for num_line_area in range(
            1, max_line_count + 1
        ):  # Number of possible line parking rows
            try:
                if num_line_area == max_line_count and extra_line:  # TODO what is that?
                    create_charging_area(
                        session,
                        scenario,
                        depot,
                        "l_extra_" + str(vehicle_type.name),
                        AreaType.LINE,
                        extra_line_length,
                        vehicle_type,
                        area.processes,
                    )

                    for idx_line_area in range(num_line_area - 1):
                        create_charging_area(
                            session,
                            scenario,
                            depot,
                            "l_" + str(idx_line_area) + "_" + str(vehicle_type.name),
                            AreaType.LINE,
                            standard_block_length,
                            vehicle_type,
                            area.processes,
                        )

                else:
                    # Create Line Area with variable Lines
                    # TODO why one extra full line?
                    for idx_line_area in range(num_line_area):
                        create_charging_area(
                            session,
                            scenario,
                            depot,
                            "l_" + str(idx_line_area) + "_" + str(vehicle_type.name),
                            AreaType.LINE,
                            standard_block_length,
                            vehicle_type,
                            area.processes,
                        )

                # Simulation
                # Clear previous vehicle and event data
                session.query(Rotation).filter(
                    Rotation.scenario_id == scenario.id
                ).update({"vehicle_id": None})
                session.query(Event).filter(Event.scenario == scenario).delete()
                session.query(Vehicle).filter(Vehicle.scenario == scenario).delete()

                # TODO: temporarily assign a constant energy consumption to all vehicles
                # TODO here make the name different as the vehicle_type in the loop
                vehicle_types_for_consumption = session.query(VehicleType).filter(
                    VehicleType.scenario_id == scenario.id
                )
                for vt in vehicle_types_for_consumption:
                    vt.consumption = 1.0

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
                    f"Iteration {num_line_area}: Für Fahrzeugtyp{vehicle_type}, Simulation fehlgeschlagen - Delay aufgetreten"
                )
                session.rollback()
                continue

            except Exception as e:
                print(
                    f"Iteration:{num_line_area} Ein unerwarteter Fehler ist aufgetreten: {e}"
                )
                session.rollback()
                continue
            else:
                print(
                    f"Iteration:{num_line_area} Keine Fehler bei der Simulation aufgetreten."
                )

            # Vehicle count for the current VehicleType
            vehicle_count = (
                session.query(Vehicle)
                .filter(Vehicle.vehicle_type == vehicle_type)
                .count()
            )

            # Check whether an additional vehicle demand has arisen
            if vehicle_count > vehicle_count_by_type:
                print(
                    f"Iteration:{num_line_area}  Für die Depotauslegung gab es einen Fahrzeugmehrbedarf. Es wurden insgesamt {vehicle_count} Fahrzeuge benötigt."
                )
                session.rollback()
                continue

            # Determine peak usage of direct parking spaces for the configuration with i * block_length block parking spaces:

            # TODO: I assume that the "area" in this loop, passed by the first_simulation_run()
            # is the same as the one in the current database state. If not, we need to query it again.
            try:
                cur_direct_peak = int(
                    power_and_occupancy(area.id, session)["occupancy"].max()
                )
            except ValueError:
                cur_direct_peak = 0

            # TODO for debugging
            # Plot the occupancy of direct area
            # from eflips.eval.output.visualize import (
            #     power_and_occupancy as plot_power_and_occupancy,
            # )
            #
            # data_direct = power_and_occupancy(area.id, session)
            # fig_direct = plot_power_and_occupancy(data_direct)
            # fig_direct.show()
            #
            # areas_line = session.query(Area.id).filter(
            #     Area.depot_id == depot.id,
            #     Area.area_type == AreaType.LINE,
            #     Area.processes.any(
            #         and_(
            #             Process.electric_power.isnot(None),
            #             Process.duration.is_(None),
            #         )
            #     ),
            # )
            #
            # data_line = power_and_occupancy(areas_line, session)
            # fig_line = plot_power_and_occupancy(data_line)
            # fig_line.show()
            #
            # all_area_ids = session.query(Area.id).filter(
            #     Area.depot_id == depot.id,
            #     Area.vehicle_type_id == vehicle_type.id,
            #     Area.processes.any(
            #         and_(
            #             Process.electric_power.isnot(None),
            #             Process.duration.is_(None),
            #         )
            #     ),
            # )

            # data_all = power_and_occupancy(all_area_ids, session)
            # fig_all = plot_power_and_occupancy(data_all)
            # fig_all.show()

            # Determine the area requirement in square meters for the current configuration

            # TODO the problem might be that the direct area is actually preferred.
            (
                demanded_area,
                line_parking_slots,
                direct_parking_slots,
                simulation_with_extra_line,
            ) = calculate_area_demand(
                session,
                num_line_area,
                cur_direct_peak,
                extra_line,
                extra_line_length,
                max_line_count,
                vehicle_type,
                standard_block_length,
            )

            # Store the results of this iteration for the selected VehicleType
            zeile = {
                "VehicleType": vehicle_type,
                "Demanded Area": demanded_area,
                "Line Parking Slots": line_parking_slots,
                "Given Line Length": standard_block_length,
                "Direct Parking Slots": direct_parking_slots,
                "Vehicle Count": vehicle_count,
                "Simulation with ExtraLine": simulation_with_extra_line,
                "ExtraLine Length": extra_line_length,
                "Iteration": num_line_area,
            }

            results.append(zeile)

            session.rollback()

        if not results:
            print(f"Keine Ergebnisse für {vehicle_type} gefunden")
            continue
        else:
            min_area_depot_configuration = min(
                results, key=lambda x: x["Demanded Area"]
            )

        # Store the configuration with the smallest area for the selected VehicleType
        total_results[f"VehicleType{vehicle_type}"] = min_area_depot_configuration

    return total_results


# Above are the utility functions for
# ----------------------------------------------------------


def capacity_estimation(
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    # spacing_params: DrivewayAndSpacing = DrivewayAndSpacing(),
    standard_block_length: int = 6,
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
    raise ValueError("deprecated!")
    # ----------------------------------------------------------
    # Helper Functions
    # ----------------------------------------------------------

    # Creates a new charging area (Area object) and associates the specified processes with it.

    # ----------------------------------------------------------
    # Function Calls
    # ----------------------------------------------------------

    depots = session.query(Depot).filter(Depot.scenario_id == scenario.id).all()
    capacity_estimates: Dict[Depot, Dict[VehicleType, CapacityEstimate]] = {}

    for depot in depots:
        try:
            result_by_area = first_simulation_run(session, scenario, depot)
        # If no VehicleTypes exist in the current scenario, abort the entire process.
        except ValueError as e:
            if (
                str(e)
                == "In dem aktuellen Scenario befinden sich keine VehicleType Objekte."
            ):
                print(f"Abbruch: {e}")
                return None
            else:
                continue

        if result_by_area is None:
            continue

        total_results = simulations_loop(
            result_by_area, session, scenario, depot, standard_block_length
        )
        if total_results is None:
            continue

        # optimal_simulation(total_results, session, scenario,depot)

        depot_estimates: Dict[VehicleType, CapacityEstimate] = {}
        for key, result in total_results.items():
            vehicle_type = result["VehicleType"]
            estimate = CapacityEstimate(
                line_peak_util=result["Line Parking Slots"],
                line_length=result["Given Line Length"],
                direct_count=result["Direct Parking Slots"],
                area_square_meters=result["Area"],
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

    # Iterate over each depot in the dictionary
    for depot, vehicle_estimates in capacity_estimates.items():
        # Optional: Prüfen, ob das Depot zum übergebenen Scenario gehört
        if depot.scenario_id != scenario.id:
            continue

        # Für jeden VehicleType im aktuellen Depot
        for vehicle_type, cap_est in vehicle_estimates.items():
            # Berechne die Anzahl der anzulegenden LINE Areas
            num_line_areas = (
                cap_est.line_count
            )  # line_count = ceil(line_peak_util / line_length)
            for i in range(num_line_areas):
                line_area = Area(
                    scenario_id=scenario.id,
                    depot_id=depot.id,
                    vehicle_type_id=vehicle_type.id,
                    area_type=AreaType.LINE,
                    capacity=cap_est.line_length,
                    name=f"Line Area {i+1} for {vehicle_type.name} at {depot.name}",
                    name_short=f"LINE-{vehicle_type.name[:5]}-{i+1}",
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
                    area_type=AreaType.DIRECT_ONESIDE,
                    capacity=cap_est.direct_count,
                    name=f"Direct Area for {vehicle_type.name} at {depot.name}",
                    name_short=f"DIRECT-{vehicle_type.name[:5]}",
                )
                session.add(direct_area)
                direct_area.processes.append(charging)
                direct_area.processes.append(standby_departure)

    session.commit()
