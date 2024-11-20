"""This package contains the private API for the depot-related functionality in eFLIPS."""
import math
from datetime import timedelta
from enum import Enum, auto
from typing import Dict, List, Tuple

import sqlalchemy.orm
from eflips.model import (
    Scenario,
    AssocPlanProcess,
    Area,
    AssocAreaProcess,
    Event,
    Process,
    Depot,
    Plan,
    AreaType,
    Rotation,
    Trip,
    Station,
    VehicleType,
)
from sqlalchemy.orm import Session


def delete_depots(scenario: Scenario, session: Session) -> None:
    """This function deletes all depot-related data from the database for a given scenario.

    Used before a new depot in this scenario is created.

    :param scenario: The scenario to be simulated
    :param session: The database session

    :return: None. The depot-related data will be deleted from the database.
    """

    # Delete assocs
    session.query(AssocPlanProcess).filter(
        AssocPlanProcess.scenario_id == scenario.id
    ).delete()
    list_of_area = session.query(Area).filter(Area.scenario_id == scenario.id).all()

    for area in list_of_area:
        session.query(AssocAreaProcess).filter(
            AssocAreaProcess.area_id == area.id
        ).delete()
        session.query(Event).filter(Event.area_id == area.id).delete()

    # delete processes
    session.query(Process).filter(Process.scenario_id == scenario.id).delete()

    # delete areas
    session.query(Area).filter(Area.scenario_id == scenario.id).delete()
    # delete depot
    session.query(Depot).filter(Depot.scenario_id == scenario.id).delete()
    # delete plan
    session.query(Plan).filter(Plan.scenario_id == scenario.id).delete()
    # delete assoc_plan_process


