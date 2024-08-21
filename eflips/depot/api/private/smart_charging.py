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
    params_for_events: List[Dict[str, float | np.ndarray | Event]] = []
    for event in charging_events:
        power_draw = np.zeros_like(total_time, dtype=float)
        charging_allowed = np.zeros_like(total_time, dtype=int)

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
                "event": event,
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
        charging_factor = (total_occupancy / mean_occupancy) * params_for_event[
            "charging_allowed"
        ]

        # Make sure the charging factor is not infinite or NaN
        charging_factor[np.isnan(charging_factor)] = 1
        charging_factor[np.isinf(charging_factor)] = 1
        power_scaling_vector = (
            (mean_power * charging_factor) - mean_power
        ) * -1  # How much to shift the power draw
        if min(power_scaling_vector) < -mean_power:
            power_scaling_vector /= min(power_scaling_vector) / -mean_power
        optimized_power = params_for_event["charging_allowed"] * (
            power_scaling_vector + mean_power
        )

        # Cap it at the peak power
        optimized_power_capped = np.minimum(
            optimized_power, params_for_event["max_power"]
        )

        if not np.all(optimized_power_capped == optimized_power):
            # If the power draw is capped, we will just use the mean power draw
            optimized_power = params_for_event["charging_allowed"] * mean_power

        # Make sure the transferred energy is the same
        post_opt_energy = (
            scipy.integrate.trapezoid(optimized_power, total_time) / 3600
        )  # kWh

        if False:
            # Some plots for only this charging event
            from matplotlib import pyplot as plt

            valid_charging_indices = np.where(
                params_for_event["charging_allowed"] == 1
            )[0]

            fig, axs = plt.subplots(3, 1, sharex=True)
            axs[0].axhline(mean_power, color="red", linestyle="--", label="Mean power")
            axs[0].plot(
                total_time[valid_charging_indices],
                params_for_event["power_draw"][valid_charging_indices],
                label="Original power draw",
            )
            axs[0].plot(
                total_time[valid_charging_indices],
                optimized_power[valid_charging_indices],
                label="Optimized power draw",
            )
            axs[0].plot(
                total_time[valid_charging_indices],
                optimized_power_capped[valid_charging_indices],
                label="Optimized power draw capped",
            )
            axs[0].set_xlabel("Time")
            axs[0].legend()

            axs[1].plot(
                total_time[valid_charging_indices],
                np.cumsum(params_for_event["power_draw"][valid_charging_indices])
                / 3600,
                label="Original energy transferred",
            )
            axs[1].plot(
                total_time[valid_charging_indices],
                np.cumsum(optimized_power[valid_charging_indices]) / 3600,
                label="Optimized energy transferred",
            )
            axs[1].set_xlabel("Time")

            axs[2].axhline(
                mean_occupancy, color="red", linestyle="--", label="Mean occupancy"
            )
            axs[2].plot(
                total_time[valid_charging_indices],
                total_occupancy[valid_charging_indices],
            )

            mean_arr = np.ones_like(total_occupancy) * mean_occupancy
            mean_cumsum = np.cumsum(mean_arr[valid_charging_indices])
            total_cumsum = np.cumsum(total_occupancy[valid_charging_indices])
            assert np.isclose(mean_cumsum[-1], total_cumsum[-1], atol=0.01)

            axs[2].set_xlabel("Time")
            plt.show()

        if not np.isclose(
            post_opt_energy, params_for_event["transferred_energy"], rtol=0.001
        ):
            # Scale the power draw to match the transferred energy
            optimized_power = optimized_power * (
                params_for_event["transferred_energy"] / post_opt_energy
            )

        # Fill NaNs with the zero power draw
        optimized_power[np.isnan(optimized_power)] = 0

        params_for_event["optimized_power"] = optimized_power

        event = params_for_event["event"]
        start_index = int((event.time_start - start_time) / TEMPORAL_RESOLUTION)
        end_index = (
            int((event.time_end - start_time) / TEMPORAL_RESOLUTION) + 1
        )  # +1 to include the last index
        powers = params_for_event["optimized_power"][start_index:end_index]

        energies = scipy.integrate.cumulative_trapezoid(powers, initial=0) / (
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

        # Add a timeseries to the event, removing the first and last index, since they will be the same as the start and
        # end SoC
        event.timeseries = {
            "time": [
                datetime.fromtimestamp(t).astimezone().isoformat()
                for t in total_time[start_index:end_index]
            ][
                1:-1
            ],  # Remove the first and last index
            "soc": socs.tolist()[1:-1],
        }
        if len(event.timeseries["time"]) > 0:
            if event.timeseries["time"][0] < event.time_start.isoformat():
                event.timeseries["time"][0] = event.time_start.isoformat()
            if event.timeseries["time"][-1] > event.time_end.isoformat():
                event.timeseries["time"][-1] = event.time_end.isoformat()
        else:
            event.timeseries = None

    # Now we have the power draw and charging allowed for each event
    if False:
        from matplotlib import pyplot as plt

        fig, axs = plt.subplots(3, 1, sharex=True)
        total_power = np.sum(
            [event["power_draw"] for event in params_for_events], axis=0
        )
        axs[0].plot(total_time, total_power, label="Original power draw")
        optimized_power = np.sum(
            [event["optimized_power"] for event in params_for_events], axis=0
        )
        axs[0].plot(total_time, optimized_power, label="Optimized power draw")
        optimized_power2 = np.sum(
            [event["optimized_power2"] for event in params_for_events], axis=0
        )
        axs[0].plot(total_time, optimized_power2, label="Mean power draw")
        axs[0].set_xlabel("Time")
        axs[0].set_ylabel("Total power draw (kW)")

        axs[0].axhline(
            y=max(total_power), color="blue", linestyle="--", label="Max power draw"
        )
        axs[0].axhline(
            y=max(optimized_power),
            color="orange",
            linestyle="--",
            label="Max optimized power draw",
        )
        axs[0].axhline(
            y=max(optimized_power2),
            color="green",
            linestyle="--",
            label="Max mean power draw",
        )

        # Energy transferred
        total_energy = scipy.integrate.cumulative_trapezoid(
            total_power, total_time, initial=0
        )
        axs[1].plot(total_time, total_energy, label="Original energy transferred")
        optimized_energy = scipy.integrate.cumulative_trapezoid(
            optimized_power, total_time, initial=0
        )
        axs[1].plot(total_time, optimized_energy, label="Optimized energy transferred")
        optimized_energy2 = scipy.integrate.cumulative_trapezoid(
            optimized_power2, total_time, initial=0
        )
        axs[1].plot(total_time, optimized_energy2, label="Mean energy transferred")
        axs[1].set_xlabel("Time")
        axs[1].set_ylabel("Total energy transferred (kWh)")

        axs[2].plot(total_time, total_occupancy)
        axs[2].set_xlabel("Time")
        axs[2].set_ylabel("Vehicle count")
        plt.show()
