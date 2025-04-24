#! /usr/bin/env python3
import argparse
import json
import logging
import os
import warnings

from eflips.model import *
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tqdm.auto import tqdm

from eflips.depot.api import (
    delete_depots,
    group_rotations_by_start_end_stop,
    simple_consumption_simulation,
)
from eflips.depot.api.private.depot import depot_smallest_possible_size


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
    parser.add_argument(
        "--simulation_core_diagram",
        help="Print the simulation core diagram. This is an older diagram from teh simulation core that my be useful for"
        " debugging.",
        required=False,
        action="store_true",
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
            "The scenario id must be specified. Use --list-scenarios to see all available scenarios, then run with "
            "--scenario-id <id>."
        )

    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    engine = create_engine(args.database_url, echo=False)
    with Session(engine) as session:
        scenario = session.query(Scenario).filter(Scenario.id == args.scenario_id).one()
        assert isinstance(scenario, Scenario)

        # from eflips.depot.api import capacity_estimation
        #
        # estimated_capacity = capacity_estimation(scenario, session)
        # print(f"Estimated capacity: {estimated_capacity}")
        #
        # from eflips.depot.api.private.capacity_estimation import update_depot_capacities
        #
        # update_depot_capacities(scenario, session, estimated_capacity)

        # from eflips.depot.api.private.binpacking import DepotLayout

        # # TODO test it with example depot
        depot = scenario.depots[0]
        #
        # layout = DepotLayout(depot=depot, max_driving_lane_width=8)
        # (
        #     placed_areas,
        #     driving_lanes,
        #     final_width,
        #     final_height,
        # ) = layout.best_possible_packing()
        # layout.visualize(placed_areas, driving_lanes, final_width, final_height)

        # TODO is it possible to integrate two packing approaches into one class?

        from eflips.depot.api.private.binpacking_partial_conflicts import (
            best_possible_packing_parcial,
        )

        (
            placed_areas,
            driving_lanes,
            final_width,
            final_length,
            available_spaces,
        ) = best_possible_packing_parcial(session, depot)