def depot_to_template(depot: Depot) -> Dict[str, str | Dict[str, str | int]]:
    """
    Converts the depot to a template for internal use in the simulation core.

    :return: A dict that can be consumed by eFLIPS-Depot.
    """
    # Initialize the template
    template = {
        "templatename_display": "",
        "general": {"depotID": "", "dispatch_strategy_name": ""},
        "resources": {},
        "resource_switches": {},
        "processes": {},
        "areas": {},
        "groups": {},
        "plans": {},
    }

    # Set up the general information
    template["templatename_display"] = depot.name
    template["general"]["depotID"] = str(depot.id)
    template["general"]["dispatch_strategy_name"] = "SMART"

    # Helper for adding processes to the template
    list_of_processes = []

    # Get dictionary of each area
    for area in depot.areas:
        area_name = str(area.id)
        template["areas"][area_name] = {
            "typename": (
                "LineArea" if area.area_type == AreaType.LINE else "DirectArea"
            ),
            "capacity": area.capacity,
            "available_processes": [str(process.id) for process in area.processes],
            "issink": False,
            "entry_filter": None,
        }

        # Fill in vehicle_filter.
        # If the vehicle type id is set, the area is only for this vehicle type
        if area.vehicle_type_id is not None:
            template["areas"][area_name]["entry_filter"] = {
                "filter_names": ["vehicle_type"],
                "vehicle_types": [str(area.vehicle_type_id)],
            }
        else:
            # If the vehicle type id is not set, the area is for all vehicle types
            template["areas"][area_name]["entry_filter"] = dict()

        for process in area.processes:
            # Add process into process list
            list_of_processes.append(
                process
            ) if process not in list_of_processes else None

            # Charging interfaces
            if process_type(process) == ProcessType.CHARGING:
                ci_per_area = []
                for i in range(area.capacity):
                    ID = "ci_" + str(len(template["resources"]))
                    template["resources"][ID] = {
                        "typename": "DepotChargingInterface",
                        "max_power": process.electric_power,
                    }
                    ci_per_area.append(ID)

                template["areas"][area_name]["charging_interfaces"] = ci_per_area

            # Set issink to True for departure areas
            if process_type(process) == ProcessType.STANDBY_DEPARTURE:
                template["areas"][area_name]["issink"] = True

    for process in list_of_processes:
        process_name = str(process.id)
        # Shared template for all processes
        template["processes"][process_name] = {
            "typename": "",  # Placeholder for initialization
            "dur": int(process.duration.total_seconds()) if process.duration else None,
            # True if this process will be executed for all vehicles. False if there are available vehicle filters
            "ismandatory": True,
            "vehicle_filter": {},
            # True if this process can be interrupted by a dispatch. False if it cannot be interrupted
            "cancellable_for_dispatch": process.dispatchable,
        }

        match process_type(process):
            case ProcessType.SERVICE:
                template["processes"][process_name]["typename"] = "Serve"

                # Fill in the worker_service
                service_capacity = sum([x.capacity for x in process.areas])

                template["processes"][process_name]["required_resources"] = [
                    "workers_service"
                ]
                template["resources"]["workers_service"] = {
                    "typename": "DepotResource",
                    "capacity": service_capacity,
                }

                if process.availability is not None and len(process.availability) > 0:
                    template["resource_switches"]["service_switch"] = {
                        "resource": "workers_service",
                        "breaks": [],
                        "preempt": process.preemptable
                        if process.preemptable is not None
                        else True,
                        # Strength 'full' means all workers can take a break at the same time
                        "strength": "full",
                        # Resume set to True means that the process will continue after the break
                        "resume": True,
                        # Priority -3 means this process has the highest priority
                        "priority": -3,
                    }

                    list_of_breaks = process._generate_break_intervals()
                    list_of_breaks_in_seconds = []

                    # Converting the time intervals into seconds

                    for time_interval in list_of_breaks:
                        start_time = time_interval[0]
                        end_time = time_interval[1]
                        start_time_in_seconds = (
                            start_time.hour * 3600
                            + start_time.minute * 60
                            + start_time.second
                        )

                        end_time_in_seconds = (
                            end_time.hour * 3600
                            + end_time.minute * 60
                            + end_time.second
                        )

                        list_of_breaks_in_seconds.append(
                            (start_time_in_seconds, end_time_in_seconds)
                        )

                    template["resource_switches"]["service_switch"][
                        "breaks"
                    ] = list_of_breaks_in_seconds

            case ProcessType.CHARGING:
                template["processes"][process_name]["typename"] = "Charge"
                del template["processes"][process_name]["dur"]

            case ProcessType.STANDBY | ProcessType.STANDBY_DEPARTURE:
                template["processes"][process_name]["typename"] = "Standby"
                template["processes"][process_name]["dur"] = 0

            case ProcessType.PRECONDITION:
                template["processes"][process_name]["typename"] = "Precondition"
                template["processes"][process_name]["dur"] = int(
                    process.duration.total_seconds()
                )
                template["processes"][process_name]["power"] = process.electric_power
            case _:
                raise ValueError(f"Invalid process type: {process_type(process).name}")

    # Initialize the default plan
    template["plans"]["default"] = {
        "typename": "DefaultActivityPlan",
        "locations": [],
    }
    # Groups
    for process in depot.default_plan.processes:
        group_name = str(process.name) + "_group"
        template["groups"][group_name] = {
            "typename": "AreaGroup",
            "stores": [str(area.id) for area in process.areas],
        }
        if process_type(process) == ProcessType.CHARGING:
            template["groups"][group_name]["typename"] = "ParkingAreaGroup"
            template["groups"][group_name]["parking_strategy_name"] = "FIRST"

        # Fill in locations of the plan
        template["plans"]["default"]["locations"].append(group_name)

    return template


