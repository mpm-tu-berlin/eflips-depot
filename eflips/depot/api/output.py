from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Hashable
from math import floor

from eflips.depot import DepotEvaluation
from eflips.depot.api.enums import EventType
from depot.api.input import VehicleType, Area


@dataclass
class DepotEvent:
    id: Hashable
    """A unique identifier for the event. It should be a positive integer."""

    event_type: EventType
    """The type of the event. It should be an instance of EventType."""

    vehicle_type_id: Hashable
    """The index of the type of the vehicle that is involved in the event. It should be an positive integer."""

    vehicle: "Vehicle"
    """The vehicle that is involved in the event. It should be an instance of 
    :class:`eflips.depot.api.output.Vehicle`."""

    area_id: Hashable
    """The index of the area of the depot where the event takes place. It should be an positive integer."""

    subloc_no: int | None
    """This represents the exact parking slot in an Area."""

    time_start: datetime
    """The time when the event starts. It should be a datetime object with timezone."""

    time_end: datetime
    """The time when the event ends. It should be a datetime object with timezone."""

    soc_start: float
    """The state of charge of the vehicle at the start of the event. Relative to net capacity. it should be a float 
    between 0 and 1."""

    soc_end: float
    """The state of charge of the vehicle at the end of the event. Relative to net capacity. it should be a float 
    between 0 and 1."""

    timeseries: Optional[Dict] = None
    """A dictionary representing the energy status over time in this event. Mandatory keys are "time" with resolution 
    of 1 second and "soc". """

    @classmethod
    def _event_type(self, process) -> "EventType":
        """This function returns a DepotEvent object from a process object in eFLIPS-Depot."""
        # Determine event type

        match type(process).__name__:
            case "Serve":
                event_type = EventType.SERVICE
            case "Charge":
                event_type = EventType.CHARGING

            case "Standby":
                # We don't distinguish standby and standby_departure in the output
                event_type = EventType.STANDBY

            case "Precondition":
                event_type = EventType.PRECONDITION

            case _:
                raise ValueError(
                    """Invalid process type %s. Valid process types are "Serve", "Charge", "Standby"""
                )

        return event_type


@dataclass
class Vehicle:
    id: int
    """The unique identifier of a vehicle. It should be a positive integer."""

    name: str
    """The name of the vehicle."""

    vehicle_type_id: Hashable
    """The type of the vehicle. It should be an instance of :class:`eflips.depot.api.input.VehicleType`"""

    depot_events: list[DepotEvent]
    """This list represents a sequence of events that the vehicle has experienced in this depot."""


def simseconds_to_timedelta(simseconds: int) -> timedelta:
    """This function converts a number representing seconds after simulation start to a timedelta object.
    :param simseconds: A number representing seconds after simulation start.
    :return: A timedelta object representing the time after simulation start."""
    days = floor(simseconds / 86400)
    hours = floor((simseconds % 86400) / 3600)
    minutes = floor((simseconds % 3600) / 60)
    seconds = simseconds % 60
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def return_vehicle_list(
    depot_evaluation: DepotEvaluation, simulation_start_time
) -> List[Vehicle]:
    """This function returns a list of Vehicle objects."""

    list_of_vehicles = []

    for current_vehicle in depot_evaluation.vehicle_generator.items:
        list_of_events = []
        list_of_vehicles.append(
            Vehicle(
                id=current_vehicle.ID,
                name=current_vehicle.ID,  # TODO check if there is a name for vehicle in eflips
                vehicle_type_id=current_vehicle.vehicle_type.ID,
                depot_events=None,
            )
        )

        # Add events of this vehicle to the list

        for time_key, process_list in current_vehicle.logger.loggedData[
            "dwd.active_processes_copy"
        ].items():
            if process_list is not None:
                # Get the time to datetime object

                # event_value could be a list of eflips processes
                for process in process_list:
                    # Only valid for events with duration > 0
                    if process.dur > 0:
                        # TODO why process.starts and ends are lists and find a proper way to deal restarted processes
                        event_start_after_simulation = simseconds_to_timedelta(
                            process.starts[0]
                        )
                        start_time = (
                            simulation_start_time + event_start_after_simulation
                        )
                        event_end_after_simulation = simseconds_to_timedelta(
                            process.ends[0]
                        )
                        end_time = simulation_start_time + event_end_after_simulation
                        # Get EventType via classmethod for now
                        # TODO 1 think about the proper way of event type, implicit or explicit 2
                        #  distinguish between standby and standby_departure
                        event_type = DepotEvent._event_type(process)
                        # Get proper area id
                        # TODO the case where a process (precondition mostly) got cancelled and there is no area id
                        area = current_vehicle.logger.loggedData["dwd.current_area"][
                            time_key
                        ]
                        area_id = area.ID if area is not None else None

                        # TODO sublocation and corresponding changes in input API of area

                        battery_logs = current_vehicle.battery_logs
                        # TODO: do soc in eflips and soc in database have different references?
                        timeseries = {}
                        for log in battery_logs:
                            if log.t == time_key:
                                # Get soc_start and soc_end from battery log
                                soc_start = log.energy / log.energy_real
                                # Get a simple version of timeseries. Expecting a bigger update after the implementation
                                # of charging curves
                                # TODO get this correct
                                # TODO for now just do some linear interpolation?
                                timeseries.update({"time": log.t, "soc": soc_start})
                            if log.t == time_key + process.dur:
                                soc_end = log.energy / log.energy_real
                                timeseries.update({"time": log.t, "soc": soc_end})

                        current_event = DepotEvent(
                            process.ID,
                            event_type,
                            current_vehicle.vehicle_type.ID,
                            current_vehicle.ID,
                            area_id,
                            None,
                            start_time,
                            end_time,
                            soc_start,
                            soc_end,
                            timeseries,
                        )

                        list_of_events.append(current_event)

                list_of_vehicles[-1].depot_events = list_of_events

    return list_of_vehicles


def return_event_list(
    depot_evaluation: DepotEvaluation, simulation_start_time
) -> List[DepotEvent]:
    """This function returns a list of DepotEvent objects. Each DepotEvent object represents an event that has occurred
    in the depot. The list is sorted by the time of the event. The list contains all events that have occurred in the
    depot."""
    pass
