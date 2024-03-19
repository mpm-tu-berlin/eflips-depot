#! /usr/bin/env python3
import argparse
import os
from datetime import timedelta

from eflips.model import *
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from eflips.depot.api import (
    add_evaluation_to_database,
    init_simulation,
    run_simulation,
    generate_depot_layout,
    simple_consumption_simulation,
)


def list_scenarios(database_url: str):
    engine = create_engine(database_url, echo=False)
    with Session(engine) as session:
        scenarios = session.query(Scenario).all()
        for scenario in scenarios:
            rotation_count = (
                session.query(Rotation)
                .filter(Rotation.scenario_id == scenario.id)
                .count()
            )
            print(f"{scenario.id}: {scenario.name} with {rotation_count} rotations.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario_id",
        "--scenario-id",
        type=int,
        help="The id of the scenario to be simulated. Run with --list-scenarios to see all available scenarios.",
    )
    parser.add_argument(
        "--list_scenarios",
        "--list-scenarios",
        action="store_true",
        help="List all available scenarios.",
    )
    parser.add_argument(
        "--database_url",
        "--database-url",
        type=str,
        help="The url of the database to be used. If it is not specified, the environment variable DATABASE_URL is used.",
        required=False,
    )
    args = parser.parse_args()

    if args.database_url is None:
        if "DATABASE_URL" not in os.environ:
            raise ValueError(
                "The database url must be specified either as an argument or as the environment variable DATABASE_URL."
            )
        args.database_url = os.environ["DATABASE_URL"]

    if args.list_scenarios:
        list_scenarios(args.database_url)
        exit()

    if args.scenario_id is None:
        raise ValueError(
            "The scenario id must be specified. Use --list-scenarios to see all available scenarios, then run with --scenario-id <id>."
        )

    engine = create_engine(args.database_url, echo=False)
    with Session(engine) as session:
        scenario = session.query(Scenario).filter(Scenario.id == args.scenario_id).one()
        assert isinstance(scenario, Scenario)

        generate_depot_layout(
            scenario=scenario, charging_power=90, delete_existing_depot=True
        )

        for vehicle_type in scenario.vehicle_types:
            vehicle_type.consumption = 1

        # Delete all vehicles and events, also disconnect the vehicles from the rotations
        rotation_q = session.query(Rotation).filter(Rotation.scenario_id == scenario.id)
        rotation_q.update({"vehicle_id": None})
        session.query(Event).filter(Event.scenario_id == scenario.id).delete()
        session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).delete()

        # Using simple consumption simulation

        simple_consumption_simulation(scenario=scenario, initialize_vehicles=True)
        simulation_host = init_simulation(
            scenario=scenario,
            session=session,
            repetition_period=timedelta(days=7),
        )

        depot_evaluations = run_simulation(simulation_host)

        # vehicle_counts: Dict[str, Dict[str, int]] = {}
        # for depot_id, depot_evaluation in depot_evaluations.items():
        #    vehicle_counts[depot_id] = depot_evaluation.nvehicles_used_calculation()

        # simulation_host = init_simulation(
        #    scenario=scenario,
        #    session=session,
        #    vehicle_count_dict=vehicle_counts,
        # )
        # depot_evaluations = run_simulation(simulation_host)

        if True:
            os.makedirs(os.path.join("output", scenario.name), exist_ok=True)
            for depot_id, depot_evaluation in depot_evaluations.items():
                os.makedirs(
                    os.path.join("output", scenario.name, depot_id), exist_ok=True
                )
                depot_evaluation.path_results = os.path.join(
                    "output", scenario.name, depot_id
                )

                depot_evaluation.vehicle_periods(
                    periods={
                        "depot general": "darkgray",
                        "park": "lightgray",
                        "Arrival Cleaning": "steelblue",
                        "Charging": "forestgreen",
                        "Standby Pre-departure": "darkblue",
                        "precondition": "black",
                        "trip": "wheat",
                    },
                    save=True,
                    show=False,
                    formats=(
                        "pdf",
                        "png",
                    ),
                    show_total_power=True,
                    show_annotates=True,
                )

        add_evaluation_to_database(scenario.id, depot_evaluations, session)

        session.commit()

        simple_consumption_simulation(scenario=scenario, initialize_vehicles=False)

        session.commit()