def find_first_last_stop_for_rotation_id(
    rotation: Rotation, session: sqlalchemy.orm.session.Session
) -> Tuple[Station, Station, VehicleType]:
    """
    Identifies the first stop, last stop and vehicle type for a given rotation.

    :param rotation: An :class:`eflips.model.Rotation` object
    :param session: An SQLAlchemy session object to the database
    :return: A tuple of the first stop, last stop and vehicle type
    """

    first_stop = rotation.trips[0].route.departure_station
    last_stop = rotation.trips[-1].route.arrival_station
    vehicle_type = rotation.vehicle_type
    return first_stop, last_stop, vehicle_type


def group_rotations_by_start_end_stop(
    scenario_id: int,
    session: sqlalchemy.orm.session.Session,
) -> Dict[Tuple[Station, Station], Dict[VehicleType, List[Rotation]]]:
    """
    For a given scenario, create a list of rotations and group them by their start and end stops.

    :param session: An SQLAlchemy session object
    :return: A dictionary of rotations grouped by their start and end stops, with each group further grouped by vehicle
        type.
    """
    rotations = (
        session.query(Rotation)
        .filter(Rotation.scenario_id == scenario_id)
        .options(sqlalchemy.orm.joinedload(Rotation.trips).joinedload(Trip.route))
        .options(sqlalchemy.orm.joinedload(Rotation.vehicle_type))
    )
    grouped_rotations: Dict[
        Tuple[Station, Station], Dict[VehicleType, List[Rotation]]
    ] = {}
    for rotation in rotations:
        first_stop, last_stop, vehicle_type = find_first_last_stop_for_rotation_id(
            rotation, session
        )
        if (first_stop, last_stop) not in grouped_rotations:
            grouped_rotations[(first_stop, last_stop)] = {}
        if vehicle_type not in grouped_rotations[(first_stop, last_stop)]:
            grouped_rotations[(first_stop, last_stop)][vehicle_type] = []
        grouped_rotations[(first_stop, last_stop)][vehicle_type].append(rotation)

    return grouped_rotations


class ProcessType(Enum):
    """This class represents the types of a process in eFLIPS-Depot."""

    SERVICE = auto()
    """This process represents a bus service by workers.

    It does not require a charging_power and has a fixed
    duration.
    """
    CHARGING = auto()
    """This process represents a bus charging process.

    It requires a charging_power and has no fixed duration.
    """
    STANDBY = auto()
    """This process represents an arriving bus that is waiting for a service. It does not require a charging_power.

    and has no fixed duration.
    """
    STANDBY_DEPARTURE = auto()
    """This process represents a bus ready for departure.

    It does not require a charging_power and has no fixed
    duration.
    """
    PRECONDITION = auto()
    """This process represents a bus preconditioning process.

    It requires a charging_power and has a fixed duration.
    """


def process_type(p: Process) -> ProcessType:
    """
    The type of the process.

    See :class:`eflips.depot.api.input.ProcessType` for more information. Note that whether
    a process needs a resource or not depends on the type of the process.
    """
    if p.duration is not None and p.electric_power is None:
        return ProcessType.SERVICE
    elif p.duration is None and p.electric_power is not None:
        return ProcessType.CHARGING
    elif p.duration is not None and p.electric_power is not None:
        return ProcessType.PRECONDITION
    elif p.duration is None and p.electric_power is None:
        if p.dispatchable:
            return ProcessType.STANDBY_DEPARTURE
        else:
            return ProcessType.STANDBY
    else:
        raise ValueError("Invalid process type")


