"""An example code to show how to start with eFLIPS-depot"""

import os
import depot.api.basic


import djangosettings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.djangosettings")
import django
from django.core import management

django.setup()
management.call_command("migrate")


absolute_path = os.path.dirname(__file__)

# File configuration
filename_eflips_settings = os.path.join(
    absolute_path, "eflips_settings/kls_diss_settings_210219"
)
filename_schedule = os.path.join(
    absolute_path, "schedules/schedule_kls_diss_scenario1_SB_DC_AB_OC_210203"
)
filename_template = os.path.join(
    absolute_path,
    "templates/diss_kls_6xS, 94x150kW_SB, 147x75kW_AB, shunting+precond+chargeequationsteps",
)

# Setup Simulation Host
# TODO: replace with read from database
host = depot.api.basic.init_simulation(
    filename_eflips_settings, filename_schedule, filename_template
)

# TODO: run ebustoolbox.tasks.run_ebus_toolbox() to get the input files for eflips
# TODO: load eflips_input.json


# Run simulation
# TODO: run_simulation(data_from_database, data_from_simba)
ev = depot.api.basic.run_simulation(host)

# Generate input data for simBA
data_for_simba = depot.api.basic.to_simba(ev)
