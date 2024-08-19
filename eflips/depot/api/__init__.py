"""
This package contains the public API for eFLIPS-Depot.

It is to be used in conjunction with the
`eflips.model <https://github.com/mpm-tu-berlin/eflips-model>`_ package, where the Scenario is defined.

Notes on the usage of the API
-----------------------------

The following steps are recommended for using the API:

1. Check if there are already "driving events" in the database. They come from a "consumption simulation" and are
   associated with a vehicle. If there are no driving events, you may use the :func:`simple_consumption_simulation`
   (with ``initialize_vehicles=True``) to create them. This function will also initialize the vehicles in the database
   with the correct vehicle type and assign them to rotations.
2. Check if there is already a depot layout in the database. If there is not, you may use the
   :func:`generate_depot_layout` function to create a simple depot layout and plan.
3. Either use the :func:`simulate_scenario` function to run the whole simulation in one go, or use the following steps:
    a. Use the :func:`init_simulation` function to create a simulation host, which is a "black box" object containing
       all input data for the simulation.
    b. Use the :func:`run_simulation` function to run the simulation and obtain the results.
    c. Use the :func:`add_evaluation_to_database` function to add the results to the database.
4. For the results to be valid, the consumption simulation should now be run again.
    a. If you are using an external consumption model, run it again making sure it does not create new vehicles.
    b. Run the :func:`simple_consumption_simulation` function again, this time with ``initialize_vehicles=False``.
"""
import copy
import datetime
import itertools
import logging
import os
import warnings
from collections import OrderedDict
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from math import ceil
from typing import Any, Dict, Optional, Union, List

import numpy as np
import sqlalchemy.orm
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
)
from eflips.model import Process
from sqlalchemy.orm import Session
from sqlalchemy.sql import select

import eflips.depot
from eflips.depot import (
    DepotEvaluation,
    DirectArea,
    LineArea,
    ProcessStatus,
    SimulationHost,
    SimpleVehicle,
)
from eflips.depot.api.private.depot import (
    _generate_all_direct_depot,
    create_simple_depot,
    delete_depots,
    depot_to_template,
    group_rotations_by_start_end_stop,
)
from eflips.depot.api.private.smart_charging import optimize_charging_events_even
from eflips.depot.api.private.util import (
    create_session,
    repeat_vehicle_schedules,
    start_and_end_times,
    vehicle_type_to_global_constants_dict,
    VehicleSchedule,
    check_depot_validity,
)
from eflips.depot.evaluation import to_prev_values


class SmartChargingStrategy(Enum):
    """Enum class for different smart charging strategies."""

    NONE = 0
    """
    Do not use smart charging.

    Buses are charged with the maximum power available, from the time they arrive at the depot
    until they are full (or leave the depot).
    """
    EVEN = 1
    """
    Use smart charging with an even distribution of charging power over the time the bus is at the depot.

    This aims to
    minimize the peak power demand.
    """
    MIN_PRICE = 2
    """
    Use smart charging in order to minimize the cost of charging.

    The price profile can be specified using the
    PRICE_PROFILE environment variable. If this is not set, the price is loaded using an API.
    """


def simple_consumption_simulation(
    scenario: Union[Scenario, int, Any],
    initialize_vehicles: bool,
    database_url: Optional[str] = None,
    calculate_timeseries: bool = False,
    terminus_deadtime: timedelta = timedelta(minutes=1),
) -> None:
    """
    A simple consumption simulation and vehicle initialization.

    Energy consumotion is calculated by multiplying the vehicle's total distance by a constant
    ``VehicleType.consumption``.

    If run with ``initialize_vehicles=True``, the method will also initialize the vehicles in the database with the
    correct vehicle type and assign them to rotations. If this is false, it will assume that there are already vehicle
    entries and ``Rotation.vehicle_id`` is already set.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
        an integer specifying the ID of a scenario in the database. Or any other object that has an attribute
        ``id`` that is an integer. If no :class:`eflips.model.Scenario` object is passed, the ``database_url``
        parameter must be set to a valid database URL ot the environment variable ``DATABASE_URL`` must be set to a
        valid database URL.

    :param initialize_vehicles: A boolean flag indicating whether the vehicles should be initialized in the database.
        When running this function for the first time, this should be set to True. When running this function again
        after the vehicles have been initialized, this should be set to False.

    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
        :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a
        valid database URL.

    :param calculate_timeseries: A boolean flag indicating whether the timeseries should be calculated. If this is set
        to True, the SoC at each stop is calculated and added to the "timeseries" column of the Event table. If this
        is set to False, the "timeseries" column of the Event table will be set to ``None``. Setting this to false
        may significantly speed up the simulation.

    :param terminus_deadtime: The total deadtime taken to both attach and detach the charging cable at the terminus.
                              If the total deadtime is greater than the time between the arrival and departure of the
                              vehicle at the terminus, the vehicle will not be able to charge at the terminus.

    :return: Nothing. The results are added to the database.
    """
    logger = logging.getLogger(__name__)

    with create_session(scenario, database_url) as (session, scenario):
        rotations = (
            session.query(Rotation)
            .filter(Rotation.scenario_id == scenario.id)
            .order_by(Rotation.id)
            .options(sqlalchemy.orm.joinedload(Rotation.trips).joinedload(Trip.route))
            .options(sqlalchemy.orm.joinedload(Rotation.vehicle_type))
            .options(sqlalchemy.orm.joinedload(Rotation.vehicle))
        )
        if initialize_vehicles:
            for rotation in rotations:
                vehicle = Vehicle(
                    vehicle_type_id=rotation.vehicle_type_id,
                    scenario_id=scenario.id,
                    name=f"Vehicle for rotation {rotation.id}",
                )
                session.add(vehicle)
                rotation.vehicle = vehicle

                # Additionally, add a short STANDBY event with 100% SoC immediately before the first trip
                first_trip_start = rotation.trips[0].departure_time
                standby_start = first_trip_start - timedelta(seconds=1)
                standby_event = Event(
                    scenario_id=scenario.id,
                    vehicle_type_id=rotation.vehicle_type_id,
                    vehicle=vehicle,
                    station_id=rotation.trips[0].route.departure_station_id,
                    subloc_no=0,
                    time_start=standby_start,
                    time_end=first_trip_start,
                    soc_start=1,
                    soc_end=1,
                    event_type=EventType.CHARGING_OPPORTUNITY,
                    description=f"DUMMY Initial standby event for rotation {rotation.id}.",
                    timeseries=None,
                )
                session.add(standby_event)
        else:
            for rotation in rotations:
                if rotation.vehicle is None:
                    raise ValueError(
                        "The rotation does not have a vehicle assigned to it."
                    )

            vehicles = (
                session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).all()
            )
            for vehicle in vehicles:
                if (
                    session.query(Event).filter(Event.vehicle_id == vehicle.id).count()
                    == 0
                ):
                    # Also add a dummy standby-departure event if this vehicle has no events
                    rotation_per_vehicle = sorted(
                        vehicle.rotations, key=lambda r: r.trips[0].departure_time
                    )
                    earliest_trip = rotation_per_vehicle[0].trips[0]
                    area = (
                        session.query(Area)
                        .filter(Area.scenario_id == scenario.id)
                        .filter(Area.vehicle_type_id == Vehicle.vehicle_type_id)
                        .first()
                    )

                    standby_start = earliest_trip.departure_time - timedelta(seconds=1)
                    standby_event = Event(
                        scenario_id=scenario.id,
                        vehicle_type_id=vehicle.vehicle_type_id,
                        vehicle=vehicle,
                        area_id=area.id,
                        subloc_no=area.capacity,
                        time_start=standby_start,
                        time_end=earliest_trip.departure_time,
                        soc_start=1,
                        soc_end=1,
                        event_type=EventType.STANDBY_DEPARTURE,
                        description=f"DUMMY Initial standby event for rotation {earliest_trip.rotation_id}.",
                        timeseries=None,
                    )
                    session.add(standby_event)

        # Since we are doing no_autoflush blocks later, we need to flush the session once here so that unflushed stuff
        # From preceding functions is visible in the database
        session.flush()

        for rotation in rotations:
            rotation: Rotation
            with session.no_autoflush:
                vehicle_type = rotation.vehicle_type
                vehicle = rotation.vehicle
            if vehicle_type.consumption is None:
                raise ValueError(
                    "The vehicle type does not have a consumption value set."
                )
            consumption = vehicle_type.consumption

            # The departure SoC for this rotation is the SoC of the last event preceding the first trip
            with session.no_autoflush:
                current_soc = (
                    session.query(Event.soc_end)
                    .filter(Event.vehicle_id == rotation.vehicle_id)
                    .filter(Event.time_end <= rotation.trips[0].departure_time)
                    .order_by(Event.time_end.desc())
                    .first()[0]
                )

            for trip in rotation.trips:
                # Set up a timeseries
                soc_start = current_soc
                if calculate_timeseries and len(trip.stop_times) > 0:
                    timeseries = {
                        "time": [],
                        "soc": [],
                        "distance": [],
                    }
                    for i in range(len(trip.stop_times)):
                        current_time = trip.stop_times[i].arrival_time
                        dwell_duration = trip.stop_times[i].dwell_duration
                        elapsed_distance = trip.route.assoc_route_stations[
                            i
                        ].elapsed_distance
                        elapsed_energy = consumption * (elapsed_distance / 1000)  # kWh
                        soc = (
                            current_soc - elapsed_energy / vehicle_type.battery_capacity
                        )
                        timeseries["time"].append(current_time.isoformat())
                        timeseries["soc"].append(soc)
                        timeseries["distance"].append(elapsed_distance)
                        if dwell_duration > timedelta(seconds=0):
                            timeseries["time"].append(
                                (current_time + dwell_duration).isoformat()
                            )
                            timeseries["soc"].append(soc)
                            timeseries["distance"].append(elapsed_distance)
                else:
                    timeseries = None
                energy_used = consumption * trip.route.distance / 1000  # kWh
                current_soc = soc_start - energy_used / vehicle_type.battery_capacity

                # Create a driving event
                current_event = Event(
                    scenario_id=scenario.id,
                    vehicle_type_id=rotation.vehicle_type_id,
                    vehicle=vehicle,
                    trip_id=trip.id,
                    time_start=trip.departure_time,
                    time_end=trip.arrival_time,
                    soc_start=soc_start,
                    soc_end=current_soc,
                    event_type=EventType.DRIVING,
                    description=f"`VehicleType.consumption`-based driving event for trip {trip.id}.",
                    timeseries=timeseries,
                )
                session.add(current_event)

                # If the vehicle is
                #  - Capable of opportunity charging
                #  - On a Rotation which allows opportunity charging
                #  - Currently at a station which allows opportunity charging
                #  - which is not the last trip of the rotation
                #  We add a charging event

                if (
                    rotation.vehicle_type.opportunity_charging_capable
                    and rotation.allow_opportunity_charging
                    and trip.route.arrival_station.is_electrified
                    and trip != rotation.trips[-1]
                ):
                    logger.debug(
                        f"Adding opportunity charging event for trip {trip.id}"
                    )
                    # Identify the break time between trips
                    trip_index = rotation.trips.index(trip)
                    next_trip = rotation.trips[trip_index + 1]
                    break_time = next_trip.departure_time - trip.arrival_time

                    # How much energy can be charged in this time?
                    energy_charged = (
                        max([v[1] for v in vehicle_type.charging_curve])
                        * (
                            break_time.total_seconds()
                            - terminus_deadtime.total_seconds()
                        )
                        / 3600
                    )

                    if energy_charged > 0:
                        # Calculate the end SoC
                        post_charge_soc = min(
                            current_soc
                            + energy_charged / vehicle_type.battery_capacity,
                            1,
                        )

                        # Create a simple timeseries for the charging event
                        timeseries = {
                            "time": [
                                trip.arrival_time.isoformat(),
                                (trip.arrival_time + terminus_deadtime / 2).isoformat(),
                                (
                                    next_trip.departure_time - terminus_deadtime / 2
                                ).isoformat(),
                                next_trip.departure_time.isoformat(),
                            ],
                            "soc": [
                                current_soc,
                                current_soc,
                                post_charge_soc,
                                post_charge_soc,
                            ],
                        }

                        # Create the charging event
                        current_event = Event(
                            scenario_id=scenario.id,
                            vehicle_type_id=rotation.vehicle_type_id,
                            vehicle=vehicle,
                            station_id=trip.route.arrival_station_id,
                            time_start=trip.arrival_time,
                            time_end=next_trip.departure_time,
                            soc_start=current_soc,
                            soc_end=post_charge_soc,
                            event_type=EventType.CHARGING_OPPORTUNITY,
                            description=f"Opportunity charging event for trip {trip.id}.",
                            timeseries=timeseries,
                        )
                        current_soc = post_charge_soc
                        session.add(current_event)


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