def generate_depot(
    capacity_of_areas: Dict[VehicleType, Dict[AreaType, None | int]],
    station: Station,
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    standard_block_length: int = 6,
    shunting_duration: None | timedelta = timedelta(minutes=5),
    num_shunting_slots: int = 10,
    cleaning_duration: None | timedelta = timedelta(minutes=30),
    num_cleaning_slots: int = 10,
    charging_power: float = 90,
) -> None:
    """
    Creates a depot object with all associated data structures and adds them to the database.

    :param capacity_of_areas: A dictionary of vehicle types and the number of areas for each type.
           Example: {VehicleType<"Electric Bus">: {AreaType.LINE: 3, AreaType.DIRECT_ONESIDE: 2}}
           For no areas of a certain type, set the value to None or zero. An exception will be raised if a LINE
           area's capacity is not a multiple of the standard block length.
    :param station: The station where the depot is located.
    :param scenario: The scenario to be simulated. An Exception will be raised if this differs from the station's scenario.
    :param session: An open SQLAlchemy session.
    :param standard_block_length: The block length (number of vehicles behind each other) for LINE areas. Defaults to 6.
    :param shunting_duration: The duration of the shunting process. Defaults to 5 minutes. Set to None if not needed.
    :param num_shunting_slots: The number of slots for shunting. Defaults to 10.
    :param cleaning_duration: The duration of the cleaning process. Defaults to 30 minutes. Set to None if not needed.
    :param num_cleaning_slots: The number of slots for cleaning. Defaults to 10.
    :param charging_power: The charging power in kW. Defaults to 90 kW.
    :return: Nothing. Depot is added to the database.
    """

    # Sanity checks
    # Make sure the capacity of areas is valid.
    for key, value in capacity_of_areas.items():
        key: VehicleType
        value: Dict[AreaType, None | int]
        for possible_area_type in AreaType:
            if possible_area_type not in value:
                value[possible_area_type] = None
        if (
            value[AreaType.LINE] is not None
            and value[AreaType.LINE] % standard_block_length != 0
        ):
            raise ValueError(
                f"LINE area capacity for {key.name} is not a multiple of the standard block length."
            )

        if (
            value[AreaType.DIRECT_TWOSIDE] is not None
            and value[AreaType.DIRECT_TWOSIDE] % 2 != 0
        ):
            raise ValueError(
                f"DIRECT_TWOSIDE area capacity for {key.name} is not a multiple of 2."
            )

    # Make sure the scenario is the same as the station's scenario
    if station.scenario_id != scenario.id:
        raise ValueError("The scenario and station do not match.")

    # Create a simple depot
    depot = Depot(
        scenario=scenario,
        name=f"Depot at {station.name}",
        name_short=station.name_short,
        station_id=station.id,
    )
    session.add(depot)

    # Create plan
    plan = Plan(scenario=scenario, name=f"Default Plan")
    session.add(plan)

    depot.default_plan = plan

    assocs: List[AssocPlanProcess] = []

    # Create processes
    if shunting_duration is not None:
        # Create processes
        shunting_1 = Process(
            name="Shunting 1",
            scenario=scenario,
            dispatchable=False,
            duration=shunting_duration,
        )
        session.add(shunting_1)
        shunting_area_1 = Area(
            scenario=scenario,
            name=f"Shunting Area 1",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            vehicle_type=None,  # Meaning any vehicle type can be shunted here
            capacity=num_shunting_slots,
        )
        session.add(shunting_area_1)
        shunting_area_1.processes.append(shunting_1)
        assocs.append(
            AssocPlanProcess(
                scenario=scenario, process=shunting_1, plan=plan, ordinal=len(assocs)
            )
        )

    if cleaning_duration is not None:
        clean = Process(
            name="Arrival Cleaning",
            scenario=scenario,
            dispatchable=False,
            duration=cleaning_duration,
        )
        session.add(clean)
        cleaning_area = Area(
            scenario=scenario,
            name=f"Cleaning Area",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            vehicle_type=None,  # Meaning any vehicle type can be cleaned here
            capacity=num_cleaning_slots,
        )
        session.add(cleaning_area)
        cleaning_area.processes.append(clean)
        assocs.append(
            AssocPlanProcess(
                scenario=scenario, process=clean, plan=plan, ordinal=len(assocs)
            )
        )

    if shunting_duration is not None:
        shunting_2 = Process(
            name="Shunting 2",
            scenario=scenario,
            dispatchable=False,
            duration=shunting_duration,
        )
        session.add(shunting_2)
        shunting_area_2 = Area(
            scenario=scenario,
            name=f"Shunting Area 2",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            vehicle_type=None,  # Meaning any vehicle type can be shunted here
            capacity=num_shunting_slots,
        )
        session.add(shunting_area_2)
        shunting_area_2.processes.append(shunting_2)
        assocs.append(
            AssocPlanProcess(
                scenario=scenario, process=shunting_2, plan=plan, ordinal=len(assocs)
            )
        )

    charging = Process(
        name="Charging",
        scenario=scenario,
        dispatchable=True,
        electric_power=charging_power,
    )
    session.add(charging)
    assocs.append(
        AssocPlanProcess(
            scenario=scenario, process=charging, plan=plan, ordinal=len(assocs)
        )
    )

    standby_departure = Process(
        name="Standby Pre-departure",
        scenario=scenario,
        dispatchable=True,
    )
    session.add(standby_departure)
    assocs.append(
        AssocPlanProcess(
            scenario=scenario, process=standby_departure, plan=plan, ordinal=len(assocs)
        )
    )
    session.add_all(assocs)  # It's complete, so add all at once

    # Create shared waiting area
    waiting_area = Area(
        scenario=scenario,
        name=f"Waiting Area for every type of vehicle",
        depot=depot,
        area_type=AreaType.DIRECT_ONESIDE,
        capacity=100,
    )
    session.add(waiting_area)

    for vehicle_type, capacities in capacity_of_areas.items():
        vehicle_type: VehicleType
        capacities: Dict[AreaType, None | int]
        if capacities[AreaType.LINE] is not None and capacities[AreaType.LINE] > 0:
            # Create a number of LINE areas
            number_of_areas = capacities[AreaType.LINE] // standard_block_length
            for i in range(number_of_areas):
                area = Area(
                    scenario=scenario,
                    name=f"Line Area {i + 1} for {vehicle_type.name_short}",
                    depot=depot,
                    area_type=AreaType.LINE,
                    vehicle_type=vehicle_type,
                    capacity=standard_block_length,
                )
                area.processes.append(charging)
                area.processes.append(standby_departure)
                session.add(area)
        if (
            capacities[AreaType.DIRECT_ONESIDE] is not None
            and capacities[AreaType.DIRECT_ONESIDE] > 0
        ):
            # Create a single DIRECT_ONESIDE area with the correct capacity
            area = Area(
                scenario=scenario,
                name=f"Direct Area for {vehicle_type.name_short}",
                depot=depot,
                area_type=AreaType.DIRECT_ONESIDE,
                vehicle_type=vehicle_type,
                capacity=capacities[AreaType.DIRECT_ONESIDE],
            )
            area.processes.append(charging)
            area.processes.append(standby_departure)
            session.add(area)
        if (
            capacities[AreaType.DIRECT_TWOSIDE] is not None
            and capacities[AreaType.DIRECT_TWOSIDE] > 0
        ):
            # Create a single DIRECT_TWOSIDE area with the correct capacity
            area = Area(
                scenario=scenario,
                name=f"Direct Area for {vehicle_type.name_short}",
                depot=depot,
                area_type=AreaType.DIRECT_TWOSIDE,
                vehicle_type=vehicle_type,
                capacity=capacities[AreaType.DIRECT_TWOSIDE],
            )
            area.processes.append(charging)
            area.processes.append(standby_departure)
            session.add(area)

    session.flush()


