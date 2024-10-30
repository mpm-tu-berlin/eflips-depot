"""This package contains the private API for the depot-related functionality in eFLIPS."""
import itertools
import logging
from datetime import timedelta
from enum import Enum, auto
from math import ceil
from typing import Dict, List, Tuple

import eflips.model
import numpy as np
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

from eflips.depot import DepotEvaluation, LineArea, DirectArea
from eflips.depot.evaluation import to_prev_values


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

    # Load all areas, sorted by their type
    area_type_order = [AreaType.LINE, AreaType.DIRECT_ONESIDE, AreaType.DIRECT_TWOSIDE]
    sorted_areas = sorted(depot.areas, key=lambda x: area_type_order.index(x.area_type))

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


def create_simple_depot(
    scenario: Scenario,
    station: Station,
    charging_capacities: Dict[VehicleType, int],
    cleaning_capacities: Dict[VehicleType, int],
    charging_power: float,
    session: sqlalchemy.orm.session.Session,
    cleaning_duration: timedelta = timedelta(minutes=30),
    safety_margin: float = 0.0,
    shunting_duration: timedelta = timedelta(minutes=5),
) -> None:
    """
    Creates a simple depot for a given scenario.

    It has one area for each vehicle type and a charging process for each
    area. Also an arrival area for each vehicle type.

    :param safety_margin: a safety margin for the number of charging and cleaning capacities. Default is 0.0
    :param scenario: The scenario to be simulated
    :param station: The station where the depot is located
    :param charging_capacities: A dictionary of vehicle types and the number of vehicles that can be charged at the same time
    :param cleaning_capacities: A dictionary of vehicle types and the number of vehicles that can be cleaned at the same time
    :param charging_power: The power of the charging process
    :param cleaning_duration: The duration of the cleaning process
    :param session: An SQLAlchemy session object to the database
    :return: Nothing. Depots are created in the database.
    """

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

    # Create processes
    shunting_1 = Process(
        name="Shunting 1",
        scenario=scenario,
        dispatchable=False,
        duration=shunting_duration,
    )
    clean = Process(
        name="Arrival Cleaning",
        scenario=scenario,
        dispatchable=False,
        duration=cleaning_duration,
    )
    shunting_2 = Process(
        name="Shunting 2",
        scenario=scenario,
        dispatchable=False,
        duration=shunting_duration,
    )
    charging = Process(
        name="Charging",
        scenario=scenario,
        dispatchable=True,
        electric_power=charging_power,
    )
    standby_departure = Process(
        name="Standby Pre-departure",
        scenario=scenario,
        dispatchable=True,
    )

    session.add(clean)
    session.add(shunting_1)
    session.add(charging)
    session.add(standby_departure)
    session.add(shunting_2)

    # Create shared waiting area
    waiting_area = Area(
        scenario=scenario,
        name=f"Waiting Area for every type of vehicle",
        depot=depot,
        area_type=AreaType.DIRECT_ONESIDE,
        capacity=100,
    )
    session.add(waiting_area)

    for vehicle_type in charging_capacities.keys():
        charging_count = charging_capacities[vehicle_type]

        charging_count = int(ceil(charging_count * (1 + safety_margin)))

        # Create charging area
        charging_area = Area(
            scenario=scenario,
            name=f"Direct Charging Area for {vehicle_type.name_short}",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=int(charging_count * 1),
        )
        session.add(charging_area)
        charging_area.vehicle_type = vehicle_type

        # Create cleaning area
        cleaning_count = cleaning_capacities[vehicle_type]

        cleaning_count = int(ceil(cleaning_count * (1 + safety_margin)))

        cleaning_area = Area(
            scenario=scenario,
            name=f"Cleaning Area for {vehicle_type.name_short}",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=cleaning_count,
        )
        session.add(cleaning_area)
        cleaning_area.vehicle_type = vehicle_type

        shunting_area_1 = Area(
            scenario=scenario,
            name=f"Shunting Area 1 for {vehicle_type.name_short}",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=10,
        )

        session.add(shunting_area_1)
        shunting_area_1.vehicle_type = vehicle_type

        shunting_area_2 = Area(
            scenario=scenario,
            name=f"Shunting Area 2 for {vehicle_type.name_short}",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=10,
        )

        session.add(shunting_area_2)
        shunting_area_2.vehicle_type = vehicle_type

        cleaning_area.processes.append(clean)
        charging_area.processes.append(charging)
        charging_area.processes.append(standby_departure)
        shunting_area_1.processes.append(shunting_1)
        shunting_area_2.processes.append(shunting_2)

        assocs = [
            AssocPlanProcess(
                scenario=scenario, process=shunting_1, plan=plan, ordinal=0
            ),
            AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
            AssocPlanProcess(
                scenario=scenario, process=shunting_2, plan=plan, ordinal=2
            ),
            AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=3),
            AssocPlanProcess(
                scenario=scenario, process=standby_departure, plan=plan, ordinal=4
            ),
        ]
        session.add_all(assocs)


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


