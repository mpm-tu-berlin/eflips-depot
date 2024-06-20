import os
import django

os.environ["DJANGO_SETTINGS_MODULE"] = "eflips.depot.api.private.djangosettings"

django.setup()

from ebustoolbox.models import Scenario as DjangoScenario, Event as DjangoEvent
from ebustoolbox.tasks import is_consistent, run_simba_scenario

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from eflips.model import Scenario, Vehicle

SCENARIO_ID = 8

# Database row mapping to DjangoScenario
django_scenario = DjangoScenario.objects.filter(id=SCENARIO_ID).first()

assert is_consistent(django_scenario)
schedule, simbascenario = run_simba_scenario(django_scenario, assign_vehicles=True)

while DjangoEvent.objects.filter(scenario_id=SCENARIO_ID, soc_end__lt=0).count() > 0:
    schedule, simbascenario = run_simba_scenario(
        django_scenario, simba_scenario=simbascenario, mode="station_optimization_single_step"
    )

# Now the session to database through django is supposed to be closed

# Open a session through sqlalchemy in eflips
# Plotting by eflips-eval also through sqlalchemy session

EFLIPS_DB = os.environ["DATABASE_URL"].replace("postgis", "postgresql")
PLOT = True
if PLOT:
    eflips_engine = create_engine(EFLIPS_DB, echo=False)
    with Session(eflips_engine) as session:

        # Interaction with database through sqlalchemy goes here. Here the row of database is mapped to objects in
        # eflips-model
        try:
            import eflips.eval.input.prepare
            import eflips.eval.input.visualize
            import eflips.eval.output.prepare
            import eflips.eval.output.visualize

        except ImportError:
            print(
                "The eflips.eval package is not installed. Visualization is not possible."
            )
            print(
                "If you want to visualize the results, install the eflips.eval package using "
                "pip install eflips-eval"
            )

        else:
            eflips_scenario = session.query(Scenario).filter(Scenario.id == SCENARIO_ID).one()
            OUTPUT_DIR = os.path.join("output", eflips_scenario.name)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            for depot in eflips_scenario.depots:
                DEPOT_NAME = depot.station.name
                DEPOT_OUTPUT_DIR = os.path.join(OUTPUT_DIR, DEPOT_NAME)
                os.makedirs(DEPOT_OUTPUT_DIR, exist_ok=True)

                # An example vehicle
                vehicle = session.query(Vehicle).filter(Vehicle.scenario_id == SCENARIO_ID).first()

                # Visualize the vehicle state of charge
                df, descriptions = eflips.eval.output.prepare.vehicle_soc(vehicle.id, session)

                fig = eflips.eval.output.visualize.vehicle_soc(df, descriptions)
                fig.update_layout(title=f"Vehicle {vehicle.id} SoC over time")
                fig.write_html(
                    os.path.join(
                        DEPOT_OUTPUT_DIR, f"vehicle_{vehicle.id}_soc.html"
                    )
                )
                fig.show()

    # Now the session to database through sqlalchemy is closed
