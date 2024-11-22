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

        ##### Step 0: Clean up the database, remove results from previous runs #####

        # Delete all vehicles and events, also disconnect the vehicles from the rotations
        rotation_q = session.query(Rotation).filter(Rotation.scenario_id == scenario.id)
        rotation_q.update({"vehicle_id": None})
        session.query(Event).filter(Event.scenario_id == scenario.id).delete()
        session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).delete()

        # Delete the old depot
        # This is a private API method automatically called by the generate_depot_layout method
        # It is run here explicitly for clarity.
        delete_depots(scenario, session)

        # Temporary workaround to set vehicle energy consumption manually
        # TODO: Replace by "use DS consumption if LUT"
        for vehicle_type in (
            session.query(VehicleType)
            .filter(VehicleType.scenario_id == scenario.id)
            .all()
        ):
            vehicle_type.consumption = 2.0
            vehicle_type.vehicle_classes = []

        ##### Step 1: Find all potential depots #####
        # These are all the spots where a rotation starts and end
        warnings.simplefilter("ignore", category=ConsistencyWarning)
        warnings.simplefilter("ignore", category=UserWarning)

        for (
            first_last_stop_tup,
            vehicle_type_dict,
        ) in group_rotations_by_start_end_stop(scenario.id, session).items():
            first_stop, last_stop = first_last_stop_tup
            if first_stop != last_stop:
                raise ValueError("First and last stop of a rotation are not the same.")

            station = first_stop

            savepoint = session.begin_nested()
            try:
                # (Temporarily) Delete all rotations not starting or ending at the station
                all_rot_for_scenario = (
                    session.query(Rotation)
                    .filter(Rotation.scenario_id == scenario.id)
                    .all()
                )
                to_delete = []
                for rot in tqdm(all_rot_for_scenario):
                    first_stop = rot.trips[0].route.departure_station
                    if first_stop != station:
                        for trip in rot.trips:
                            for stop_time in trip.stop_times:
                                to_delete.append(stop_time)
                            to_delete.append(trip)
                        to_delete.append(rot)
                for obj in tqdm(to_delete):
                    session.delete(obj)

                logger.info(f"Generating depot layout for station {station.name}")
                vt_capacities_for_station = depot_smallest_possible_size(
                    station, scenario, session, charging_power=150
                )

                # Change the dictionary for pickling:
                # Replace the vehicle type objects with their ids
                vt_capacities_for_station = {
                    vt.id: capacity
                    for vt, capacity in vt_capacities_for_station.items()
                }
                # Replace the AreaType objects with their string representation
                for vt_id, capacity in vt_capacities_for_station.items():
                    vt_capacities_for_station[vt_id] = {
                        str(area_type): capacity
                        for area_type, capacity in capacity.items()
                    }

                with open(
                    f"depot_at_{station.id} (Scenario {scenario.id} station {station.name}.json",
                    "w",
                ) as f:
                    json.dump(vt_capacities_for_station, f, indent=4)
            finally:
                savepoint.rollback()