def _generate_all_direct_depot(
    CLEAN_DURATION: int,
    charging_power: float,
    first_stop: Station,
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    vehicle_type_dict: Dict[VehicleType, List[Rotation]],
    shunting_duration: timedelta = timedelta(minutes=5),
) -> None:
    """
    Private inner function to generate a depot layout with an arrival and a charging area for each vehicle type.

    :param CLEAN_DURATION: The duration of the cleaning process in seconds.
    :param charging_power: The charging power of the charging area in kW.
    :param first_stop: The stop where the depot is located.
    :param scenario: The scenario for which the depot layout should be generated.
    :param session: The SQLAlchemy session object.
    :param vehicle_type_dict: A dictionary with vehicle types as keys and rotations as values.
    :return: Nothing. The depot layout is created in the database.
    """
    max_occupancies: Dict[eflips.model.VehicleType, int] = {}
    max_clean_occupancies: Dict[eflips.model.VehicleType, int] = {}
    for vehicle_type, rotations in vehicle_type_dict.items():
        # Slightly convoluted vehicle summation
        start_time = min(
            [rotation.trips[0].departure_time for rotation in rotations]
        ).timestamp()
        end_time = max(
            [rotation.trips[-1].arrival_time for rotation in rotations]
        ).timestamp()
        timestamps_to_sample = np.arange(start_time, end_time, 60)
        occupancy = np.zeros_like(timestamps_to_sample)
        clean_occupancy = np.zeros_like(timestamps_to_sample)
        for rotation in rotations:
            rotation_start = rotation.trips[0].departure_time.timestamp()
            rotation_end = rotation.trips[-1].arrival_time.timestamp()
            occupancy += np.interp(
                timestamps_to_sample,
                [rotation_start, rotation_end],
                [1, 1],
                left=0,
                right=0,
            )
            clean_occupancy += np.interp(
                timestamps_to_sample,
                [rotation_end, rotation_end + CLEAN_DURATION],
                [1, 1],
                left=0,
                right=0,
            )
        max_occupancies[vehicle_type] = max(
            max(occupancy), 1
        )  # To avoid zero occupancy
        max_clean_occupancies[vehicle_type] = max(max(clean_occupancy), 1)
    # Create a simple depot at this station
    create_simple_depot(
        scenario=scenario,
        station=first_stop,
        charging_capacities=max_occupancies,
        cleaning_capacities=max_clean_occupancies,
        charging_power=charging_power,
        session=session,
        cleaning_duration=timedelta(seconds=CLEAN_DURATION),
        safety_margin=0.2,
        shunting_duration=shunting_duration,
    )