@dataclass
class DepotConfiguration:
    charging_power: float
    line_counts: Dict[VehicleType, int]
    direct_counts: Dict[VehicleType, int]
    clean_duration: int
    num_clean_areas: int
    num_shunting_areas: int


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


def generate_realistic_depot_layout(
    scenario: Union[Scenario, int, Any],
    charging_power: float,
    database_url: Optional[str] = None,
    delete_existing_depot: bool = False,
    line_length: int = 8,
    CLEAN_DURATION: int = 10 * 60,  # 10 minutes in seconds
    shunting_duration: timedelta = timedelta(minutes=5),
) -> DepotConfiguration:
    """
    Creates a realistic depot layout for the scenario.

    This is done by starting with an all direct depot layout,
    looking at the vehicle count, creating an "all line" layout and then turning some of these lines into direct
    areas until the vehicle count of the all direct depot layout (+ an allowance) is reached.

    :param scenario: The scenario for which the depot layout should be generated.
    :param charging_power: The charging power for the line areas in kW.
    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
        :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a valid database
        URL.
    :param delete_existing_depot: Whether to delete an existing depot layout for this scenario. If set to False and a
        depot layout already exists, a ValueError will be raised.
    :param charging_power_direct: The charging power for the direct areas in kW. If not set, the charging power for the
        line areas will be used.

    :return: None. The depot layout will be added to the database.
    """
    logging.basicConfig(level=logging.DEBUG)  # TODO: Remove this line
    logger = logging.getLogger(__name__)

    with create_session(scenario, database_url) as (session, scenario):
        # STEP 0: Delete existing depots if asked to, raise an Exception otherwise
        if session.query(Depot).filter(Depot.scenario_id == scenario.id).count() != 0:
            if delete_existing_depot is False:
                raise ValueError("Depot already exists.")
            delete_depots(scenario, session)

        # Make sure that the consumption simulation has been run
        if session.query(Event).filter(Event.scenario_id == scenario.id).count() == 0:
            raise ValueError(
                "No consumption simulation found. Please run the consumption simulation first."
            )

        # STEP 2: Identify the spots where we will put a depot
        # Identify all the spots that serve as start *and* end of a rotation
        depot_stations_and_vehicle_types = group_rotations_by_start_end_stop(
            scenario.id, session
        )

        # STEP 3: Put "all direct" depots at these spots and find the vehicle counts
        depot_stations = []
        vehicle_type_rotation_dict_by_station: Dict[
            Station, Dict[VehicleType, List[Rotation]]
        ] = dict()
        for (
            first_last_stop_tup,
            vehicle_type_rotation_dict,
        ) in depot_stations_and_vehicle_types.items():
            first_stop, last_stop = first_last_stop_tup
            if first_stop != last_stop:
                raise ValueError("First and last stop of a rotation are not the same.")
            depot_stations.append(first_stop)
            vehicle_type_rotation_dict_by_station[
                first_stop
            ] = vehicle_type_rotation_dict

        del first_last_stop_tup, vehicle_type_rotation_dict

        all_direct_counts: Dict[
            Station, Dict[VehicleType, int]
        ] = vehicle_counts_for_direct_layout(
            CLEAN_DURATION=CLEAN_DURATION,
            charging_power=charging_power,
            stations=depot_stations,
            scenario=scenario,
            session=session,
            vehicle_type_dict_by_station=vehicle_type_rotation_dict_by_station,
            shunting_duration=shunting_duration,
        )

        # STEP 4: Run the simulation with depots that also have a lot of line areas
        # I know I could probably skip step 3 and go directly to step 4, but that's how I got it working and
        # I'm too lazy to change it now

        for station, vehicle_type_and_counts in all_direct_counts.items():
            line_counts: Dict[VehicleType, int] = dict()
            direct_counts: Dict[VehicleType, int] = dict()

            # Create a Depot that has a lot of line areas as well
            for vehicle_type, count in vehicle_type_and_counts.items():
                line_counts[vehicle_type] = ceil(count / line_length)
                direct_counts[vehicle_type] = ceil(count) + 500

            # Run the simulation with this depot
            generate_line_depot_layout(
                CLEAN_DURATION=CLEAN_DURATION,
                charging_power=charging_power,
                station=station,
                scenario=scenario,
                session=session,
                direct_counts=direct_counts,
                line_counts=line_counts,
                line_length=line_length,
                vehicle_type_rotation_dict=vehicle_type_rotation_dict_by_station[
                    station
                ],
                shunting_duration=shunting_duration,
            )

        # Simulate the depot
        # We will not be using add_evaluation_to_database instead taking the vehicle counts directly from the `ev` object
        logger.info("Simulating the scenario")
        logger.info("1/2: Initializing the simulation host")
        simulation_host = init_simulation(
            scenario=scenario,
            session=session,
        )
        logger.info("2/2: Running the simulation")
        depot_evaluations = run_simulation(simulation_host)

        # We need to remember the depot-id-station mapping
        depot_id_station_mapping: Dict[str, Station] = dict()
        for depot_id_as_str, ev in depot_evaluations.items():
            station = (
                session.query(Station)
                .join(Depot)
                .filter(Depot.id == int(depot_id_as_str))
                .one()
            )
            depot_id_station_mapping[depot_id_as_str] = station

        # Delete the old depot
        delete_depots(scenario, session)

        for depot_id_as_str, ev in depot_evaluations.items():
            assert isinstance(ev, DepotEvaluation)

            if False:
                ev.path_results = depot_id_as_str
                os.makedirs(depot_id_as_str, exist_ok=True)

                ev.vehicle_periods(
                    periods={
                        "depot general": "darkgray",
                        "park": "lightgray",
                        "Arrival Cleaning": "steelblue",
                        "Charging": "forestgreen",
                        "Standby Pre-departure": "darkblue",
                        "precondition": "black",
                        "trip": "wheat",
                    },
                    save=True,
                    show=False,
                    formats=(
                        "pdf",
                        "png",
                    ),
                    show_total_power=True,
                    show_annotates=True,
                )

            # Find the actual utilization.
            utilization: Dict[str, int] = real_peak_area_utilization(ev)
            utilization = {
                session.query(VehicleType).filter(VehicleType.id == int(k)).one(): v
                for k, v in utilization.items()
            }

            # Turn utilization into a two dictionaries, one for line areas and one for direct areas
            for vehicle_type, counts in utilization.items():
                line_counts[vehicle_type] = counts[AreaType.LINE]
                direct_counts[vehicle_type] = counts[AreaType.DIRECT_ONESIDE] + 100

            station = depot_id_station_mapping[depot_id_as_str]

            generate_line_depot_layout(
                CLEAN_DURATION=CLEAN_DURATION,
                charging_power=charging_power,
                station=station,
                scenario=scenario,
                session=session,
                direct_counts=direct_counts,
                line_counts=line_counts,
                line_length=line_length,
                vehicle_type_rotation_dict=vehicle_type_rotation_dict_by_station[
                    station
                ],
                shunting_duration=shunting_duration,
            )


