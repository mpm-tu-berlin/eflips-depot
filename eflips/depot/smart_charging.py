"""Smart charging algorithm."""

import eflips
import eflips.evaluation
from eflips.settings import globalConstants
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
import math
import datetime


class SmartCharging:
    """
    Class which implements the smart charging algorithm for depot charged vehicles of Lauth, Mundt,
    Göhlich.
    In the eflips_setiings [depot][log_cm_data] has to be True
    """

    def __init__(self, data, start_date, price_data_path, power_limit_grid):
        """

        :param data: data for schedule (DepotEvaluation or pd.Frame
        :param start_date:
        :param price_data_path:
        :param power_limit_grid: int or dict of powerlimits (15 min intervalls) (if list is not long enough, the list starts over again, the last value has to be given for Ex. for a day key 86400 has to exist),
                only the changeges has to be in the dict, index of dict has to be the time in seconds.
         [or an function] not yet implemnted
        """

        if isinstance(
            data, eflips.depot.evaluation.DepotEvaluation
        ):  # for data straight out of eflips
            self.schedule_method(data)
        if isinstance(
            data, pd.core.frame.DataFrame
        ):  # for schedule from externeal programms. Watch that the format is correct.
            self.schedule = data

        self.charging_log = self.construct_empty_dict_charging_log()
        # Outdated, not maintained self.charging_log_even = self.construct_empty_dict_charging_log()
        self.charging_log_imm = self.construct_empty_dict_charging_log()

        self.max_sim_time = self.max_simulation_time()

        try:
            self.start_date = datetime.date(start_date[0], start_date[1], start_date[2])
        except:
            print(
                "The start date has the wrong format. I use the 2019-06-03 as the start date."
            )
            self.start_date = datetime.date(2019, 6, 3)

        power_item = eflips.depot.PowerFrame(
            self.max_sim_time, self.start_date, price_data_path
        )
        self.power = power_item.pdframe()
        self.add_power_limit_grid_to_power(power_limit_grid)

        self.power["used_power_immediately"] = float(0)
        self.power["used_power_smart"] = float(0)
        # Outdated, not maintained self.power["used_power_smart_even"] = float(0)
        if hasattr(self, "precondition_schedule"):
            self.precondition()

        if not (self.immediately_charging()):
            print("Immediately charging, went wrong.")

    def add_power_limit_grid_to_power(self, data):
        if isinstance(data, int):  # backward compatible
            self.power["limit_grid"] = data

        if isinstance(data, dict):
            power_limit_grid = []
            time = 0
            for key, power_limit in data.items():
                while time < int(key):
                    power_limit_grid.append(pre_power_limit)
                    time += 900
                pre_power_limit = power_limit

            while len(self.power.index) > len(power_limit_grid):
                power_limit_grid += power_limit_grid

            # for performance
            self.power["limit_grid"] = power_limit_grid[0 : len(self.power.index)]

    def schedule_method(self, ev):
        """
        Iterates over the process list. And generates the Precon and normal schedule
        :param ev: has to be type DepotEvaluation
        :return: prober schedule and a schdule with all incomplet depot stays
        """

        columns = [
            "charging_power",
            "energy_demand_real",
            "energy_demand",
            "time_arrival",
            "time_depart",
            "time_charging_flex",
            "charging_efficiency",
        ]  # units[kW, kWs,kWs, s, s, s,1] energy_demand_calc is the box integral, which is used for the calculation
        # time depart is always the starting time of the next process
        self.schedule = pd.DataFrame(columns=columns)
        self.schedule_debug = pd.DataFrame(columns=columns)

        precondition_columns = [
            "charging_power",
            "time_start",
            "time_ends",
            "charging_efficiency",
        ]  # units[kW,  s, s, 1]
        self.precondition_schedule = pd.DataFrame(columns=precondition_columns)
        self.vehicles_id = []

        for vehicle in ev.vehicle_generator.items:
            self.vehicles_id.append(vehicle.ID)
            i_charg = 0  # number of charging processes
            i_precon = 0

            pre_process_time = 0
            pre_process_item_charge = None
            pre_precon_process = None

            for process_time, process_list_item in vehicle.logger.loggedData[
                "dwd.active_processes_copy"
            ].items():
                if isinstance(
                    pre_process_item_charge, eflips.depot.processes.ChargeAbstract
                ):
                    row = {}
                    row["time_arrival"] = pre_process_item_charge.starts[0]
                    row[
                        "time_depart"
                    ] = process_time  # Depart time of the process before is the estart time of the next process

                    charging_duration = self.calc_charging_time(
                        pre_process_item_charge.starts, pre_process_item_charge.ends
                    )
                    row["time_charging_flex"] = (
                        row["time_depart"] - row["time_arrival"] - charging_duration
                    )

                    row["energy_demand_real"] = (
                        pre_process_item_charge.energy * 3600
                    )  # kWh ->kWs
                    row["charging_efficiency"] = pre_process_item_charge.efficiency

                    row["charging_power"] = vehicle.power_logs[row["time_arrival"]]
                    row["energy_demand"] = charging_duration * row["charging_power"]

                    if (
                        row["energy_demand_real"] > row["energy_demand"]
                    ):  # Makes sure the that the numerical error gets deleted
                        row["energy_demand_real"] = row["energy_demand"]

                    all_keys_exist = True
                    for key_to_check in columns:  # checks if dict is complet
                        if key_to_check in row:
                            all_keys_exist = True
                        else:
                            all_keys_exist = False
                            break

                    if all_keys_exist:  # safes the row in the dataframe
                        row = pd.Series(row)
                        self.schedule.loc[vehicle.ID + "_" + str(i_charg)] = row
                        if row["energy_demand"] / row["energy_demand_real"] > 2:
                            print(
                                "Bus "
                                + vehicle.ID
                                + " has deviation of over 50 percent between energy_demand_real and energy_demand. You might wanna check this."
                            )
                    else:
                        row = pd.Series(row)
                        self.schedule_debug.loc[vehicle.ID + "_" + str(i_charg)] = row

                    i_charg += 1
                    pre_process_item_charge = None  # Reset the item

                for process_item in process_list_item:
                    if isinstance(process_item, eflips.depot.processes.ChargeAbstract):
                        # Filter and Store the correct processes
                        pre_process_item_charge = process_item

                    if (
                        isinstance(process_item, eflips.depot.processes.Precondition)
                        and process_item is not pre_precon_process
                    ):
                        # Filter and Store the correct processes
                        row = {
                            "charging_power": process_item.power,
                            "time_start": process_item.starts[0],
                        }

                        if process_item.ends:  # Checks if Precon process is complete
                            row["time_ends"] = process_item.ends[0]
                            row["charging_efficiency"] = process_item.efficiency

                            row = pd.Series(row)
                            self.precondition_schedule.loc[
                                vehicle.ID + "_" + str(i_precon)
                            ] = row

                            i_precon += 1

                            pre_precon_process = process_item  # Because if a precon process is canceled, it is logged twice. It has to be checked if the precon process was already used.

        print("Schedule building done.")

    def calc_charging_time(self, start, end):
        """

        :param start: a list of all start times
        :param end: a list of all end times
        :return: total duration of charging
        """
        duration = 0
        for i in range(len(start)):
            if i <= len(end):
                duration += end[i] - start[i]

        return duration

    def set_power(self, power):
        """
        :param power: a pd_frame
        """
        self.power = power

    def max_simulation_time(self):
        """

        :return: The maximum time.
        """
        return self.schedule.max().time_depart

    def construct_pd_frame_charging_history_energy(self):
        """

        :return: empty pd Frame with time as index and bus stops in depot as columns
        """
        columns = list(self.schedule.index)
        index = list(self.power.index)
        charging_history = pd.DataFrame(index=index, columns=columns)
        return charging_history

    def construct_empty_dict_charging_log(self):
        """

        :return: a dict, in which al the charging intervalles of the vehicles will be stored
        """
        columns = ["start", "end", "power", "price"]
        dict_of_pd = {}
        for ID in list(self.schedule.index):
            # words = ID.split("_")
            # ID_bus = words[0] + "_" + words[1]
            dict_of_pd[ID] = pd.DataFrame(columns=columns)

        return dict_of_pd

    def smart_charging_algorithm(self):
        """
        needs self.schedule, self.power_price (start_time, end_time, price), power_limi_grid. Implements the algorithm of Lauth, Mundt,
        Göhlich.
        :return: False if smart charging is not possible, a pd Frame with the time intervalls and the charging power, if smart charging was possible
        """

        schedule = self.schedule.sort_values(by=["time_charging_flex"])

        self.power = self.power.sort_values(by=["price"])
        for ID_bus, row_bus in schedule.iterrows():
            # power delta is needed for later adjustments, to match the real energy need
            energy_delta = row_bus.energy_demand - row_bus.energy_demand_real

            for ID_power, row_power in self.power.iterrows():
                if row_bus.energy_demand <= 0:
                    break
                power_rest = (
                    row_power.limit_grid - row_power.used_power_smart
                )  # determines the left power
                interval = self.intersection(
                    [row_power.start_time, row_power.end_time],
                    [row_bus.time_arrival, row_bus.time_depart],
                )
                if isinstance(interval, list) and power_rest > 0:
                    self.energy_calculation_smart(
                        interval[0],
                        interval[1],
                        row_bus,
                        row_power,
                        ID_power,
                        power_rest,
                        ID_bus,
                    )

            if row_bus.energy_demand >= 50:  # needed against numerical error
                print(row_bus.energy_demand, ID_bus, " Smart")
                return False

            # Make adjustments so the charging energy fit the real energy
            if energy_delta > 0:
                self.compensate_to_energy_real(
                    energy_delta, ID_bus, row_bus, self.charging_log, "used_power_smart"
                )

        return True

    def energy_calculation_smart(
        self, start_time, end_time, row_bus, row_power, ID_power, power_rest, ID_bus
    ):
        """
        For outsource some of the stuff of smart_charging_algorithm
        :param start_time:
        :param end_time:
        :param row_bus:
        :param ID_power:
        :param power_rest:
        :return:
        """
        factor_power_used = (end_time - start_time) / (
            row_power.end_time - row_power.start_time
        )  # a factor which scales the power, if a bus intervall is not complet in a power intervall

        if power_rest >= row_bus.charging_power / row_bus.charging_efficiency:
            if (
                row_bus.energy_demand
                >= (end_time - start_time) * row_bus.charging_power
            ):
                energy = (end_time - start_time) * row_bus.charging_power
                row_bus.energy_demand -= energy
                # self.energy_log.at[row_power.start_time,ID_bus] = energy

                power = (
                    row_bus.charging_power / row_bus.charging_efficiency
                ) * factor_power_used
                self.power.at[ID_power, "used_power_smart"] += power
                row = [
                    start_time,
                    end_time,
                    power * row_bus.charging_efficiency,
                    row_power.price,
                ]
                self.charging_log[ID_bus].loc[int(start_time)] = row
            else:
                power = (
                    row_bus.energy_demand
                    / ((end_time - start_time) * row_bus.charging_efficiency)
                    * factor_power_used
                )
                self.power.at[ID_power, "used_power_smart"] += power
                row = [
                    start_time,
                    end_time,
                    power * row_bus.charging_efficiency,
                    row_power.price,
                ]
                self.charging_log[ID_bus].loc[int(start_time)] = row

                # self.energy_log.at[row_power.start_time, ID_bus] = row_bus.energy_demand
                row_bus.energy_demand = 0
        else:
            if (
                row_bus.energy_demand
                >= (end_time - start_time) * power_rest * row_bus.charging_efficiency
            ):
                energy = (
                    (end_time - start_time) * power_rest * row_bus.charging_efficiency
                )
                row_bus.energy_demand -= energy
                # self.energy_log.at[row_power.start_time, ID_bus] = energy

                power = power_rest * factor_power_used
                self.power.at[ID_power, "used_power_smart"] += power
                row = [
                    start_time,
                    end_time,
                    power * row_bus.charging_efficiency,
                    row_power.price,
                ]
                self.charging_log[ID_bus].loc[int(start_time)] = row
            else:
                power = (
                    row_bus.energy_demand
                    / ((end_time - start_time) * row_bus.charging_efficiency)
                ) * factor_power_used
                self.power.at[ID_power, "used_power_smart"] += power
                row = [
                    start_time,
                    end_time,
                    power * row_bus.charging_efficiency,
                    row_power.price,
                ]
                self.charging_log[ID_bus].loc[int(start_time)] = row

                # self.energy_log.at[row_power.start_time, ID_bus] = row_bus.energy_demand
                row_bus.energy_demand = 0

        return row_bus.energy_demand

    def smart_charging_algorithm_even(self):  # Outdated, not maintained
        """Same power curve, just buses will always be charged."""

        for ID_bus, row_bus in self.schedule.iterrows():
            constant_power = row_bus.energy_demand / (
                row_bus.time_depart - row_bus.time_arrival
            )

            for ID_power, row_power in self.power.iterrows():
                interval = self.intersection(
                    [row_power.start_time, row_power.end_time],
                    [row_bus.time_arrival, row_bus.time_depart],
                )
                if isinstance(interval, list):
                    self.energy_calculation_smart_even(
                        interval[0],
                        interval[1],
                        row_bus,
                        row_power,
                        ID_power,
                        ID_bus,
                        constant_power,
                    )

        self.power = self.power.sort_values(by=["price"])

        for ID_power, row_power in self.power.iterrows():
            if row_power.used_power_smart_even > 0:
                scaling_quotients = (
                    row_power.limit_grid / row_power.used_power_smart_even
                )
                if scaling_quotients > 1:
                    self.smart_even_upscalinig(scaling_quotients, ID_power)
                elif scaling_quotients < 1:
                    self.smart_even_downscalinig(scaling_quotients, ID_power)

        if any(
            self.power.used_power_smart_even > self.power.limit_grid * 1.01
        ):  # *1.01 because of the inaccuracy of the numeric calculations, in the calculations before
            print("Smart even failed")
            return False
        else:
            return True

    def energy_calculation_smart_even(
        self, start_time, end_time, row_bus, row_power, ID_power, ID_bus, constant_power
    ):  # Outdated, not maintained
        factor_power_used = (end_time - start_time) / (
            row_power.end_time - row_power.start_time
        )  # a factor which scales the power, if a bus intervall is not complet in a power intervall

        power = (constant_power * factor_power_used) / row_bus.charging_efficiency
        self.power.at[ID_power, "used_power_smart_even"] += power
        row = [row_power.start_time, row_power.end_time, power, row_power.price]
        self.charging_log_even[ID_bus].loc[int(row_power.start_time)] = row

    def smart_even_upscalinig(
        self, scaling_quotients, ID_power
    ):  # Outdated, not maintained
        """Scales the power for a specific intervall by scaling quotinet up. Also starts the proces to substract the power from an other power intervall."""

        for ID_bus, value in self.charging_log_even.items():
            for start_time, row in value.iterrows():
                if ID_power == start_time:
                    # Some checks
                    if (
                        row.power * scaling_quotients
                        > self.schedule.at[ID_bus, "charging_power"]
                    ):
                        power_delta = (
                            self.schedule.at[ID_bus, "charging_power"] - row.power
                        )
                        self.power.at[ID_power, "used_power_smart_even"] += power_delta
                        self.substract_power_most_expensive(
                            power_delta, ID_bus, ID_power
                        )
                    else:
                        power_delta = (row.power * scaling_quotients) - row.power
                        self.power.at[ID_power, "used_power_smart_even"] += power_delta
                        self.substract_power_most_expensive(
                            power_delta, ID_bus, ID_power
                        )

    def substract_power_most_expensive(
        self, power, ID_bus, ID_power
    ):  # Outdated, not maintained
        """Substracts the power from an other power intervall."""
        self.charging_log_even[ID_bus] = self.charging_log_even[ID_bus].sort_values(
            by=["price"], ascending=False
        )
        for start_time, row in self.charging_log_even[ID_bus].iterrows():
            if start_time != ID_power:
                if row.power >= power:
                    self.charging_log_even[ID_bus].at[start_time, "power"] -= power
                    self.power.at[start_time, "used_power_smart_even"] -= power
                    break
                else:
                    power -= self.charging_log_even[ID_bus].at[start_time, "power"]
                    self.power.at[
                        start_time, "used_power_smart_even"
                    ] -= self.charging_log_even[ID_bus].at[start_time, "power"]
                    self.charging_log_even[ID_bus].at[start_time, "power"] = 0

    def smart_even_downscalinig(
        self, scaling_quotients, ID_power
    ):  # Outdated, not maintained
        """Scales the power for a specific intervall by scaling quotinet down. Also starts the proces to add the power from an other power intervall."""
        for ID_bus, value in self.charging_log_even.items():
            for start_time, row in value.iterrows():
                if ID_power == start_time:
                    power_delta = row.power - (row.power * scaling_quotients)
                    self.power.at[ID_power, "used_power_smart_even"] -= power_delta
                    self.add_power_less_expensive(power_delta, ID_bus, ID_power)

    def add_power_less_expensive(
        self, power, ID_bus, ID_power
    ):  # Outdated, not maintained
        # Adds the power to an other intervall.
        self.charging_log_even[ID_bus] = self.charging_log_even[ID_bus].sort_values(
            by=["price"]
        )
        for start_time, row in self.charging_log_even[ID_bus].iterrows():
            if start_time != ID_power:
                potential_power = min(
                    [
                        self.schedule.at[ID_bus, "charging_power"],
                        self.power.at[start_time, "limit_grid"]
                        - self.power.at[start_time, "used_power_smart_even"],
                    ]
                )  # max power of charging infrastructur of a singele bus, left power until max of the grid
                if potential_power >= power:
                    self.charging_log_even[ID_bus].at[start_time, "power"] += power
                    self.power.at[start_time, "used_power_smart_even"] += power
                    break
                else:
                    self.charging_log_even[ID_bus].at[
                        start_time, "power"
                    ] += potential_power
                    self.power.at[
                        start_time, "used_power_smart_even"
                    ] += potential_power
                    power -= potential_power

    def immediately_charging(self):
        """
        Charges the buses immediatley after the parked. No charging eqauation steps.
        :return: pd Frame
        """

        schedule = self.schedule.copy()

        self.power = self.power.sort_values(by=["start_time"])
        for ID_bus, row_bus in schedule.iterrows():
            # power delta is needed for later adjustments, to match the real energy need
            energy_delta = row_bus.energy_demand - row_bus.energy_demand_real

            for ID_power, row_power in self.power.iterrows():
                interval = self.intersection(
                    [row_power.start_time, row_power.end_time],
                    [row_bus.time_arrival, row_bus.time_depart],
                )
                if isinstance(interval, list):
                    self.energy_calculation_immediately(
                        interval[0], interval[1], row_bus, row_power, ID_power, ID_bus
                    )

            if row_bus.energy_demand >= 50:  # needed against numerical error
                print(row_bus.energy_demand, ID_bus, "Imm")
                return False

            # Make adjustments so the charging energy fit the real energy
            if energy_delta > 0:
                self.compensate_to_energy_real(
                    energy_delta,
                    ID_bus,
                    row_bus,
                    self.charging_log_imm,
                    "used_power_immediately",
                )

        print("Immediately charging done.")
        return True

    def energy_calculation_immediately(
        self, start_time, end_time, row_bus, row_power, ID_power, ID_bus
    ):
        """
        Helper
        :param start_time:
        :param end_time:
        :param row_bus:
        :param row_power:
        :param ID_power:
        :return:
        """

        factor_power_used = (end_time - start_time) / (
            row_power.end_time - row_power.start_time
        )  # a factor which scales the power, if a bus intervall is not complet in a power intervall

        if row_bus.energy_demand >= (end_time - start_time) * row_bus.charging_power:
            row_bus.energy_demand -= (end_time - start_time) * row_bus.charging_power
            self.power.at[ID_power, "used_power_immediately"] += (
                row_bus.charging_power / row_bus.charging_efficiency
            ) * factor_power_used
            row = [start_time, end_time, row_bus.charging_power, row_power.price]
            self.charging_log_imm[ID_bus].loc[int(start_time)] = row
        else:
            power_at_grid = (
                row_bus.energy_demand
                / ((end_time - start_time) * row_bus.charging_efficiency)
            ) * factor_power_used
            self.power.at[ID_power, "used_power_immediately"] += power_at_grid
            row_bus.energy_demand = 0
            row = [
                start_time,
                end_time,
                power_at_grid * row_bus.charging_efficiency,
                row_power.price,
            ]
            self.charging_log_imm[ID_bus].loc[int(start_time)] = row

        return row_bus.energy_demand

    def intersection(self, interval_a, interval_b):
        """

        :param interval_a: list of two numbers
        :param interval_b: list of two numbers
        :return: the intersection of the two intervalls or False if no intersection is there
        """
        lower = max(interval_a[0], interval_b[0])
        upper = min(interval_a[1], interval_b[1])
        if lower >= upper:
            return False
        else:
            return [lower, upper]

    def in_period(self, interval, time):
        """Checks if time is in interval."""
        if interval[0] < interval[1]:
            return interval[0] <= time <= interval[1]
        else:
            return interval[0] <= time <= 86400 or time <= interval[1]

        return False

    def compensate_to_energy_real(
        self, energy_delta, ID_bus, row_bus, charging_log, type_of_power
    ):
        """
        Makes the correct adjustments, so charg_eqaution_steps can be implemented.
        :param energy_delta: differnce between energy and energy_real
        :param ID_bus:
        :param row_bus:
        :param charging_log: the charging log the function should use
        type_of_power: "used_power_smart" or "used_power_immediately"
        :return:
        """

        charging_log[ID_bus] = charging_log[ID_bus].sort_values(
            by="start", ascending=False
        )
        for start_time, charg_log in charging_log[ID_bus].iterrows():
            energy_slot = (charg_log.end - charg_log.start) * charg_log.power

            if energy_delta >= energy_slot:
                energy_delta -= energy_slot

                # find the correct time slot in power and substract the power
                power_time = int(start_time / 900) * 900

                self.power.at[power_time, type_of_power] -= (
                    charg_log.power / row_bus.charging_efficiency
                )

                charging_log[ID_bus].at[start_time, "power"] = 0

            else:
                energy_slot = energy_slot - energy_delta
                energy_delta = 0

                # find the correct time slot in power and substract the power
                power_time = int(start_time / 900) * 900
                power_at_bus = energy_slot / (charg_log.end - charg_log.start)

                self.power.at[power_time, type_of_power] -= (
                    charg_log.power - power_at_bus
                ) / row_bus.charging_efficiency
                charging_log[ID_bus].at[start_time, "power"] = power_at_bus

            if energy_delta <= 0:
                break

    def precondition(self):
        """Writes only the power in the power DF not in the charging logs. Writes the power always in all three charging type columns"""

        self.power = self.power.sort_values(by=["start_time"])
        self.power["precondition"] = float(0)
        for ID_bus, row_bus in self.precondition_schedule.iterrows():
            for ID_power, row_power in self.power.iterrows():
                interval = self.intersection(
                    [row_power.start_time, row_power.end_time],
                    [row_bus.time_start, row_bus.time_ends],
                )
                if isinstance(interval, list):
                    factor_power_used = (interval[1] - interval[0]) / (
                        row_power.end_time - row_power.start_time
                    )
                    power = (
                        row_bus.charging_power / row_bus.charging_efficiency
                    ) * factor_power_used
                    self.power.at[ID_power, "used_power_immediately"] += power
                    self.power.at[ID_power, "used_power_smart"] += power
                    # self.power.at[ID_power,"used_power_smart_even"] += power
                    self.power.at[ID_power, "precondition"] += power

    def plot_results(self, language="eng"):
        """
        Plots the results
        :return:
        """
        power = self.power.sort_values(by=["start_time"])

        x = list(power.start_time)

        used_power_immediately = list(power.used_power_immediately)
        if hasattr(self, "precondition_schedule"):
            precondition = list(power.precondition)
        power_limit_grid = list(power.limit_grid)
        price = list(power.price)
        y_max = max(used_power_immediately) + 2000

        fig, ax1 = plt.subplots(figsize=(7, 4.5))
        eflips.depot.evaluation.setting_language(language)

        if language == "eng":
            ax1.set_xlabel("Time")
        else:
            ax1.set_xlabel("Zeit")

        ax1.set_title(
            "Charging power (all buses) and power price, Date: " + str(self.start_date)
        )

        # Convert x axis seconds to dates
        eflips.depot.evaluation.to_dateaxis(ax1)

        # plot used_power_smart
        if "used_power_smart" in power:
            used_power_smart = list(power.used_power_smart)
            ax1.set_ylim(0, y_max)
            if language == "eng":
                ax1.set_ylabel("Power [kW]")
            else:
                ax1.set_ylabel("Leistung [kW]")
            ax1.yaxis.grid(True)
            plt.fill_between(x, used_power_smart, 0, color="grey", alpha=0.7)

        # plot used_power_smart_even
        if "used_power_smart_even" in power:
            used_power_smart_even = list(power.used_power_smart_even)
            plt.plot(x, used_power_smart_even)

        # plot used_power_immediately
        plt.fill_between(x, used_power_immediately, 0, color="grey", alpha=0.3)

        # plot precondition
        if language == "eng":
            (precondition,) = ax1.plot(
                x,
                precondition,
                color="k",
                linestyle="--",
                linewidth=1,
                label="precondition",
            )
        else:
            (precondition,) = ax1.plot(
                x,
                precondition,
                color="k",
                linestyle="--",
                linewidth=1,
                label="Vorkonditionierung",
            )

        # plot power limit grid
        if language == "eng":
            (power_limit_grid,) = ax1.plot(
                x, power_limit_grid, color="k", linewidth=1, label="power limit grid"
            )
        else:
            (power_limit_grid,) = ax1.plot(
                x,
                power_limit_grid,
                color="k",
                linewidth=1,
                label="Limit Gesamtladeleistung",
            )

        # if language == 'eng':
        #     black_line = mlines.Line2D([], [], color='k', label='power limit grid')
        # else:
        #     black_line = mlines.Line2D([], [], color='k', label='power limit grid')

        # plot price
        ax2 = ax1.twinx()
        if language == "eng":
            (spot_price,) = ax2.plot(
                x, price, color="r", linewidth=1, label="spot price"
            )
        else:
            (spot_price,) = ax2.plot(
                x, price, color="r", linewidth=1, label="Preis Spotmarkt"
            )
        if language == "eng":
            ax2.set_ylabel("Spot price [€/kWh]", color="r")
        else:
            ax2.set_ylabel("Preis Spotmarkt [€/kWh]", color="r")
        ax2.tick_params("y", colors="r")
        ax2.set_ylim(0, max(price) + 0.02)

        if language == "eng":
            lightgrey_patch = mpatches.Patch(
                color="grey", label="non-controlled charging", alpha=0.3
            )
        else:
            lightgrey_patch = mpatches.Patch(
                color="grey", label="Lademanagement: Nicht gesteuert", alpha=0.3
            )
        if language == "eng":
            grey_patch = mpatches.Patch(color="grey", label="smart charging", alpha=0.7)
        else:
            grey_patch = mpatches.Patch(
                color="grey", label="Lademanagement: Smart", alpha=0.7
            )

        plt.legend(
            handles=[
                lightgrey_patch,
                grey_patch,
                power_limit_grid,
                precondition,
                spot_price,
            ],
            mode="expand",
            ncol=2,
        )

        plt.show()
        fig.savefig(
            globalConstants["depot"]["path_results"]
            + "charging_profile_non-controlled_vs_smart.pdf"
        )

    def plot_charging_intervalls_smart(self):
        self.plot_charging_intervalls(self.charging_log)

    def plot_charging_intervalls_smart_even(self):
        self.plot_charging_intervalls(self.charging_log_even)

    def plot_charging_intervalls(self, dict_charging_logs):
        """
        Plots a figure, where you can see the stress of the battries.
        :return:
        """

        # combines the diffrent stays of a vehicle to one "vehicle"
        dict_of_pd_plot = {}
        for key, value in dict_charging_logs.items():
            ID_bus = ""
            words = key.split("_")
            for i in range(len(words) - 1):
                if i == 0:
                    ID_bus += words[i]
                else:
                    ID_bus += "_" + words[i]
            try:
                pre_pd = dict_of_pd_plot[ID_bus]
            except:
                pre_pd = None
            dict_of_pd_plot[ID_bus] = pd.concat([pre_pd, value])

        fig2, ax10 = plt.subplots()
        ax10.set_xlabel("Time (hh:mm)\n Date (yyyy-mm-dd)")
        ax10.set_title("Charging time for each bus")

        # x Tick labels
        days = math.ceil(self.max_sim_time / 86400)
        current_day = self.start_date
        x_labels = []
        x_position = []
        for day in range(days):
            x_labels.append("00:00\n" + str(current_day))
            current_day += datetime.timedelta(days=1)
            x_position.append(day * 86400)
            x_labels.append("12:00")
            x_position.append(day * 86400 + 43200)
        ax10.set_xticks(x_position)
        ax10.set_xticklabels(x_labels)

        y_labels = []
        y_position_bar = 0
        for key, value in dict_of_pd_plot.items():
            y_position_bar += 5
            y_labels.append(key)
            broken_bar = []
            for index, row in value.iterrows():
                broken_bar.append([row.start, row.end - row.start])
                ax10.broken_barh(broken_bar, (y_position_bar, row.power * 0.027))

        ax10.set_yticks(list(range(5, y_position_bar, 5)))
        ax10.set_yticklabels(y_labels, fontdict={"fontsize": 4})

        plt.show

    def results(self):
        """
        Calculates some results and prints them.
        :return:
        """
        columns = ["Imm. Charging", "Smart Charging", "Comparison [%]"]
        results = pd.DataFrame(columns=columns)

        power_sorted_interval = self.power.sort_index()
        for index, row in power_sorted_interval.iterrows():
            if index < 86400 or index >= 172800:
                power_sorted_interval.drop([index], inplace=True)

        if "used_power_immediately" in self.power.columns:
            max_power_ic = round(max(self.power.used_power_immediately))
            results.at["max. Power (kW)", "Imm. Charging"] = max_power_ic

            energy_ic = round(
                (
                    self.power.used_power_immediately
                    * (self.power.end_time - self.power.start_time)
                    / 3600
                ).sum()
            )
            results.at["Energy (kWh)", "Imm. Charging"] = energy_ic

            energy_ic_interval = round(
                (
                    power_sorted_interval.used_power_immediately
                    * (
                        power_sorted_interval.end_time
                        - power_sorted_interval.start_time
                    )
                    / 3600
                ).sum()
            )
            results.at["Energy Interval (kWh)", "Imm. Charging"] = energy_ic_interval

            # energy_ic_cost_spot = round(((self.power.used_power_immediately * (self.power.end_time - self.power.start_time) / 3600) * self.power.price).sum(), 2)
            # results.at["Energy Costs Spot (€)", "Imm. Charging"] = energy_ic_cost_spot

            energy_ic_cost_fix = round(
                (
                    (
                        self.power.used_power_immediately
                        * (self.power.end_time - self.power.start_time)
                        / 3600
                    )
                    * 0.0433
                ).sum(),
                2,
            )
            results.at["Energy Costs (€)", "Imm. Charging"] = energy_ic_cost_fix

            energy_ic_cost_fix_interval = round(
                (
                    (
                        power_sorted_interval.used_power_immediately
                        * (
                            power_sorted_interval.end_time
                            - power_sorted_interval.start_time
                        )
                        / 3600
                    )
                    * 0.0433
                ).sum(),
                2,
            )
            results.at[
                "Energy Costs Interval (€)", "Imm. Charging"
            ] = energy_ic_cost_fix_interval

            grid_ic_cost = round(
                max_power_ic * 66.82 / 365 * self.max_sim_time / (24 * 60 * 60)
                + energy_ic * 0.0098,
                2,
            )
            results.at["Grid Costs (€)", "Imm. Charging"] = grid_ic_cost

            grid_ic_cost_interval = round(
                max_power_ic * 66.82 / 365 + energy_ic_interval * 0.0098, 2
            )
            results.at[
                "Grid Costs Interval (€)", "Imm. Charging"
            ] = grid_ic_cost_interval

            usage_per_year_ic = round(
                energy_ic / max_power_ic / self.max_sim_time * 365 * 24 * 60 * 60
            )
            results.at["Usage Hours per Year (h)", "Imm. Charging"] = usage_per_year_ic

            usage_per_year_ic_interval = round(energy_ic_interval / max_power_ic * 365)
            results.at[
                "Usage Hours per Year Interval (h)", "Imm. Charging"
            ] = usage_per_year_ic_interval

            usage_per_day_ic_interval = round(energy_ic_interval / max_power_ic, 2)
            results.at[
                "Usage Hours per Day Interval (h)", "Imm. Charging"
            ] = usage_per_day_ic_interval

        if "used_power_smart" in self.power.columns:
            max_power_sc = round(max(self.power.used_power_smart))
            results.at["max. Power (kW)", "Smart Charging"] = max_power_sc

            energy_sc = round(
                (
                    self.power.used_power_smart
                    * (self.power.end_time - self.power.start_time)
                    / 3600
                ).sum()
            )
            results.at["Energy (kWh)", "Smart Charging"] = energy_sc

            energy_sc_interval = round(
                (
                    power_sorted_interval.used_power_smart
                    * (
                        power_sorted_interval.end_time
                        - power_sorted_interval.start_time
                    )
                    / 3600
                ).sum()
            )
            results.at["Energy Interval (kWh)", "Smart Charging"] = energy_sc_interval

            energy_sc_cost_spot = round(
                (
                    (
                        self.power.used_power_smart
                        * (self.power.end_time - self.power.start_time)
                        / 3600
                    )
                    * self.power.price
                ).sum(),
                2,
            )
            results.at["Energy Costs (€)", "Smart Charging"] = energy_sc_cost_spot

            energy_sc_cost_spot_interval = round(
                (
                    (
                        power_sorted_interval.used_power_smart
                        * (
                            power_sorted_interval.end_time
                            - power_sorted_interval.start_time
                        )
                        / 3600
                    )
                    * power_sorted_interval.price
                ).sum(),
                2,
            )
            results.at[
                "Energy Costs Interval (€)", "Smart Charging"
            ] = energy_sc_cost_spot_interval

            # energy_sc_cost_fix = round(((self.power.used_power_smart * (self.power.end_time - self.power.start_time) / 3600) * 4.33).sum(), 2)
            # results.at["Energy Costs Fix Price (€)", "Smart Charging"] = energy_sc_cost_fix

            grid_sc_cost = round(
                max_power_sc * 66.82 / 365 * self.max_sim_time / (24 * 60 * 60)
                + energy_sc * 0.0098,
                2,
            )
            results.at["Grid Costs (€)", "Smart Charging"] = grid_sc_cost

            grid_sc_cost_interval = round(
                max_power_sc * 66.82 / 365 + energy_sc_interval * 0.0098, 2
            )
            results.at[
                "Grid Costs Interval (€)", "Smart Charging"
            ] = grid_sc_cost_interval

            usage_per_year_sc = round(
                energy_sc / max_power_sc / self.max_sim_time * 365 * 24 * 60 * 60
            )
            results.at["Usage Hours per Year (h)", "Smart Charging"] = usage_per_year_sc

            usage_per_year_sc_interval = round(energy_sc_interval / max_power_sc * 365)
            results.at[
                "Usage Hours per Year Interval (h)", "Smart Charging"
            ] = usage_per_year_sc_interval

            usage_per_day_sc_interval = round(energy_sc_interval / max_power_sc, 2)
            results.at[
                "Usage Hours per Day Interval (h)", "Smart Charging"
            ] = usage_per_day_sc_interval

        if "used_power_smart" and "used_power_immediately" in self.power.columns:
            results.at["max. Power (kW)", "Comparison [%]"] = round(
                (max_power_sc / max_power_ic - 1) * 100, 2
            )
            results.at["Energy (kWh)", "Comparison [%]"] = round(
                (energy_sc / energy_ic - 1) * 100, 2
            )
            results.at["Energy Interval (kWh)", "Comparison [%]"] = round(
                (energy_sc_interval / energy_ic_interval - 1) * 100, 2
            )
            results.at["Energy Costs (€)", "Comparison [%]"] = round(
                (energy_sc_cost_spot / energy_ic_cost_fix - 1) * 100, 2
            )
            results.at["Energy Costs Interval (€)", "Comparison [%]"] = round(
                (energy_sc_cost_spot_interval / energy_ic_cost_fix_interval - 1) * 100,
                2,
            )
            # results.at["Energy Costs Fix Price (€)", "Comparison [%]"] = round((energy_sc_cost_fix / energy_ic_cost_fix - 1) * 100, 2)
            results.at["Grid Costs (€)", "Comparison [%]"] = round(
                (grid_sc_cost / grid_ic_cost - 1) * 100, 2
            )
            results.at["Grid Costs Interval (€)", "Comparison [%]"] = round(
                (grid_sc_cost_interval / grid_ic_cost_interval - 1) * 100, 2
            )
            results.at["Usage Hours per Year (h)", "Comparison [%]"] = round(
                (usage_per_year_sc / usage_per_year_ic - 1) * 100, 2
            )
            results.at["Usage Hours per Year Interval (h)", "Comparison [%]"] = round(
                (usage_per_year_sc_interval / usage_per_year_ic_interval - 1) * 100, 2
            )
            results.at["Usage Hours per Day Interval (h)", "Comparison [%]"] = round(
                (usage_per_day_sc_interval / usage_per_day_ic_interval - 1) * 100, 2
            )

        # Outdated, not maintained
        """if "used_power_smart_even" in self.power.columns:
            results.at["Smart Charging Even", "Energy (kWh)"] = round((self.power.used_power_smart_even * ( self.power.end_time - self.power.start_time) / 3600).sum(),2)
            results.at["Smart Charging Even", "Cost of Energy (€)"] = round(((
                        self.power.used_power_smart_even * (self.power.end_time - self.power.start_time) / 3600) * self.power.price).sum(),2)
            results.at["Smart Charging Even", "Period of use (min)"] = self.period_of_use("used_power_smart_even")"""

        # self.results = results

        return results

    def period_of_use(self, type):
        """
        Calculates the time [minutes] which the grid infrastructure is used.
        :param type:
        :return:
        """
        count = self.power.loc[self.power[type] > 0].count().start_time
        return count * 15

    def export_for_vehicle_periods_smart(self):
        return self.export_for_vehicle_periods(self.charging_log)

    def export_for_vehicle_periods_smart_even(self):  # Outdated, not maintained
        return self.export_for_vehicle_periods(self.charging_log_even)

    def export_for_vehicle_periods(self, dict_charging_logs):
        """Brings the data to an format, evalutatio.DepotEvaluation.vehicle_periods can work with."""

        dict_of_pd_plot = {}
        for key, value in dict_charging_logs.items():
            ID_bus = ""
            words = key.split("_")
            for i in range(len(words) - 1):
                if i == 0:
                    ID_bus += words[i]
                else:
                    ID_bus += "_" + words[i]

            try:
                pre_pd = dict_of_pd_plot[ID_bus]
            except:
                pre_pd = None
            dict_of_pd_plot[ID_bus] = pd.concat([pre_pd, value])

        vehicle_periods = {}
        i = 0
        for vehicle_id in self.vehicles_id:
            i += 1
            vehicle_periods[vehicle_id] = {"xranges": [], "yranges": (i, 0.5)}
            if vehicle_id in dict_of_pd_plot:
                for index, row in dict_of_pd_plot[vehicle_id].iterrows():
                    vehicle_periods[vehicle_id]["xranges"].append(
                        (row.start, row.end - row.start)
                    )

        return vehicle_periods

    def validation(self):
        """Validates some values, and prints weired occurrences, which might be checked by the user"""

        results = self.results()
        valid = True
        total_energy_real = self.schedule["energy_demand_real"].sum() / 3600

        # Check precondition durations
        if (
            self.precondition_schedule["time_ends"]
            - self.precondition_schedule["time_start"]
        ).min() < 0 and (
            self.precondition_schedule["time_ends"]
            - self.precondition_schedule["time_start"]
        ).max() > 1800:
            print("Precondition duration is higher than configured")
            valid = False

        # Check precondition energy
        precon_energy = (
            self.precondition_schedule["time_ends"]
            - self.precondition_schedule["time_start"]
        ) * self.precondition_schedule["charging_power"]
        precon_energy.sum()

        # Check energy_real (energy in charging processes) bigger than sum of energy sorted in intervalls
        if total_energy_real > results.at["Energy (kWh)", "Smart Charging"]:
            print(
                "Violation of energy conservation rate. The buses loaded with more enegry then the grid was providing. Smart Charging"
            )
            valid = False

        # Check energy_real (energy in charging processes) bigger than sum of energy sorted in intervalls
        if total_energy_real > results.at["Energy (kWh)", "Imm. Charging"]:
            print(
                "Violation of energy conservation rate. The buses loaded with more enegry then the grid was providing. Immediately Charging"
            )
            valid = False

        if (
            1
            - abs(
                results.at["Energy (kWh)", "Imm. Charging"]
                / results.at["Energy (kWh)", "Smart Charging"]
            )
            > 0.01
        ):
            print("Immediately Charging and Smart Charging differs more then 1 percent")
            valid = False

        for vID, df in self.charging_log.items():
            if df["power"].nunique() >= 8:
                print(
                    "Bus "
                    + vID
                    + " charged with "
                    + str(df["power"].nunique())
                    + " different power values."
                )
                valid = False

        print("Smart charging is valid: " + str(valid))