def generate_line_depot_layout(
    CLEAN_DURATION: int,
    charging_power: float,
    station: Station,
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    direct_counts: Dict[VehicleType, int],
    line_counts: Dict[VehicleType, int],
    line_length: int,
    vehicle_type_rotation_dict: Dict[VehicleType, List[Rotation]],
    shunting_duration: timedelta = timedelta(minutes=5),
) -> None:
    """
    Generate a depot layout with line areas and direct areas.

    :param CLEAN_DURATION: The duration of the cleaning process in seconds.
    :param charging_power: The charging power of the charging area in kW.
    :param station: The stop where the depot is located.
    :param scenario: The scenario for which the depot layout should be generated.
    :param session: The SQLAlchemy session object.
    :param direct_counts: A dictionary with vehicle types as keys and the number of vehicles in the direct areas as
        values.
    :param line_counts: A dictionary with vehicle types as keys and the number of vehicles in the line areas as values.
    :param line_length: The length of the line areas.
    :param vehicle_type_rotation_dict: A dictionary with vehicle types as keys and rotations as values.
    :return: The number of cleaning areas and the number of shunting areas.
    """
    logger = logging.getLogger(__name__)
    DEBUG_PLOT = False

    # In order to figure out how many cleaning areas we need, we look at the number of vehicle simultaneously being
    # cleaned. This is the number of vehicles simulatenously being within the "CLEAN_DURATION" after their arrival.

    # We assemble a vector of all time in the simulation
    logger.info("Calculating the number of cleaning areas needed")
    all_rotations = list(itertools.chain(*vehicle_type_rotation_dict.values()))
    start_time = min(
        [rotation.trips[0].departure_time for rotation in all_rotations]
    ).timestamp()
    end_time = max(
        [rotation.trips[-1].arrival_time for rotation in all_rotations]
    ).timestamp()
    timestamps_to_sample = np.arange(start_time, end_time, 60)
    clean_occupancy = np.zeros_like(timestamps_to_sample)

    # Then fir each arrival, we add 1 to the CLEAN_DURATION after the arrival
    for rotation in all_rotations:
        rotation_end = rotation.trips[-1].arrival_time.timestamp()
        clean_occupancy += np.interp(
            timestamps_to_sample,
            [rotation_end, rotation_end + CLEAN_DURATION],
            [1, 1],
            left=0,
            right=0,
        )

    if DEBUG_PLOT:
        from matplotlib import pyplot as plt

        plt.figure()
        plt.plot(timestamps_to_sample, clean_occupancy)
        plt.show()

    vehicles_arriving_in_window = int(max(clean_occupancy))
    logger.info(
        f"Number of vehicles arriving in a {CLEAN_DURATION/60:.1f} minute window: {vehicles_arriving_in_window:.0f}"
    )

    # Take a fifth of the vehicles arriving in the window as the number of cleaning areas needed
    clean_areas_needed = ceil(vehicles_arriving_in_window / 2)
    logger.info(f"Number of cleaning areas created: {clean_areas_needed}")
    del all_rotations, clean_occupancy, timestamps_to_sample, start_time, end_time

    # Create the depot
    # `vehicles_arriving_in_window`+1 will be the size of our shunting areas
    # `clean_areas_needed` will be the size of our cleaning areas
    # We will create line and direct areas for each vehicle type
    # - THe line areas will be of length `line_length` and count `line_counts[vehicle_type]`
    # - The direct areas will be of length 1 and count `direct_counts[vehicle_type]`
    # - The charging power for the line areas will be `charging_power_direct` and for the direct areas `charging_power`
    #   unless `charging_power_direct` is not set, in which case `charging_power` will be used.

    # Create the depot
    depot = Depot(
        scenario=scenario,
        name=f"Depot at {station.name}",
        name_short=station.name_short,
        station_id=station.id,
    )
    session.add(depot)

    shunting_1 = Process(
        name="Shunting 1 (Arrival -> Cleaning)",
        scenario=scenario,
        dispatchable=False,
        duration=shunting_duration,
    )

    session.add(shunting_1)
    clean = Process(
        name="Arrival Cleaning",
        scenario=scenario,
        dispatchable=False,
        duration=timedelta(seconds=CLEAN_DURATION),
    )
    session.add(clean)

    shunting_2 = Process(
        name="Shunting 2 (Cleaning -> Charging)",
        scenario=scenario,
        dispatchable=False,
        duration=shunting_duration,
    )
    session.add(shunting_2)

    charging = Process(
        name="Charging",
        scenario=scenario,
        dispatchable=True,
        electric_power=charging_power,
    )

    standby_departure = Process(
        name="Standby Pre-departure",
        scenario=scenario,
        dispatchable=True,
    )
    session.add(standby_departure)

    # Create shared waiting area
    # This will be the "virtual" area where vehicles wait for a spot in the depot
    waiting_area = Area(
        scenario=scenario,
        name=f"Waiting Area for every type of vehicle",
        depot=depot,
        area_type=AreaType.DIRECT_ONESIDE,
        capacity=100,
    )
    session.add(waiting_area)

    # Create a shared shunting area (large enough to fit all rotations)
    shunting_area_1 = Area(
        scenario=scenario,
        name=f"Shunting Area 1 (Arrival -> Cleaning)",
        depot=depot,
        area_type=AreaType.DIRECT_ONESIDE,
        capacity=sum(
            [len(rotations) for rotations in vehicle_type_rotation_dict.values()]
        ),  # TODO
    )
    session.add(shunting_area_1)
    shunting_area_1.processes.append(shunting_1)

    # Create a shared cleaning area
    cleaning_area = Area(
        scenario=scenario,
        name=f"Cleaning Area",
        depot=depot,
        area_type=AreaType.DIRECT_ONESIDE,
        capacity=clean_areas_needed,
    )
    session.add(cleaning_area)
    cleaning_area.processes.append(clean)

    # Create a shared shunting area
    shunting_area_2 = Area(
        scenario=scenario,
        name=f"Shunting Area 2 (Cleaning -> Charging)",
        depot=depot,
        area_type=AreaType.DIRECT_ONESIDE,
        capacity=clean_areas_needed,
    )
    session.add(shunting_area_2)
    shunting_area_2.processes.append(shunting_2)

    # Create the line areas for each vehicle type
    for vehicle_type, count in line_counts.items():
        for i in range(count):
            line_area = Area(
                scenario=scenario,
                name=f"Line Area for {vehicle_type.name} #{i+1:02d}",
                depot=depot,
                area_type=AreaType.LINE,
                capacity=line_length,
                vehicle_type=vehicle_type,
            )
            session.add(line_area)
            line_area.processes.append(charging)
            line_area.processes.append(standby_departure)

    # Create the direct areas for each vehicle type
    for vehicle_type, count in direct_counts.items():
        if count > 0:
            direct_area = Area(
                scenario=scenario,
                name=f"Direct Area for {vehicle_type.name}",
                depot=depot,
                area_type=AreaType.DIRECT_ONESIDE,
                capacity=count,
                vehicle_type=vehicle_type,
            )
            session.add(direct_area)
            direct_area.processes.append(charging)
            direct_area.processes.append(standby_departure)

    # Create the plan
    # Create plan
    plan = Plan(scenario=scenario, name=f"Default Plan")
    session.add(plan)

    depot.default_plan = plan

    # Create the assocs in order to put the areas in the plan
    assocs = [
        AssocPlanProcess(scenario=scenario, process=shunting_1, plan=plan, ordinal=0),
        AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
        AssocPlanProcess(scenario=scenario, process=shunting_2, plan=plan, ordinal=2),
        AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=3),
        AssocPlanProcess(
            scenario=scenario, process=standby_departure, plan=plan, ordinal=4
        ),
    ]
    session.add_all(assocs)