def vehicle_counts_for_direct_layout(
    CLEAN_DURATION: int,
    charging_power: float,
    stations: List[Station],
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    vehicle_type_dict_by_station: Dict[Station, Dict[VehicleType, List[Rotation]]],
    shunting_duration: timedelta = timedelta(minutes=5),
) -> Dict[Station, Dict[VehicleType, int]]:
    """
    Generate a simple depot, simulate it and return the number of vehicles for each vehicle type.

    Do this for each depot station in the scenario.
    :param CLEAN_DURATION: The duration of the cleaning process in seconds.
    :param charging_power: The charging power of the charging area in kW.
    :param station: The stop where the depot is located.
    :param scenario: The scenario for which the depot layout should be generated.
    :param session: The SQLAlchemy session object.
    :param vehicle_type_dict: A dictionary with vehicle types as keys and rotations as values.
    :return: A dictionary with vehicle types as keys and the number of vehicles as values.
    """
    logger = logging.getLogger(__name__)

    for station in stations:
        logger.info(f"Generating all direct depot layout at {station.name}")
        # Generate the depot
        direct_counts = {}
        line_counts = {}
        for vehicle_type, rotations in vehicle_type_dict_by_station[station].items():
            direct_counts[vehicle_type] = len(rotations)
            line_counts[vehicle_type] = 0

        generate_line_depot_layout(
            CLEAN_DURATION=CLEAN_DURATION,
            charging_power=charging_power,
            station=station,
            scenario=scenario,
            session=session,
            direct_counts=direct_counts,
            line_counts=line_counts,
            line_length=8,  # We don't care about the line length here
            vehicle_type_rotation_dict=vehicle_type_dict_by_station[station],
            shunting_duration=shunting_duration,
        )

    # Simulate the scenario
    # We will not be using add_evaluation_to_database instead taking the vehicle counts directly from the `ev` object
    logger.info("Simulating the scenario")
    logger.info("1/2: Initializing the simulation host")
    simulation_host = init_simulation(
        scenario=scenario,
        session=session,
    )
    logger.info("2/2: Running the simulation")
    depot_evaluations = run_simulation(simulation_host)

    assert len(depot_evaluations) == len(stations)
    depot_evaluations: Dict[str, DepotEvaluation]

    ret_val: Dict[Station, Dict[VehicleType, int]] = dict()

    for depot_id_as_str, ev in depot_evaluations.items():
        assert isinstance(ev, DepotEvaluation)
        counts: Dict[str, int] = real_peak_vehicle_count(ev)
        # The key of the dictionary is the vehicle type ID as a string. We need to convert it to a vehicle type object
        vehicle_type_dict = {
            session.query(VehicleType).filter(VehicleType.id == int(k)).one(): v
            for k, v in counts.items()
        }

        # Find the station object
        station = (
            session.query(Station)
            .join(Depot)
            .filter(Depot.id == int(depot_id_as_str))
            .one()
        )

        ret_val[station] = vehicle_type_dict

    # Delete the old depots
    delete_depots(scenario, session)

    return ret_val


def generate_depot_layout(
    scenario: Union[Scenario, int, Any],
    charging_power: float = 150,
    database_url: Optional[str] = None,
    delete_existing_depot: bool = False,
) -> None:
    """
    Generates one or more depots for the scenario.

    First, the rotations are scanned to identify all the spots that serve as start *and* end of a rotation. Then the set
    of rotations for these spots are checked for vehicle types that are used there. Next, the amount of vehicles that
    are simultaneously present at the depot is calculated. Then a depot layout with an arrival and a charging area for
    each vehicle type is created. The capacity of each area is taken from the calculated amount of vehicles.
    The depot layout is then added to the database.

    A default plan will also be generated, which includes the following default processes: standby_arrival, cleaning,
    charging and standby_departure. Each vehicle will be processed with this exact order (standby_arrival is optional
    because it only happens if a vehicle needs to wait for the next process).

    The function only deletes the depot if the `delete_existing_depot` parameter is set to True. If there is already a
    depot existing in this scenario and this parameter is set to False, a ValueError will be raised.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
        an integer specifying the ID of a scenario in the database. Or any other object that has an attribute
        ``id`` that is an integer. If no :class:`eflips.model.Scenario` object is passed, the ``database_url``
        parameter must be set to a valid database URL ot the environment variable ``DATABASE_URL`` must be set to a
        valid database URL.

    :param charging_power: the charging power of the charging area in kW

    :param delete_existing_depot: if there is already a depot existing in this scenario, set True to delete this
        existing depot. Set to False and a ValueError will be raised if there is a depot in this scenario.

    :return: None. The depot layout will be added to the database.
    """
    CLEAN_DURATION = 30 * 60  # 30 minutes in seconds

    with create_session(scenario, database_url) as (session, scenario):
        # Handles existing depot
        if session.query(Depot).filter(Depot.scenario_id == scenario.id).count() != 0:
            if delete_existing_depot is False:
                raise ValueError("Depot already exists.")
            delete_depots(scenario, session)

        # Identify all the spots that serve as start *and* end of a rotation
        for (
            first_last_stop_tup,
            vehicle_type_dict,
        ) in group_rotations_by_start_end_stop(scenario.id, session).items():
            first_stop, last_stop = first_last_stop_tup
            if first_stop != last_stop:
                raise ValueError("First and last stop of a rotation are not the same.")
            _generate_all_direct_depot(
                CLEAN_DURATION,
                charging_power,
                first_stop,
                scenario,
                session,
                vehicle_type_dict,
            )