def area_needed_for_vehicle_parking(
    vehicle_type: VehicleType,
    count: int,
    area_type: AreaType,
    standard_block_length: int = 6,
    spacing: float = 0.5,
    angle=45,
) -> float:
    """
    Calculates the area (in m²) needed to park a given number of vehicles of a given type.

    DOes not take into account
    the area needed to drive in and out of the parking spots.

    - For AreaType.LINE, the vehicle count is rounded up to the next multiple of the standard block length.
    - For AreaType.DIRECT_ONESIDE, the vehicle count is used as is.
    - For AreaType.DIRECT_TWOSIDE, the vehicle count is rounded up to the next even number.

    For the DIRECT area types, an angle of 45° is assumed for the vehicles.

    :param vehicle_type: The vehicle type to calculate the area for.
    :param count: The number of vehicles to park.
    :param area_type: The type of the area to calculate the area for.
    :param standard_block_length: The standard block length to use for LINE areas. Defaults to 6 (vehicles behind each other).
    :param spacing: The space needed on the sides of the vehicles. Defaults to 0.5m.
    :param angle: The angle the vehicles are parked at in direct areas, in degrees. Defaults to 45°. *Only used for direct areas.*
    :return: The area needed in m².
    """

    length = vehicle_type.length
    width = vehicle_type.width

    if length is None or width is None:
        raise ValueError(f"No length or width found for VehicleType {vehicle_type}")

    # This is the angle the vehicles are parked at in direct areas
    # zero is equivalent to the direction they would be parked in a line area
    # 90 means they are parked perpendicular to the line and would need to turn 90 degrees to drive out
    # This is the angle the vehicles are parked at in direct areas
    # zero is equivalent to the direction they would be parked in a line area
    # 90 means they are parked perpendicular to the line and would need to turn 90 degrees to drive out
    #
    #   LINE AREA (0°):
    #   |   |
    #   |   |
    #
    #   DIRECT AREA (45°):
    #   /
    #   /
    #
    #   DIRECT AREA (90°):
    #   -
    #   -
    angle = math.radians(angle)

    match area_type:
        case AreaType.LINE:
            # For LINE areas, we need to round up the vehicle count to the next multiple of the standard block length
            count = math.ceil(count / standard_block_length) * standard_block_length
            number_of_rows = count / standard_block_length

            # Return the total area, including the space between the vehicles
            # But the space between the vehicles is only needed between, so it's one less than the count of vehicles
            # | | | |   ^
            # | | | |   | <- area_height
            # | | | |   v
            # <-----> area_width

            area_height = length * standard_block_length + (
                spacing * (standard_block_length - 1)
            )
            area_width = width * number_of_rows + (spacing * (number_of_rows - 1))

        case AreaType.DIRECT_ONESIDE:
            # Here, it's more complicated math, due to the vehicles being parked at an angle
            #
            # See "docs/direct_details.pdf" for a visual explanation
            # /  ^
            # /  | <- area_height
            # /  v
            # <-> area_width
            #
            # - 0°
            # / 45°
            # | 90°

            # Area height, according tho the formula in the docs
            b_0 = (
                math.cos(angle) * vehicle_type.width
                + math.sin(angle) * vehicle_type.length
            )

            # If the angle os too steep, refuse to calculate
            if math.tan(angle) > vehicle_type.length / vehicle_type.width:
                raise ValueError("The angle is too steep for the vehicle to fit")

            h = (1 / math.cos(angle)) * vehicle_type.width
            space_between = (count - 1) * math.cos(angle) * spacing
            area_height = b_0 + (count - 1) * h + space_between

            # Area width, according tho the formula in the docs
            area_width = (
                math.sin(angle) * vehicle_type.width
                + math.cos(angle) * vehicle_type.length
            )

        case AreaType.DIRECT_TWOSIDE:
            # For DIRECT_TWOSIDE, we need to round up the vehicle count to the next even number
            count = count + (count % 2)
            number_of_rows = count / 2

            # Here, it's more complicated math, due to the vehicles being parked at an angle
            # See "docs/direct_details.pdf" for a visual explanation
            #   \
            #  / \
            # / \
            #  / \
            # / \
            #  / \
            # /
            raise NotImplementedError("This area type is not yet implemented")

    return area_height * area_width