def real_peak_area_utilization(ev: DepotEvaluation) -> Dict[str, Dict[AreaType, int]]:
    """
    Calculate the real peak vehicle count for a depot evaluation by vehicle type and area type.

    For the line areas, the maximum number of lines in use at the same time is calculated.

    :param ev: A DepotEvaluation object.
    :return: The real peak vehicle count by vehicle type and area type.
    """
    area_types_by_id: Dict[int, AreaType] = dict()
    total_counts_by_area: Dict[str, Dict[str, np.ndarray]] = dict()

    # We are assuming that the smulation runs for at least four days
    SECONDS_IN_A_DAY = 24 * 60 * 60
    assert ev.SIM_TIME >= 4 * SECONDS_IN_A_DAY

    for area in ev.depot.list_areas:
        # We need to figure out which kind of area this is
        # We do this by looking at the vehicle type of the area
        if len(area.entry_filter.filters) > 0:
            if isinstance(area, LineArea):
                area_types_by_id[area.ID] = AreaType.LINE
            elif isinstance(area, DirectArea):
                area_types_by_id[area.ID] = AreaType.DIRECT_ONESIDE
            else:
                raise ValueError("Unknown area type")

            assert len(area.entry_filter.vehicle_types_str) == 1
            vehicle_type_name = area.entry_filter.vehicle_types_str[0]

            nv = area.logger.get_valList("count", SIM_TIME=ev.SIM_TIME)
            nv = to_prev_values(nv)
            nv = np.array(nv)

            # If the area is empty, we don't care about it
            if np.all(nv == 0):
                continue

            if vehicle_type_name not in total_counts_by_area:
                total_counts_by_area[vehicle_type_name] = dict()
            # We don't want the last day, as all vehicles will re-enter the depot
            total_counts_by_area[vehicle_type_name][area.ID] = nv[:-SECONDS_IN_A_DAY]
        else:
            # This is an area for all vehicle types
            # We don't care about this
            continue

    if False:
        from matplotlib import pyplot as plt

        for vehicle_type_name, counts in total_counts_by_area.items():
            plt.figure()
            for area_id, proper_counts in counts.items():
                # dashed if direct, solid if line
                if area_types_by_id[area_id] == AreaType.DIRECT_ONESIDE:
                    plt.plot(proper_counts, "--", label=area_id)
                else:
                    plt.plot(proper_counts, label=area_id)
            plt.legend()
            plt.show()

    # Calculate the maximum utilization of the direct areas and the maximum number of lines in use at the same time
    # Per vehicle type
    ret_val: Dict[str, Dict[AreaType, int]] = dict()
    for vehicle_type_name, count_dicts in total_counts_by_area.items():
        peak_direct_area_usage = 0
        number_of_lines_in_use = 0
        for area_id, counts in count_dicts.items():
            if area_types_by_id[area_id] == AreaType.DIRECT_ONESIDE:
                peak_direct_area_usage += max(peak_direct_area_usage, np.max(counts))
            else:
                number_of_lines_in_use += 1

        ret_val[vehicle_type_name] = {
            AreaType.DIRECT_ONESIDE: int(peak_direct_area_usage),
            AreaType.LINE: int(number_of_lines_in_use),
        }

    return ret_val


