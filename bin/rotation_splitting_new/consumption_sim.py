import argparse
import warnings
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from eflips.model import *
from eflips.depot.api import simple_consumption_simulation

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario_id",
        "--scenario-id",
        type=int,
    )
    parser.add_argument(
        "--database_url",
        "--database-url",
        type=str
    )
    args = parser.parse_args()
    engine = create_engine(args.database_url)
    with Session(engine) as session:
        scenario = session.query(Scenario).filter(Scenario.id == args.scenario_id).one()
        rotation_q = session.query(Rotation).filter(Rotation.scenario_id == scenario.id)
        rotation_q.update({"vehicle_id": None})
        session.query(Event).filter(Event.scenario_id == scenario.id).delete()
        session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).delete()
        warnings.simplefilter("ignore", category=ConsistencyWarning)
        simple_consumption_simulation(scenario=scenario, initialize_vehicles=True)
        session.commit()
