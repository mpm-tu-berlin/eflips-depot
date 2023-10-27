"""

This module contains the data structures needed by django-simba to process the output of eFLIPS-Depot.

"""
from dataclasses import dataclass
from typing import List

from depot import DepotEvaluation


@dataclass
class InputForSimba:
    """Input Data for Simba

    :param rotation_id: ID of rotation (=`:class:`eflips.depot.api.input.VehicleSchedule` in out wording)
    :param vehicle_id: ID of vehicle
    :param soc_departure: soc at departure of each vehicle (in a range of 0 to 1)
    """

    rotation_id: int
    """The Rotation ID this rotation had in the input data."""

    vehicle_type_id: str
    """The Vehicle Type ID for the vehicle assigned to this rotation."""

    vehicle_id: str
    """The Vehicle ID assigned to this rotation. Using this value, you can see how the same vehicle was used in different rotations."""

    soc_departure: float
    """The SOC at departure of each vehicle (in a range of 0 to 1)."""


def to_simba(ev: DepotEvaluation) -> List[InputForSimba]:
    """

    Turns a `:class:`eflips.depot.evaluation.DepotEvaluation` object into a list of `:class:`InputForSimba` objects.

    :param ev: Object storing all simulation results. Generated by :func:`eflips.api.run_simulation()`.
    :return: list of :class:`InputForSimba` objects.

    """

    inputs_for_simba = []

    for trip_i in ev.timetable.trips_issued:
        if "_r1" in trip_i.ID:  # _r1: repetition 1 of all rotations

            # Get actual departure time of that trip
            actual_time_departure = trip_i.atd

            # Get start_soc from battery logs
            vehicle = trip_i.vehicle
            for log in vehicle.battery_logs:
                if log.t == actual_time_departure:
                    assert log.event_name == "consume_start", (
                        f"Expected consume_start event at {actual_time_departure} "
                        f"for vehicle {vehicle.ID}, but got {log.event_name} instead."
                    )
                    start_soc = log.energy/log.energy_real

            data_unit = InputForSimba(
                int(
                    float(trip_i.ID_orig)
                ),  # Slightly ugly, but we need to return an int
                trip_i.vehicle.vehicle_type.ID,
                trip_i.vehicle.ID,
                start_soc,
            )

            inputs_for_simba.append(data_unit)

    return inputs_for_simba