class ControlSmartCharging:
    """Some control units for the Smart Charging class. Does multiple runs with the same basic settings."""

    def __init__(
        self,
        data,
        start_date,
        price_data_path,
        power_limit_grid=0,
        charging_efficiency=1,
    ):
        """

        :param simulation_host:
        :param start_date:
        :param price_data_path:
        :param power_limit_grid: int or list of powerlimits (15 min intervalls) [or an function] not yet implemnted
        :param capacity_charge: "Jahresleistungspreis" in EUR/kW*a
        :param charging_efficiency:
        """
        self.smart_charging = eflips.depot.SmartCharging(
            data, start_date, price_data_path, power_limit_grid, charging_efficiency
        )
        self.power_limit_gird = power_limit_grid

    def lowest_power(self, accuracy):
        """
        Uses as the first upper boundary the max power of immediately charging, if no power_limit_grid is given, only works if the power_limit_grid is an int
        :param accuracy: in percent
        :return: results of the last succesful power run, power_lower_boundary and power_upper_boundary
        """
        accuracy = accuracy / 100
        if not (isinstance(self.power_limit_grid, int)):
            raise Exception(
                "Function (lowest_power) only works if, the power_limit_grid is an integer."
            )
        if self.power_limit_gird <= 0:
            potential_power_boundary = self.power.max().used_power_immediately
        else:
            potential_power_boundary = self.power_limit_gird
        power_upper_boundary = potential_power_boundary
        power_lower_boundary = 1  # because of not dividing by zero

        self.smart_charging.set_power_limit_grid(potential_power_boundary)
        if self.smart_charging.smart_charging_algorithm():
            power_upper_boundary = potential_power_boundary
            potential_power_boundary = (
                power_upper_boundary - (power_upper_boundary - power_lower_boundary) / 2
            )
            deviation = (
                power_upper_boundary - power_lower_boundary
            ) / power_lower_boundary
        else:
            print("Power limit grid is to low.")
            return

        while accuracy < deviation:
            self.smart_charging.set_power_limit_grid(potential_power_boundary)
            if self.smart_charging.smart_charging_algorithm():
                power_upper_boundary = potential_power_boundary
                potential_power_boundary = (
                    power_upper_boundary
                    - (power_upper_boundary - power_lower_boundary) / 2
                )
                deviation = (
                    power_upper_boundary - power_lower_boundary
                ) / power_lower_boundary
            else:
                power_lower_boundary = potential_power_boundary
                potential_power_boundary = (
                    power_upper_boundary
                    - (power_upper_boundary - power_lower_boundary) / 2
                )
                deviation = (
                    power_upper_boundary - power_lower_boundary
                ) / power_lower_boundary

        # Just for safety.
        self.smart_charging.set_power_limit_grid(power_upper_boundary)
        self.smart_charging.smart_charging_algorithm()

        self.smart_charging.immediately_charging()

        self.smart_charging.smart_charging_algorithm_even()
        self.smart_charging.plot_results()
        self.smart_charging.plot_charging_intervalls_smart()
        self.smart_charging.plot_charging_intervalls_smart_even()

        return self.smart_charging.results(), power_lower_boundary, power_upper_boundary