def apply_even_smart_charging(
    scenario: Union[Scenario, int, Any],
    database_url: Optional[str] = None,
    standby_departure_duration: timedelta = timedelta(minutes=5),
) -> None:
    """
    Takes a scenario where depot simulation has been run and applies smart charging to the depot.

    This modifies the time and power of the charging events in the database. The arrival and departure times and SoCs at
    these times are not modified.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.

    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
        :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a valid database
        URL.

    :param standby_departure_duration: The duration of the STANDBY_DEPARTURE event. This is the time the vehicle is
        allowed to wait at the depot before it has to leave. The default is 5 minutes.

    :return: None. The results are added to the database.
    """
    logger = logging.getLogger(__name__)

    with create_session(scenario, database_url) as (session, scenario):
        depots = session.query(Depot).filter(Depot.scenario_id == scenario.id).all()
        for depot in depots:
            # Load all the charging events at this depot
            charging_events = (
                session.query(Event)
                .join(Area)
                .filter(Area.depot_id == depot.id)
                .filter(Event.event_type == EventType.CHARGING_DEPOT)
                .all()
            )

            # For each event, take the subsequent STANDBY_DEPARTURE event of the same vehicle
            # Reduce the STANDBY_DEPARTURE events duration to 5 minutes
            # Move the end time of the charging event to the start time of the STANDBY_DEPARTURE event
            for charging_event in charging_events:
                next_event = (
                    session.query(Event)
                    .filter(Event.time_start >= charging_event.time_end)
                    .filter(Event.vehicle_id == charging_event.vehicle_id)
                    .order_by(Event.time_start)
                    .first()
                )

                if (
                    next_event is None
                    or next_event.event_type != EventType.STANDBY_DEPARTURE
                ):
                    logger.info(
                        f"Event {charging_event.id} has no STANDBY_DEPARTURE event after a CHARGING_DEPOT "
                        f"event. No room for smart charging."
                    )
                    continue

                assert next_event.time_start == charging_event.time_end

                if (
                    next_event.time_end - next_event.time_start
                ) > standby_departure_duration:
                    next_event.time_start = (
                        next_event.time_end - standby_departure_duration
                    )
                    session.flush()
                    # Add a timeseries to the charging event
                    assert charging_event.timeseries is None
                    charging_event.timeseries = {
                        "time": [
                            charging_event.time_start.isoformat(),
                            charging_event.time_end.isoformat(),
                            next_event.time_start.isoformat(),
                        ],
                        "soc": [
                            charging_event.soc_start,
                            charging_event.soc_end,
                            charging_event.soc_end,
                        ],
                    }
                    charging_event.time_end = next_event.time_start
                    session.flush()

            optimize_charging_events_even(charging_events)


def simulate_scenario(
    scenario: Union[Scenario, int, Any],
    repetition_period: Optional[timedelta] = None,
    database_url: Optional[str] = None,
    smart_charging_strategy: SmartChargingStrategy = SmartChargingStrategy.EVEN,
) -> None:
    """
    This method simulates a scenario and adds the results to the database.

    It fills in the "Charging Events" in the :class:`eflips.model.Event` table and associates
    :class:`eflips.model.Vehicle` objects with all the existing "Driving Events" in the :class:`eflips.model.Event`
    table.

    :param scenario: Either a :class:`eflips.model.Scenario` object containing the input data for the simulation. Or
        an integer specifying the ID of a scenario in the database. Or any other object that has an attribute
        ``id`` that is an integer. If no :class:`eflips.model.Scenario` object is passed, the ``database_url``
        parameter must be set to a valid database URL ot the environment variable ``DATABASE_URL`` must be set to a
        valid database URL.

    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. This
        is needed because the result should be a steady-state result. THis can only be achieved by simulating a
        time period before and after our actual simulation, and then only using the "middle". eFLIPS tries to
        automatically detect whether the schedule should be repeated daily or weekly. If this fails, a ValueError is
        raised and repetition needs to be specified manually.

    :param database_url: An optional database URL. If no database URL is passed and the `scenario` parameter is not a
        :class:`eflips.model.Scenario` object, the environment variable `DATABASE_URL` must be set to a valid database
        URL.

    :param smart_charging_strategy: An optional parameter specifying the smart charging strategy to be used. The
        default is SmartChargingStragegy.NONE. The following strategies are available:
        - SmartChargingStragegy.NONE: Do not use smart charging. Buses are charged with the maximum power available,
        from the time they arrive at the depot until they are full (or leave the depot).
        - SmartChargingStragegy.EVEN: Use smart charging with an even distribution of charging power over the time the
        bus is at the depot. This aims to minimize the peak power demand.

    :return: Nothing. The results are added to the database.
    """
    with create_session(scenario, database_url) as (session, scenario):
        simulation_host = init_simulation(
            scenario=scenario,
            session=session,
            repetition_period=repetition_period,
        )
        ev = run_simulation(simulation_host)
        add_evaluation_to_database(scenario, ev, session)

        match smart_charging_strategy:
            case SmartChargingStrategy.NONE:
                pass
            case SmartChargingStrategy.EVEN:
                apply_even_smart_charging(scenario, database_url)
            case SmartChargingStrategy.MIN_PRICE:
                raise NotImplementedError("MIN_PRICE strategy is not implemented yet.")
            case _:
                raise NotImplementedError()


def _init_simulation(
    scenario: Scenario,
    session: Session,
    repetition_period: Optional[timedelta] = None,
    vehicle_count_dict: Optional[Dict[str, int]] = None,
) -> SimulationHost:
    """Deprecated stub for init_simulation."""
    raise NotImplementedError(
        "The function _init_simulation is deprecated. Please use init_simulation instead."
    )


