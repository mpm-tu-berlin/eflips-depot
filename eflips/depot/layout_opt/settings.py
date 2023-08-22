# -*- coding: utf-8 -*-
"""Load and check optimization and scenario settings.

EXEC_TIME: [str] top-level value appended during loading. Example:
      '2018-11-13_1800'

FILENAME: [str] top-level value appended during loading. filename argument
    that was passed to load_settings()
"""
import datetime
from eflips.helperFunctions import load_json


OPT_CONSTANTS = {"algorithm": {}, "scenario": {}}


def load_settings(filename):
    """Load custom opt settings from a json file.

    filename: [str] excluding file extension
    """
    OPT_CONSTANTS.clear()
    custom = load_json(filename)
    OPT_CONSTANTS.update(custom)

    # Append execution time
    OPT_CONSTANTS["EXEC_TIME"] = str(datetime.datetime.now().strftime("%Y-%m-%d_%H%M"))

    # Append filename
    OPT_CONSTANTS["FILENAME"] = filename
