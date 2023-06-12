# -*- coding: utf-8 -*-

# 1st: Namespaced Package Compat., see https://packaging.python.org/en/latest/guides/packaging-namespace-packages/
__path__ = __import__('pkgutil').extend_path(__path__, __name__)


# 2nd: Importing of the eflips modules -> TODO Cleanups here to avoid all those imports

"""
This init file puts the components required for simulations into a single
namespace.

@author: e.lauth
"""

import eflips.depot
import eflips.settings
from eflips.evaluation import DataLogger, DataGatherer, Evaluation
from eflips.helperFunctions import flexprint, Tictoc, progressbar, save_json, \
    load_json
from eflips.settings import globalConstants, load_settings
from eflips.simpy_ext import FilterStoreExt, PositionalStore, \
    PositionalFilterStore, LineFilterStore, StoreConnector
