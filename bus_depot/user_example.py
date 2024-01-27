#! /usr/bin/env python3
import argparse

from eflips.model import *
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from eflips.depot.api import (
    _add_evaluation_to_database,
    _init_simulation,
    _run_simulation,
)

DATABASE_URL = "postgresql://ludger:@/bvg_schedule_all?host=/var/run/postgresql"


engine = create_engine(
    DATABASE_URL, echo=False
)  # Change echo to True to see SQL queries
session = Session(engine)

scenario = session.query(Scenario).filter(Scenario.id == 1).one()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario_id",
        type=int,
        help="The id of the scenario to be simulated. Run with --list-scenarios to see all available scenarios.",
    )
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="List all available scenarios.",
    )
    parser.add_argument(
        "--database_url",
        type=str,
        help="The url of the database to be used. If it is not specified, the environment variable DATABASE_URL is used.",
        required=False,
    )
    args = parser.parse_args()

    # Initialize the simulation
    _init_simulation(scenario, session)

    # Run the simulation
    _run_simulation(scenario, session)

    # Add the evaluation to the database
    _add_evaluation_to_database(scenario, session)