def init_simulation(
    scenario: Scenario,
    session: Session,
    repetition_period: Optional[timedelta] = None,
    vehicle_count_dict: Optional[Dict[str, Dict[str, int]]] = None,
) -> SimulationHost:
    """
    This methods checks the input data for consistency, initializes a simulation host object and returns it.

    The simulation host object can then be passed to :func:`run_simulation()`.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.

    :param session: A SQLAlchemy session object.

    :param repetition_period: An optional timedelta object specifying the period of the vehicle schedules. This
        is needed because the *result* should be a steady-state result. THis can only be achieved by simulating a
        time period before and after our actual simulation, and then only using the "middle". eFLIPS tries to
        automatically detect whether the schedule should be repeated daily or weekly. If this fails, a ValueError is
        raised and repetition needs to be specified manually.

    :param vehicle_count_dict: An optional dictionary specifying the number of vehicles for each vehicle type for each
        depot. The dictionary should have the following structure:

         ::

            {
                "1" (depot.id as str): {
                    "1" (vehicle_type.id as str): 10,
                    "2" (vehicle_type.id as str): 20,
                    ...
                },
                "2" (depot.id as str): {
                    "1" (vehicle_type.id as str): 10,
                    "2" (vehicle_type.id as str): 20,
                    ...
                },

    :return: A :class:`eflips.depot.Simulation.SimulationHost` object. This object should be reagrded as a "black box"
        by the user. It should be passed to :func:`run_simulation()` to run the simulation and obtain the results.
    """

    # Clear the eflips settings
    eflips.settings.reset_settings()

    path_to_this_file = os.path.dirname(__file__)

    # Step 1: Set up the depot
    eflips_depots = []
    for depot in session.query(Depot).filter(Depot.scenario_id == scenario.id).all():
        # Step 1.5: Check validity of a depot
        check_depot_validity(depot)

        depot_dict = depot_to_template(depot)
        eflips_depots.append(
            eflips.depot.Depotinput(filename_template=depot_dict, show_gui=False)
        )

    # Step 2: eFLIPS initialization
    simulation_host = SimulationHost(eflips_depots, print_timestamps=False)
    # Now we do what is done in `standard_setup()` from the old input files
    # Load the settings
    path_to_default_settings = os.path.join(
        path_to_this_file, "defaults", "default_settings"
    )
    eflips.load_settings(path_to_default_settings)

    # Step 3: Set up the vehicle schedules
    # Turn rotations into vehicleschedules
    # Turn API VehicleSchedule objects into eFLIPS TimeTable object
    # Get correctly repeated vehicle schedules
    # if total duration time is 1 or 2 days, vehicle schedule will be repeated daily
    # if total duration time is 7 or 8 days, vehicle schedule will be repeated weekly
    # However, we need to override quite a lot of the settings
    # The ["general"]["SIMULATION_TIME"] entry is calculated from the difference between the first and last departure
    # time in the vehicle schedule
    vehicle_schedules = [
        VehicleSchedule.from_rotation(
            rotation,
            scenario=scenario,
            session=session,
        )
        for rotation in session.query(Rotation)
        .filter(Rotation.scenario_id == scenario.id)
        .all()
    ]

    first_departure_time = min(
        [vehicle_schedule.departure for vehicle_schedule in vehicle_schedules]
    )
    last_arrival_time = max(
        [vehicle_schedule.arrival for vehicle_schedule in vehicle_schedules]
    )

    # We take first arrival time as simulation start
    total_duration = (last_arrival_time - first_departure_time).total_seconds()
    schedule_duration_days = ceil(total_duration / (24 * 60 * 60))

    if repetition_period is None and schedule_duration_days in [1, 2]:
        repetition_period = timedelta(days=1)
    elif repetition_period is None and schedule_duration_days in [7, 8]:
        repetition_period = timedelta(weeks=1)
    elif repetition_period is None:
        raise ValueError(
            "Could not automatically detect repetition period. Please specify manually."
        )

    # Now, we need to repeat the vehicle schedules

    vehicle_schedules = repeat_vehicle_schedules(vehicle_schedules, repetition_period)

    sim_start_stime, total_duration_seconds = start_and_end_times(vehicle_schedules)
    eflips.globalConstants["general"]["SIMULATION_TIME"] = int(total_duration_seconds)

    eflips.globalConstants["general"]["SIMULATION_START_DATETIME"] = sim_start_stime

    timetable = VehicleSchedule._to_timetable(
        vehicle_schedules, simulation_host.env, sim_start_stime
    )
    simulation_host.timetable = timetable

    # Step 4: Set up the vehicle types
    # Clear old vehicle counts, if they exist
    eflips.globalConstants["depot"]["vehicle_count"] = {}

    # We need to calculate roughly how many vehicles we need for each depot
    for depot in session.query(Depot).filter(Depot.scenario_id == scenario.id).all():
        depot_id = str(depot.id)
        eflips.globalConstants["depot"]["vehicle_count"][depot_id] = {}
        vehicle_types_for_depot = set(str(area.vehicle_type_id) for area in depot.areas)
        if "None" in vehicle_types_for_depot:
            vehicle_types_for_depot.remove("None")

        # If we have a vehicle count dictionary, we validate and use ir
        if vehicle_count_dict is not None and depot_id in vehicle_count_dict.keys():
            if set(vehicle_count_dict[depot_id].keys()) < vehicle_types_for_depot:
                raise ValueError(
                    "The vehicle count dictionary does not contain all vehicle types for depot {depot_id}."
                )
            eflips.globalConstants["depot"]["vehicle_count"][
                depot_id
            ] = vehicle_count_dict[depot_id]
        else:
            # Calculate it from the size of the charging area with a 2x margin

            for vehicle_type in vehicle_types_for_depot:
                vehicle_count = 0
                for area in depot.areas:
                    if area.vehicle_type_id == int(vehicle_type):
                        # TODO potential edit if we make vehicle type of an area a list
                        for p in area.processes:
                            if p.electric_power is not None and p.duration is None:
                                vehicle_count += area.capacity

                assert (
                    vehicle_count > 0
                ), f"The charging area capacity for vehicle type {vehicle_type} should not be 0."

                eflips.globalConstants["depot"]["vehicle_count"][depot_id][
                    vehicle_type
                ] = (vehicle_count * 2)

    # We  need to put the vehicle type objects into the GlobalConstants
    for vehicle_type in (
        session.query(VehicleType).filter(VehicleType.scenario_id == scenario.id).all()
    ):
        eflips.globalConstants["depot"]["vehicle_types"][
            str(vehicle_type.id)
        ] = vehicle_type_to_global_constants_dict(vehicle_type)

    # Step 5: Final checks and setup
    # Run the eflips validity checks
    eflips.depot.settings_config.check_gc_validity()

    # Complete the eflips settings
    eflips.depot.settings_config.complete_gc()

    # Set up the depots
    for dh, di in zip(simulation_host.depot_hosts, simulation_host.to_simulate):
        dh.load_and_complete_template(di.filename_template)

    simulation_host.complete()

    return simulation_host


def _run_simulation(simulation_host: SimulationHost) -> DepotEvaluation:
    """Deprecated stub for run_simulation."""
    raise NotImplementedError(
        "The function _run_simulation is deprecated. Please use run_simulation instead."
    )


def run_simulation(simulation_host: SimulationHost) -> Dict[str, DepotEvaluation]:
    """Run simulation and return simulation results.

    :param simulation_host: A "black box" object containing all input data for the simulation.

    :return: A dictionary of :class:`eflips.depot.evaluation.DepotEvaluation` objects. The keys are the depot IDs, as
        strings.
    """
    simulation_host.run()

    results = {}
    for depot_host in simulation_host.depot_hosts:
        depot_id = depot_host.depot.ID
        ev = depot_host.evaluation

        # We need to clean up the timetable, it has trips from all depots
        ev.timetable = copy.copy(ev.timetable)
        ev.timetable.trips = [
            t for t in ev.timetable.trips if t.destination.ID == depot_id
        ]
        ev.timetable.trips_issued = [
            t for t in ev.timetable.trips_issued if t.destination.ID == depot_id
        ]
        ev.timetable.all_trips = [
            t for t in ev.timetable.all_trips if t.destination.ID == depot_id
        ]

        # We also need to clean up the vehicle generator
        ev.vehicle_generator = copy.copy(ev.vehicle_generator)
        ev.vehicle_generator.items = copy.copy(ev.vehicle_generator.items)
        indizes_to_remove = []
        for i in range(len(ev.vehicle_generator.items)):
            vehicle = copy.copy(ev.vehicle_generator.items[i])
            depot_ids = [trip.destination.ID for trip in vehicle.finished_trips]
            if len(set(depot_ids)) > 1:
                raise ValueError("Vehicle has finished trips in multiple depots.")
            if len(depot_ids) == 0 or depot_ids[0] != depot_id:
                indizes_to_remove.append(i)

        for index in sorted(indizes_to_remove, reverse=True):
            del ev.vehicle_generator.items[index]

        results[depot_id] = ev

    return results


def _add_evaluation_to_database(
    scenario_id: int,
    depot_evaluation: DepotEvaluation,
    session: sqlalchemy.orm.Session,
) -> None:
    """Deprecated stub for add_evaluation_to_database."""
    raise NotImplementedError(
        "The function _add_evaluation_to_database is deprecated. Please use add_evaluation_to_database instead."
    )


