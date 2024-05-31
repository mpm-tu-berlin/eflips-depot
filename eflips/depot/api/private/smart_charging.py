from datetime import timedelta, datetime
from typing import List, Dict

import numpy as np
import scipy
from eflips.model import Event, EventType


def optimize_charging_events_even(charging_events: List[Event]) -> None:
    """
    This function optimizes the power draw of a list of charging events.

    The power draw is optimized such that the total
    power draw is minimized, while the energy transferred remains constant.
    :param charging_events: The list of charging events to optimize
    :return: Nothing, the charging events are updated in place
    """

    TEMPORAL_RESOLUTION = timedelta(seconds=60)

    assert all(
        [event.event_type == EventType.CHARGING_DEPOT for event in charging_events]
    )
    start_time = min([event.time_start for event in charging_events])
    end_time = max([event.time_end for event in charging_events])

    # Formulate the optimzation problem.
    # - Each charging event has a peak power, which cannot be exceeded.
    # - Each charging event has a start and end time.
    # - betweeen the start time and end time, the energy transferred must remain constant.
    # - the total power draw is the sum of all events' power at this point in time
    # - the total power draw must be minimized

    total_duration = int((end_time - start_time) / TEMPORAL_RESOLUTION)
    total_time = np.arange(
        start_time.timestamp(),
        end_time.timestamp(),
        TEMPORAL_RESOLUTION.total_seconds(),
    )  # The time axis, used to resample the power draw

    # For each event, create an array of power draws and a boolean array of charging allowed
    # Also note down the peak power and transferred energy
    params_for_events: List[Dict[str, float | np.ndarray]] = []
    for event in charging_events:
        power_draw = np.zeros(total_duration, dtype=float)
        charging_allowed = np.zeros(total_duration, dtype=int)

        # Calculate the power draw vector, from the start SoC, end SoC and timeseries, if available
        event_soc = [event.soc_start]
        event_time = [event.time_start.timestamp()]
        if event.timeseries is not None:
            event_soc.extend(event.timeseries["soc"])
            event_time.extend(
                [
                    datetime.fromisoformat(t).timestamp()
                    for t in event.timeseries["time"]
                ]
            )
        event_soc.append(event.soc_end)
        event_time.append(event.time_end.timestamp())

        # Resample the timeseries to the temporal resolution
        expanded_soc = np.interp(total_time, event_time, event_soc)
        expanded_power = (
            np.diff(expanded_soc, prepend=expanded_soc[0])
            * event.vehicle.vehicle_type.battery_capacity
        )  # Change in kWh each minute
        expanded_power = (
            expanded_power / TEMPORAL_RESOLUTION.total_seconds() * 3600
        )  # to kW

        # Between the start and end time, charging is allowed
        start_index = int((event.time_start - start_time) / TEMPORAL_RESOLUTION)
        end_index = int((event.time_end - start_time) / TEMPORAL_RESOLUTION)
        charging_allowed[start_index:end_index] = 1
        max_power = max(expanded_power)
        transferred_energy = (
            event_soc[-1] - event_soc[0]
        ) * event.vehicle.vehicle_type.battery_capacity  # kWh

        params_for_events.append(
            {
                "power_draw": expanded_power,
                "charging_allowed": charging_allowed,
                "max_power": max_power,
                "transferred_energy": transferred_energy,
            }
        )

    # The total number of vehicles at the depot is the sum of the charging allowed for each event, since it is 1 if
    # the bus is at the depot and 0 otherwise
    total_occupancy = np.sum(
        [event["charging_allowed"] for event in params_for_events], axis=0
    )

    # For each event, calculate an optimized power draw
    for params_for_event in params_for_events:
        # Calculate the mean power that would be drawn if the vehicle was charged evenly
        charging_duration = (
            np.sum(params_for_event["charging_allowed"])
            * TEMPORAL_RESOLUTION.total_seconds()
        )
        mean_power = (
            params_for_event["transferred_energy"] / charging_duration
        ) * 3600  # kW

        # Calculate the mean amount of vehicles present at the depot when this vehicle is charging
        # We only consider the time when the vehicle is charging
        total_occupancy_while_charging = total_occupancy[
            params_for_event["charging_allowed"] == 1
        ]
        mean_occupancy = np.mean(total_occupancy_while_charging)

        # for each timestep optimize the power draw. We want to charge less when there are more vehicles at the depot
        # The power draw is varies with the amount of vehicles present at the depot relative to the mean amount
        charging_factor = (mean_occupancy / (total_occupancy)) * params_for_event[
            "charging_allowed"
        ]

        # Make sure the charging factor is not infinite or NaN
        charging_factor[np.isnan(charging_factor)] = 1
        charging_factor[np.isinf(charging_factor)] = 1
        optimized_power = (
            params_for_event["charging_allowed"] * mean_power * charging_factor
        )

        # Cap it at the peak power
        optimized_power_capped = np.minimum(
            optimized_power, params_for_event["max_power"]
        )

        # Count the energy that is we need to distribute over the time when the vehicle is not at peak power
        energy_to_distribute = (
            np.trapz((optimized_power - optimized_power_capped), total_time) / 3600
        )  # kWh
        if energy_to_distribute > 0:
            optimized_power_not_capped = np.where(
                optimized_power > optimized_power_capped
            )
            # Distribute the energy over the time when the vehicle is not at peak power
            uncapped_duration = (
                optimized_power_not_capped[0].shape[0]
                * TEMPORAL_RESOLUTION.total_seconds()
            )
            power_to_add = energy_to_distribute / (uncapped_duration / 3600)
            optimized_power[optimized_power_not_capped[0]] += power_to_add

        # Make sure the transferred energy is the same
        post_opt_energy = (
            scipy.integrate.trapz(optimized_power, total_time) / 3600
        )  # kWh
        assert post_opt_energy >= params_for_event["transferred_energy"]
        # Make it fit exactly
        optimized_power = optimized_power * (
            params_for_event["transferred_energy"] / post_opt_energy
        )

        optimized_power2 = (
            params_for_event["charging_allowed"] * mean_power
        )  # * (mean_occupancy / total_occupancy)
        # Fill NaNs with the zero power draw
        optimized_power[np.isnan(optimized_power)] = 0
        optimized_power2[np.isnan(optimized_power2)] = 0

        params_for_event["optimized_power"] = optimized_power
        params_for_event["optimized_power2"] = optimized_power2

    # Now we have the power draw and charging allowed for each event
    if False:
        from matplotlib import pyplot as plt

        plt.subplot(3, 1, 1)
        total_power = np.sum(
            [event["power_draw"] for event in params_for_events], axis=0
        )
        plt.plot(total_time, total_power, label="Original power draw")
        optimized_power = np.sum(
            [event["optimized_power"] for event in params_for_events], axis=0
        )
        plt.plot(total_time, optimized_power, label="Optimized power draw")
        optimized_power2 = np.sum(
            [event["optimized_power2"] for event in params_for_events], axis=0
        )
        plt.plot(total_time, optimized_power2, label="Mean power draw")
        plt.xlabel("Time")
        plt.ylabel("Total power draw (kW)")

        plt.axhline(
            y=max(total_power), color="blue", linestyle="--", label="Max power draw"
        )
        plt.axhline(
            y=max(optimized_power),
            color="orange",
            linestyle="--",
            label="Max optimized power draw",
        )
        plt.axhline(
            y=max(optimized_power2),
            color="green",
            linestyle="--",
            label="Max mean power draw",
        )

        plt.subplot(3, 1, 2)
        # Energy transferred
        total_energy = scipy.integrate.cumtrapz(total_power, total_time, initial=0)
        plt.plot(total_time, total_energy, label="Original energy transferred")
        optimized_energy = scipy.integrate.cumtrapz(
            optimized_power, total_time, initial=0
        )
        plt.plot(total_time, optimized_energy, label="Optimized energy transferred")
        optimized_energy2 = scipy.integrate.cumtrapz(
            optimized_power2, total_time, initial=0
        )
        plt.plot(total_time, optimized_energy2, label="Mean energy transferred")
        plt.xlabel("Time")
        plt.ylabel("Total energy transferred (kWh)")

        plt.subplot(3, 1, 3)
        plt.plot(total_time, total_occupancy)
        plt.xlabel("Time")
        plt.ylabel("Vehicle count")
        plt.show()

    # Finally, update the events in the database
    for i in range(len(charging_events)):
        event = charging_events[i]
        start_index = int((event.time_start - start_time) / TEMPORAL_RESOLUTION)
        end_index = int((event.time_end - start_time) / TEMPORAL_RESOLUTION)
        powers = params_for_events[i]["optimized_power"][start_index:end_index]
        energies = scipy.integrate.cumtrapz(powers, initial=0) / (
            3600 / TEMPORAL_RESOLUTION.total_seconds()
        )  # kWh
        socs = event.soc_start + energies / event.vehicle.vehicle_type.battery_capacity

        # Make sure the last SoC is the same as the end SoC
        assert np.isclose(socs[-1], event.soc_end, atol=0.01)
        # Make sure the first SoC is the same as the start SoC
        assert np.isclose(socs[0], event.soc_start, atol=0.01)

        # Make the socs match exactly, setting all those smaller than the start SoC to the start SoC and
        # all those larger than the end SoC to the end SoC
        socs[socs < event.soc_start] = event.soc_start
        socs[socs > event.soc_end] = event.soc_end

        # Add a timeseries to the event
        event.timeseries = {
            "time": [
                datetime.fromtimestamp(t).astimezone().isoformat()
                for t in total_time[start_index:end_index]
            ],
            "soc": socs.tolist(),
        }
        if event.timeseries["time"][0] < event.time_start.isoformat():
            event.timeseries["time"][0] = event.time_start.isoformat()
        if event.timeseries["time"][-1] > event.time_end.isoformat():
            event.timeseries["time"][-1] = event.time_end.isoformat()
