"""This package contains the private API for the depot-related functionality in eFLIPS."""

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
            template["groups"][group_name]["parking_strategy_name"] = "SMART2"

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