def insert_dummy_standby_departure_events(
    depot_id: int, session: Session, sim_time_end: Optional[datetime.datetime] = None
) -> None:
    """
    Workaround for the missing STANDBY_DEPARTURE events in the database.

    :param session: The database session
    :param scenario: A scenario object
    :param sim_time_end: The end time of the simulation. If None, final events might not be properly handled.
    :return:
    """
    logger = logging.getLogger(__name__)

    # Look for charging events at areas belonging to the depot
    charging_events = (
        session.query(Event)
        .join(Area)
        .filter(Area.depot_id == depot_id)
        .filter(Event.event_type == EventType.CHARGING_DEPOT)
        .all()
    )

    for charging_event in charging_events:
        # See if the next event is a DRIVING event, but there is time between the two events
        next_event = (
            session.query(Event)
            .filter(Event.time_start >= charging_event.time_end)
            .filter(Event.vehicle_id == charging_event.vehicle_id)
            .order_by(Event.time_start)
            .first()
        )
        if (
            next_event is not None
            and next_event.event_type == EventType.DRIVING
            and (next_event.time_start - charging_event.time_end) > timedelta(seconds=1)
        ):
            logger.warning("Inserting dummy STANDBY_DEPARTURE event")
            # Insert a dummy STANDBY_DEPARTURE event
            dummy_event = Event(
                vehicle_id=charging_event.vehicle_id,
                vehicle_type_id=charging_event.vehicle.vehicle_type_id,
                time_start=charging_event.time_end,
                time_end=(next_event.time_start - timedelta(seconds=1)),
                event_type=EventType.STANDBY_DEPARTURE,
                area_id=charging_event.area_id,
                subloc_no=charging_event.subloc_no,
                scenario_id=charging_event.scenario_id,
                soc_start=charging_event.soc_end,
                soc_end=charging_event.soc_end,
                description="Dummy STANDBY_DEPARTURE event",
            )
            session.add(dummy_event)
        elif next_event is None and sim_time_end is not None:
            # If the event's end is before the simulation end, insert a dummy STANDBY_DEPARTURE event
            # From the end of the charging event to the end of the simulation
            logger.warning("Inserting dummy STANDBY_DEPARTURE event")
            if charging_event.time_end < sim_time_end:
                dummy_event = Event(
                    vehicle_id=charging_event.vehicle_id,
                    vehicle_type_id=charging_event.vehicle.vehicle_type_id,
                    time_start=charging_event.time_end,
                    time_end=sim_time_end,
                    event_type=EventType.STANDBY_DEPARTURE,
                    area_id=charging_event.area_id,
                    subloc_no=charging_event.subloc_no,
                    scenario_id=charging_event.scenario_id,
                    soc_start=charging_event.soc_end,
                    soc_end=charging_event.soc_end,
                    description="Dummy STANDBY_DEPARTURE event",
                )
                session.add(dummy_event)


def add_evaluation_to_database(
    scenario: Scenario,
    depot_evaluations: Dict[str, DepotEvaluation],
    session: sqlalchemy.orm.Session,
) -> None:
    """
    This method adds a simulation result to the database.

    It reads the simulation results from the :class:`eflips.depot.evaluation.DepotEvaluation` object and  adds them into
     the database. Tables of Event, Rotation and Vehicle will be updated.

    :param scenario: A :class:`eflips.model.Scenario` object containing the input data for the simulation.

    :param depot_evaluations: A dictionary of :class:`eflips.depot.evaluation.DepotEvaluation` objects. The keys are
        the depot IDs, as strings.

    :param session: a SQLAlchemy session object. This is used to add all the simulation results to the
        database.

    :return: Nothing. The results are added to the database.
    """

    # Read simulation start time

    for depot_id, depot_evaluation in depot_evaluations.items():
        simulation_start_time = depot_evaluation.sim_start_datetime

        # Depot-layer operations

        list_of_assigned_schedules = []

        waiting_area_id = None

        total_areas = session.query(Area).filter(Area.scenario_id == scenario.id).all()
        for area in total_areas:
            if area.depot_id == int(depot_id) and len(area.processes) == 0:
                waiting_area_id = area.id

        assert isinstance(waiting_area_id, int) and waiting_area_id > 0, (
            f"Waiting area id should be an integer greater than 0. For every depot there must be at least "
            f"one waiting area."
        )

        for current_vehicle in depot_evaluation.vehicle_generator.items:
            # Vehicle-layer operations

            vehicle_type_id = int(current_vehicle.vehicle_type.ID)

            current_vehicle_db = Vehicle(
                vehicle_type_id=vehicle_type_id,
                scenario=scenario,
                name=current_vehicle.ID,
                name_short=None,
            )

            # Flush the vehicle object to get the vehicle id
            session.add(current_vehicle_db)
            session.flush()

            dict_of_events = OrderedDict()

            (
                schedule_current_vehicle,
                earliest_time,
                latest_time,
                # Earliest and latest time defines a time window, only the events within this time window will be
                # handled. It is usually the departure time of the last copy trip in the "early-shifted" copy
                # schedules and the departure time of the first copy trip in the "late-shifted" copy schedules.
            ) = _get_finished_schedules_per_vehicle(
                dict_of_events, current_vehicle.finished_trips, current_vehicle_db.id
            )

            try:
                assert earliest_time is not None and latest_time is not None

            except AssertionError as e:
                warnings.warn(
                    f"Vehicle {current_vehicle_db.id} has only copied trips. The profiles of this vehicle "
                    f"will not be written into database."
                )
                continue

            assert (
                earliest_time < latest_time
            ), f"Earliest time {earliest_time} is not less than latest time {latest_time}."

            list_of_assigned_schedules.extend(schedule_current_vehicle)

            _generate_vehicle_events(
                dict_of_events,
                current_vehicle,
                waiting_area_id,
                earliest_time,
                latest_time,
            )

            # Python passes dictionaries by reference

            _complete_standby_departure_events(dict_of_events, latest_time)

            _add_soc_to_events(dict_of_events, current_vehicle.battery_logs)

            try:
                assert (not dict_of_events) is False
            except AssertionError as e:
                warnings.warn(
                    f"Vehicle {current_vehicle_db.id} has no valid events. The vehicle will not be written "
                    f"into database."
                )

                continue

            _add_events_into_database(
                current_vehicle_db,
                dict_of_events,
                session,
                scenario,
                simulation_start_time,
            )

        # Postprocessing of events
        _update_vehicle_in_rotation(session, scenario, list_of_assigned_schedules)
        _update_waiting_events(session, scenario, waiting_area_id)


def _get_finished_schedules_per_vehicle(
    dict_of_events, list_of_finished_trips: List, db_vehicle_id: int
):
    """
    This function completes the following tasks:

    1. It gets the finished non-copy schedules of the current vehicle,
    which will be used in :func:`_update_vehicle_in_rotation()`.

    2. It fills the dictionary of events with the trip_ids of the current vehicle.

    3. It returns an earliest and a latest time according to this vehicle's schedules. Only processes happening within
    this time window will be handled later.

    Usually the earliest time is the departure time of the last copy trip in the "early-shifted" copy schedules
    and the lastest time is the departure time of the first copy trip in the "late-shifted" copy schedules.

    # If the vehicle's first trip is a non-copy trip, the earliest time is the departure time of the first trip. If the
    # vehicle's last trip is a non-copy trip, the latest time is the departure time of the last trip.

    :param dict_of_events: An ordered dictionary storing the data related to an event. The keys are the start times of
        the events.
    :param list_of_finished_trips: A list of finished trips of a vehicle directly from
        :class:`eflips.depot.simple_vehicle.SimpleVehicle` object.

    :param db_vehicle_id: The vehicle id in the database.

    :return: A tuple of three elements. The first element is a list of finished schedules of the vehicle. The second and
        third elements are the earliest and latest time of the vehicle's schedules.
    """
    finished_schedules = []

    list_of_finished_trips.sort(key=lambda x: x.atd)
    earliest_time = None
    latest_time = None

    for i in range(len(list_of_finished_trips)):
        assert list_of_finished_trips[i].atd == list_of_finished_trips[i].std, (
            "The trip {current_trip.ID} is delayed. The simulation doesn't "
            "support delayed trips for now."
        )

        if list_of_finished_trips[i].is_copy is False:
            current_trip = list_of_finished_trips[i]

            finished_schedules.append((int(current_trip.ID), db_vehicle_id))
            dict_of_events[current_trip.atd] = {
                "type": "Trip",
                "id": int(current_trip.ID),
            }
            if i == 0:
                raise ValueError(
                    f"New Vehicle required for the trip {current_trip.ID}, which suggests the fleet or the "
                    f"infrastructure might not be enough for the full electrification. Please add charging "
                    f"interfaces or increase charging power ."
                )

            elif i != 0 and i == len(list_of_finished_trips) - 1:
                # Vehicle's last trip is a non-copy trip
                if earliest_time is None:
                    earliest_time = list_of_finished_trips[i - 1].ata
                latest_time = list_of_finished_trips[i].ata

            else:
                if list_of_finished_trips[i - 1].is_copy is True:
                    earliest_time = list_of_finished_trips[i - 1].ata
                if list_of_finished_trips[i + 1].is_copy is True:
                    latest_time = list_of_finished_trips[i + 1].atd

    return finished_schedules, earliest_time, latest_time