def real_peak_vehicle_count(ev: DepotEvaluation) -> Dict[str, int]:
    """
    Calculate the real peak vehicle count for a depot evaluation.

    This is different from the amount of vehicles used
    in the calculation, as towards the end of the simulation all vehicles will re-enter-the depot, which leads to
    a lower actual peak vehicle count than what `nvehicles_used_calculation` returns.
    :param ev: A DepotEvaluation object.
    :return: The real peak vehicle count. This is what the depot layout should be designed for.
    """

    total_counts_by_vehicle_type: Dict[str, np.ndarray] = dict()

    for area in ev.depot.list_areas:
        # We need to figure out which kind of area this is
        # We do this by looking at the vehicle type of the area
        if len(area.entry_filter.filters) > 0:
            assert len(area.entry_filter.vehicle_types_str) == 1
            vehicle_type_name = area.entry_filter.vehicle_types_str[0]

            nv = area.logger.get_valList("count", SIM_TIME=ev.SIM_TIME)
            nv = to_prev_values(nv)
            nv = np.array(nv)

            if vehicle_type_name not in total_counts_by_vehicle_type:
                total_counts_by_vehicle_type[vehicle_type_name] = np.zeros(
                    ev.SIM_TIME, dtype=np.int32
                )
            total_counts_by_vehicle_type[vehicle_type_name] += nv
        else:
            # This is an area for all vehicle types
            # We don't care about this
            continue

    # We are assuming that the smulation runs for at least four days
    SECONDS_IN_A_DAY = 24 * 60 * 60
    assert ev.SIM_TIME >= 4 * SECONDS_IN_A_DAY

    # Towards the end, all the vehicles will re-enter the depot
    # So our practital peak vehicle count is the maximum excluding the last day
    for vehicle_type_name, counts in total_counts_by_vehicle_type.items():
        total_counts_by_vehicle_type[vehicle_type_name] = counts[:-SECONDS_IN_A_DAY]

    return {
        vehicle_type_name: int(np.max(counts))
        for vehicle_type_name, counts in total_counts_by_vehicle_type.items()
    }
