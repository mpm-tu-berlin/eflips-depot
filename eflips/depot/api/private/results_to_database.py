import datetime
import itertools
import logging
from datetime import timedelta
from typing import List, Dict

import numpy as np
from eflips.model import Event, EventType, Rotation, Vehicle, Area, AreaType
from sqlalchemy import select

from eflips.depot import SimpleVehicle, ProcessStatus
from eflips.depot import UnstableSimulationException, DelayedTripException


def get_finished_schedules_per_vehicle(
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
        try:
            assert list_of_finished_trips[i].atd == list_of_finished_trips[i].std
        except AssertionError:
            raise DelayedTripException(
                f"The trip {list_of_finished_trips[i].ID} is delayed. The simulation doesn't "
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
                raise UnstableSimulationException(
                    f"New Vehicle required for the rotation/block {current_trip.ID}, which suggests the fleet or the "
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


def generate_vehicle_events(
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
            if len(process_log) == 0:
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
                                start_this_event = None
                                if time_stamp in dict_of_events.keys():
                                    assert "end" in dict_of_events[time_stamp].keys(), (
                                        f"The former event of {process} "
                                        f"should have an end time."
                                    )
                                    start_this_event = dict_of_events[time_stamp]["end"]
                                else:
                                    for other_process in process_log:
                                        if (
                                            other_process.ID != process.ID
                                            and other_process.dur > 0
                                        ):
                                            start_this_event = other_process.ends[0]
                                            break

                                assert (
                                    start_this_event is not None
                                ), f"Current process {process} should have a start time by now"

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


def complete_standby_departure_events(
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
                end_time = max(latest_time, max(time_keys))
                # Lu: Apparently sometimes there are events going beyond the simulation end time?
            else:
                end_time = time_keys[i + 1]

            process_dict["end"] = end_time


def add_soc_to_events(dict_of_events, battery_log) -> None:
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

    battery_log_times = [log[0] for log in battery_log_list]
    battery_log_socs = [log[1] for log in battery_log_list]

    for i in range(len(time_keys)):
        # Get soc

        start_time = time_keys[i]
        process_dict = dict_of_events[time_keys[i]]

        if process_dict["type"] != "Trip":
            soc_start = np.interp(start_time, battery_log_times, battery_log_socs)
            process_dict["soc_start"] = min(float(soc_start), 1.0)
            soc_end = np.interp(
                process_dict["end"], battery_log_times, battery_log_socs
            )
            process_dict["soc_end"] = min(float(soc_end), 1.0)
        else:
            continue


def add_events_into_database(
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

        # Get station_id of the current depot through area

        # TODO needs better implementation
        if type(process_dict["area"]) == str and "_" in process_dict["area"]:
            area_name = process_dict["area"].split("_")
            area_id = int(area_name[0])
            row = int(area_name[-1])

        else:
            area_id = int(process_dict["area"])

        current_area = session.query(Area).filter(Area.id == area_id).one()
        station_id = current_area.depot.station_id

        if current_area.area_type == AreaType.LINE:
            capacity_per_line = int(current_area.capacity / current_area.row_count)
            process_dict["slot"] = capacity_per_line * row + process_dict["slot"] - 1

        current_event = Event(
            scenario=scenario,
            vehicle_type_id=db_vehicle.vehicle_type_id,
            vehicle=db_vehicle,
            station_id=station_id,
            area_id=area_id,
            subloc_no=process_dict["slot"] if "slot" in process_dict.keys() else 00,
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


def update_vehicle_in_rotation(session, scenario, list_of_assigned_schedules) -> None:
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
        Event.trip_id.isnot(None)
        | (Event.station_id.isnot(None) & Event.area_id.is_(None)),
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


def update_waiting_events(session, scenario, waiting_area_id) -> None:
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