def _generate_vehicle_events(
    dict_of_events,
    current_vehicle: SimpleVehicle,
    virtual_waiting_area_id: int,
    earliest_time: datetime.datetime,
    latest_time: datetime.datetime,
) -> None:
    """
    This function generates and ordered dictionary storing the data related to an event.

    It returns a dictionary. The keys are the start times of the
    events. The values are also dictionaries containing:
    - type: The type of the event.
    - end: The end time of the event.
    - area: The area id of the event.
    - slot: The slot id of the event.
    - id: The id of the event-related process.

    For trips, only the type is stored.

    For waiting events, the slot is not stored for now.

    :param current_vehicle: a :class:`eflips.depot.simple_vehicle.SimpleVehicle` object.

    :param virtual_waiting_area_id: the id of the virtual waiting area. Vehicles waiting for the first process will park here.

    :param earliest_time: the earliest relevant time of the current vehicle. Any events earlier than this will not be
        handled.

    :param latest_time: the latest relevant time of the current vehicle. Any events later than this will not be handled.

    :return: None. The results are added to the dictionary.
    """

    logger = logging.getLogger(__name__)

    # For convenience
    area_log = current_vehicle.logger.loggedData["dwd.current_area"]
    slot_log = current_vehicle.logger.loggedData["dwd.current_slot"]
    waiting_log = current_vehicle.logger.loggedData["area_waiting_time"]

    # Handling waiting events
    waiting_log_timekeys = sorted(waiting_log.keys())

    for idx in range(len(waiting_log_timekeys)):
        waiting_end_time = waiting_log_timekeys[idx]

        # Only extract events if the time is within the upper mentioned range

        if earliest_time <= waiting_end_time <= latest_time:
            waiting_info = waiting_log[waiting_end_time]

            if waiting_info["waiting_time"] == 0:
                continue

            logger.info(
                f"Vehicle {current_vehicle.ID} has been waiting for {waiting_info['waiting_time']} seconds. "
            )

            start_time = waiting_end_time - waiting_info["waiting_time"]

            if waiting_info["area"] == waiting_log[waiting_log_timekeys[0]]["area"]:
                # if the vehicle is waiting for the first process, put it in the virtual waiting area
                waiting_area_id = virtual_waiting_area_id
            else:
                # If the vehicle is waiting for other processes,
                # put it in the area of the prodecessor process of the waited process.
                waiting_area_id = waiting_log[waiting_log_timekeys[idx - 1]]["area"]

            dict_of_events[start_time] = {
                "type": "Standby",
                "end": waiting_end_time,
                "area": waiting_area_id,
                "is_waiting": True,
            }

    # Create a list of battery log in order of time asc. Convenient for looking up corresponding soc

    for time_stamp, process_log in current_vehicle.logger.loggedData[
        "dwd.active_processes_copy"
    ].items():
        if earliest_time <= time_stamp <= latest_time:
            num_process = len(process_log)
            if num_process == 0:
                # A departure happens and this trip should already be stored in the dictionary
                pass
            else:
                for process in process_log:
                    current_area = area_log[time_stamp]
                    current_slot = slot_log[time_stamp]

                    if current_area is None or current_slot is None:
                        raise ValueError(
                            f"For process {process.ID} Area and slot should not be None."
                        )

                    match process.status:
                        case ProcessStatus.COMPLETED | ProcessStatus.CANCELLED:
                            assert (
                                len(process.starts) == 1 and len(process.ends) == 1
                            ), (
                                f"Current process {process.ID} is completed and should only contain one start and "
                                f"one end time."
                            )

                            if process.dur > 0:
                                # Valid duration
                                dict_of_events[time_stamp] = {
                                    "type": type(process).__name__,
                                    "end": process.ends[0],
                                    "area": current_area.ID,
                                    "slot": current_slot,
                                    "id": process.ID,
                                }
                            else:
                                # Duration is 0
                                assert current_area.issink is True, (
                                    f"A process with no duration could only "
                                    f"happen in the last area before dispatched"
                                )
                                if (
                                    time_stamp in dict_of_events.keys()
                                    and "end" in dict_of_events[time_stamp].keys()
                                ):
                                    start_this_event = dict_of_events[time_stamp]["end"]
                                    if start_this_event in dict_of_events.keys():
                                        if (
                                            dict_of_events[start_this_event]["type"]
                                            == "Trip"
                                        ):
                                            logger.info(
                                                f"Vehicle {current_vehicle.ID} must depart immediately after charged. "
                                                f"Thus there will be no STANDBY_DEPARTURE event."
                                            )

                                        else:
                                            raise ValueError(
                                                f"There is already an event "
                                                f"{dict_of_events[start_this_event]} at {start_this_event}."
                                            )

                                        continue

                                    dict_of_events[start_this_event] = {
                                        "type": type(process).__name__,
                                        "area": current_area.ID,
                                        "slot": current_slot,
                                        "id": process.ID,
                                    }

                        case ProcessStatus.IN_PROGRESS:
                            assert (
                                len(process.starts) == 1 and len(process.ends) == 0
                            ), f"Current process {process.ID} is marked IN_PROGRESS, but has an end."

                            if current_area is None or current_slot is None:
                                raise ValueError(
                                    f"For process {process.ID} Area and slot should not be None."
                                )

                            if process.dur > 0:
                                # Valid duration
                                dict_of_events[time_stamp] = {
                                    "type": type(process).__name__,
                                    "end": process.etc,
                                    "area": current_area.ID,
                                    "slot": current_slot,
                                    "id": process.ID,
                                }
                            else:
                                raise NotImplementedError(
                                    "We believe this should never happen. If it happens, handle it here."
                                )

                        # The following ProcessStatus possibly only happen while the simulation is running,
                        # not in the results
                        case ProcessStatus.WAITING:
                            raise NotImplementedError(
                                f"Current process {process.ID} is waiting. Not implemented yet."
                            )

                        case ProcessStatus.NOT_STARTED:
                            raise NotImplementedError(
                                f"Current process {process.ID} is not started. Not implemented yet."
                            )

                        case _:
                            raise ValueError(
                                f"Invalid process status {process.status} for process {process.ID}."
                            )


def _complete_standby_departure_events(
    dict_of_events: Dict, latest_time: datetime.datetime
) -> None:
    """
    This function completes the standby departure events by adding an end time to each standby departure event.

    :param dict_of_events: a dictionary containing the events of a vehicle. The keys are the start times of the events.

    :param latest_time: the latest relevant time of the current vehicle. Any events later than this will not be handled.

    :return: None. The results are added to the dictionary.
    """
    for i in range(len(dict_of_events.keys())):
        time_keys = sorted(dict_of_events.keys())

        process_dict = dict_of_events[time_keys[i]]
        if "end" not in process_dict and process_dict["type"] != "Trip":
            # End time of a standby_departure will be the start of the following trip
            if i == len(time_keys) - 1:
                # The event reaches simulation end
                end_time = latest_time
            else:
                end_time = time_keys[i + 1]

            process_dict["end"] = end_time


def _add_soc_to_events(dict_of_events, battery_log) -> None:
    """
    This function completes the soc of each event by looking up the battery log.

    :param dict_of_events: a dictionary containing the events of a vehicle. The keys are the start times of the events.

    :param battery_log: a list of battery logs of a vehicle.

    :return: None. The results are added to the dictionary.
    """
    battery_log_list = []
    for log in battery_log:
        battery_log_list.append((log.t, log.energy / log.energy_real))

    time_keys = sorted(dict_of_events.keys())
    for i in range(len(time_keys)):
        # Get soc
        soc_start = None
        soc_end = None
        start_time = time_keys[i]
        process_dict = dict_of_events[time_keys[i]]
        for j in range(len(battery_log_list)):
            # Access the correct battery log according to time since there is only one battery log for each time
            log = battery_log_list[j]

            if process_dict["type"] != "Trip":
                if log[0] == start_time:
                    soc_start = log[1]
                if log[0] == process_dict["end"]:
                    soc_end = log[1]
                if log[0] < start_time < battery_log_list[j + 1][0]:
                    soc_start = log[1]
                if log[0] < process_dict["end"] < battery_log_list[j + 1][0]:
                    soc_end = log[1]

                if soc_start is not None:
                    soc_start = min(soc_start, 1)  # so
                process_dict["soc_start"] = soc_start
                if soc_end is not None:
                    soc_end = min(soc_end, 1)  # soc should not exceed 1
                process_dict["soc_end"] = soc_end

            else:
                continue


