import os
import django

os.environ["DJANGO_SETTINGS_MODULE"] = "eflips.depot.api.private.djangosettings"

django.setup()

from ebustoolbox.models import Scenario, Rotation
from ebustoolbox.tasks import is_consistent, run_simba_scenario

scenario = Scenario.objects.filter(name="All Rotations starting and ending at Betriebshof Cicerostr.").first()

assert is_consistent(scenario)
schedule, simbascenario = run_simba_scenario(scenario, assign_vehicles=True, db_url=os.environ["DATABASE_URL"])

schedule, simbascenario = run_simba_scenario(
    scenario, simba_scenario=simbascenario, db_url=os.environ["DATABASE_URL"], mode="station_optimization_single_step"
)
