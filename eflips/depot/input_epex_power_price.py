"""Crawl spot prices and built data frame."""
import pandas as pd
import datetime
import pickle


class InputReader:
    LINK_INTRADAY = (
        "https://www.epexspot.com/en/market-data/intradaycontinuous/intraday-table/"
    )
    LINK_DAY_AHEAD = (
        "https://www.epexspot.com/en/market-data/dayaheadauction/auction-table/"
    )
    COUNTRY_INTRADAY = "/DE"
    COUNTRY_DAY_AHEAD = "/DE_LU/24"

    def __init__(self, type, start_date, end_date, safe_path):
        self.type = type  # "day_ahead" or "intraday"
        self.start_date = start_date
        self.end_date = end_date
        self.safe_path = safe_path

    def start_input(self):
        """Decides which input reader to run."""

        if self.type == "day_ahead":
            self.start_date += datetime.timedelta(days=6)
            self.input_day_ahead()
        elif self.type == "intraday":
            self.input_intraday()
        else:
            print("No valid type.")

    def input_day_ahead(self):
        """Input reader for https://www.epexspot.com/en/market-data/dayaheadauction/auction-table/2018-10-27/DE_LU/24"""

        date = self.start_date
        while date <= self.end_date:
            raw_pd_frame = pd.read_html(
                InputReader.LINK_DAY_AHEAD + str(date) + InputReader.COUNTRY_DAY_AHEAD
            )[8]
            pd_frame = raw_pd_frame.dropna()
            index = pd_frame.iloc[:, 0]

            i = 2
            while i < 9:
                values = pd_frame.iloc[:, i] / 1000
                series = pd.Series(list(values), index=index)
                date_name = str(
                    date + datetime.timedelta(days=(i - 8))
                )  # because of the +6 on top and the structure of the pd_frame it has to be +8
                pickle.dump(series, open(self.safe_path + date_name + ".p", "wb"))
                i += 1

            print(str(date) + ":imported")
            date = date + datetime.timedelta(days=7)

    def input_intraday(self):
        """Input reader for https://www.epexspot.com/en/market-data/intradaycontinuous/intraday-table/2018-10-18/DE"""
        date = self.start_date
        vector_row_needed = [
            2,
            3,
            5,
            6,
            9,
            10,
            12,
            13,
            16,
            17,
            19,
            20,
            23,
            24,
            26,
            27,
            30,
            31,
            33,
            34,
            37,
            38,
            40,
            41,
            44,
            45,
            47,
            48,
            51,
            52,
            54,
            55,
            58,
            59,
            61,
            62,
            65,
            66,
            68,
            69,
            72,
            73,
            75,
            76,
            79,
            80,
            82,
            83,
            86,
            87,
            89,
            90,
            93,
            94,
            96,
            97,
            100,
            101,
            103,
            104,
            107,
            108,
            110,
            111,
            114,
            115,
            117,
            118,
            121,
            122,
            124,
            125,
            128,
            129,
            131,
            132,
            135,
            136,
            138,
            139,
            142,
            143,
            145,
            146,
            149,
            150,
            152,
            153,
            156,
            157,
            159,
            160,
            163,
            164,
            166,
            167,
        ]
        while date < (self.end_date + datetime.timedelta(days=1)):
            date += datetime.timedelta(days=1)

            raw_pd_frame = pd.read_html(
                InputReader.LINK_INTRADAY + str(date) + InputReader.COUNTRY_INTRADAY
            )[0]
            values = raw_pd_frame.iloc[vector_row_needed, 7].astype("float") / 1000
            index = raw_pd_frame.iloc[vector_row_needed, 2]
            series = pd.Series(list(values), index=index)
            pickle.dump(
                series,
                open(
                    self.safe_path + str(date - datetime.timedelta(days=1)) + ".p", "wb"
                ),
            )
            print("Import complet: " + str(date - datetime.timedelta(days=1)))


class PowerFrame:
    """Constructs a pdFrame, which is used by the smart charging skript."""

    def __init__(self, max_sim_time, start_date, price_data_path):
        """Decides which pd constructer."""
        self.max_sim_time = max_sim_time
        self.start_date = start_date
        self.price_data_path = price_data_path

    def pdframe(self):
        """
        :return: power-pdframe with the power prices
        """
        power = pd.DataFrame(columns=["start_time", "end_time", "price"])
        current_day = self.start_date
        time_in_sec = 0  # sim time in sec
        while time_in_sec <= self.max_sim_time:
            power_price = pickle.load(
                open(self.price_data_path + str(current_day) + ".p", "rb")
            )
            time_delta = 86400 / power_price.size
            for price in power_price:
                row = [time_in_sec, time_in_sec + time_delta, price]
                power.loc[time_in_sec] = row
                time_in_sec += time_delta
            current_day += datetime.timedelta(days=1)

        return power