def _add_events_into_database(
    db_vehicle, dict_of_events, session, scenario, simulation_start_time
) -> None:
    """
    This function generates :class:`eflips.model.Event` objects from the dictionary of events and adds them into the.

    database.

    :param db_vehicle: vehicle object in the database

    :param dict_of_events: dictionary containing the events of a vehicle. The keys are the start times of the events.

    :param session: a :class:`sqlalchemy.orm.Session` object for database connection.

    :param scenario: the current simulated scenario

    :param simulation_start_time: simulation start time in :class:`datetime.datetime` format

    :return: None. The results are added to the database.
    """
    logger = logging.getLogger(__name__)

    for start_time, process_dict in dict_of_events.items():
        # Generate EventType
        match process_dict["type"]:
            case "Serve":
                event_type = EventType.SERVICE
            case "Charge":
                event_type = EventType.CHARGING_DEPOT
            case "Standby":
                if (
                    "is_waiting" in process_dict.keys()
                    and process_dict["is_waiting"] is True
                ):
                    event_type = EventType.STANDBY
                else:
                    event_type = EventType.STANDBY_DEPARTURE
            case "Precondition":
                event_type = EventType.PRECONDITIONING
            case "Trip":
                continue
            case _:
                raise ValueError(
                    'Invalid process type %s. Valid process types are "Serve", "Charge", '
                    '"Standby", "Precondition"'
                )

        if process_dict["end"] == start_time:
            logger.warning("Refusing to create an event with zero duration.")
            continue

        current_event = Event(
            scenario=scenario,
            vehicle_type_id=db_vehicle.vehicle_type_id,
            vehicle=db_vehicle,
            station_id=None,
            area_id=int(process_dict["area"]),
            subloc_no=int(process_dict["slot"]) - 1
            if "slot" in process_dict.keys()
            else 00,
            trip_id=None,
            time_start=timedelta(seconds=start_time) + simulation_start_time,
            time_end=timedelta(seconds=process_dict["end"]) + simulation_start_time,
            soc_start=process_dict["soc_start"]
            if process_dict["soc_start"] is not None
            else process_dict["soc_end"],
            soc_end=process_dict["soc_end"]
            if process_dict["soc_end"] is not None
            else process_dict["soc_start"],  # if only one battery log is found,
            # then this is not an event with soc change
            event_type=event_type,
            description=process_dict["id"] if "id" in process_dict.keys() else None,
            timeseries=None,
        )

        session.add(current_event)


def _update_vehicle_in_rotation(session, scenario, list_of_assigned_schedules) -> None:
    """
    This function updates the vehicle id assigned to the rotations and deletes the events that are not depot events.

    :param session: a :class:`sqlalchemy.orm.Session` object for database connection.
    :param scenario: the current simulated scenario
    :param list_of_assigned_schedules: a list of tuples containing the rotation id and the vehicle id.
    :return: None. The results are added to the database.
    """
    # New rotation assignment
    for schedule_id, vehicle_id in list_of_assigned_schedules:
        # Get corresponding old vehicle id
        session.query(Rotation).filter(Rotation.id == schedule_id).update(
            {"vehicle_id": vehicle_id}, synchronize_session="auto"
        )

    # Delete all non-depot events
    session.query(Event).filter(
        Event.scenario == scenario,
        Event.trip_id.isnot(None) | Event.station_id.isnot(None),
    ).delete(synchronize_session="auto")

    session.flush()

    # Delete all vehicles without rotations
    vehicle_assigned_sq = (
        session.query(Rotation.vehicle_id)
        .filter(Rotation.scenario == scenario)
        .distinct()
        .subquery()
    )

    session.query(Vehicle).filter(Vehicle.scenario == scenario).filter(
        Vehicle.id.not_in(select(vehicle_assigned_sq))
    ).delete()

    session.flush()


def _update_waiting_events(session, scenario, waiting_area_id) -> None:
    """
    This function evaluates the capacity of waiting area and assigns the waiting events to corresponding slots in the.

    waiting area.

    :param session: a :class:`sqlalchemy.orm.Session` object for database connection.

    :param scenario: the current simulated scenario.

    :param waiting_area_id: id of the waiting area.

    :raise ValueError: if the waiting area capacity is less than the peak waiting occupancy.

    :return: None. The results are added to the database.
    """
    logger = logging.getLogger(__name__)

    # Process all the STANDBY (waiting) events #
    all_waiting_starts = (
        session.query(Event)
        .filter(
            Event.scenario_id == scenario.id,
            Event.event_type == EventType.STANDBY,
            Event.area_id == waiting_area_id,
        )
        .all()
    )

    all_waiting_ends = (
        session.query(Event)
        .filter(
            Event.scenario_id == scenario.id,
            Event.event_type == EventType.STANDBY,
            Event.area_id == waiting_area_id,
        )
        .all()
    )

    assert len(all_waiting_starts) == len(
        all_waiting_ends
    ), f"Number of waiting events starts {len(all_waiting_starts)} is not equal to the number of waiting event ends"

    if len(all_waiting_starts) == 0:
        logger.info(
            "No waiting events found. The depot has enough capacity for waiting. Change the waiting area capacity to 10 as buffer."
        )

        session.query(Area).filter(Area.id == waiting_area_id).update(
            {"capacity": 10}, synchronize_session="auto"
        )

        return

    list_waiting_timestamps = []
    for waiting_start in all_waiting_starts:
        list_waiting_timestamps.append(
            {"timestamp": waiting_start.time_start, "event": (waiting_start.id, 1)}
        )

    for waiting_end in all_waiting_ends:
        list_waiting_timestamps.append(
            {"timestamp": waiting_end.time_end, "event": (waiting_end.id, -1)}
        )

    list_waiting_timestamps.sort(key=lambda x: x["timestamp"])
    start_and_end_records = [wt["event"][1] for wt in list_waiting_timestamps]

    peak_waiting_occupancy = max(list(itertools.accumulate(start_and_end_records)))

    # Assuming that there is only one waiting area in each depot

    waiting_area_id = all_waiting_starts[0].area_id
    waiting_area = session.query(Area).filter(Area.id == waiting_area_id).first()
    if waiting_area.capacity > peak_waiting_occupancy:
        logger.info(
            f"Current waiting area capacity {waiting_area.capacity} "
            f"is greater than the peak waiting occupancy. Updating the capacity to {peak_waiting_occupancy}."
        )
        session.query(Area).filter(Area.id == waiting_area_id).update(
            {"capacity": peak_waiting_occupancy}, synchronize_session="auto"
        )
        session.flush()
    elif waiting_area.capacity < peak_waiting_occupancy:
        raise ValueError(
            f"Waiting area capacity is less than the peak waiting occupancy. "
            f"Waiting area capacity: {waiting_area.capacity}, peak waiting occupancy: {peak_waiting_occupancy}."
        )
    else:
        pass

    session.flush()

    # Update waiting slots
    virtual_waiting_area = [None] * peak_waiting_occupancy
    for wt in list_waiting_timestamps:
        # check in
        if wt["event"][1] == 1:
            for i in range(len(virtual_waiting_area)):
                if virtual_waiting_area[i] is None:
                    virtual_waiting_area[i] = wt["event"][0]
                    session.query(Event).filter(Event.id == wt["event"][0]).update(
                        {"subloc_no": i}, synchronize_session="auto"
                    )
                    break
        # check out
        else:
            for i in range(len(virtual_waiting_area)):
                if virtual_waiting_area[i] == wt["event"][0]:
                    current_waiting_event = (
                        session.query(Event).filter(Event.id == wt["event"][0]).first()
                    )
                    assert current_waiting_event.subloc_no == i, (
                        f"Subloc number of the event {current_waiting_event.id} is not equal to the index of the "
                        f"event in the virtual waiting area."
                    )
                    virtual_waiting_area[i] = None
                    break

    session.flush()
