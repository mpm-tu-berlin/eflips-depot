#! /usr/bin/env python3
import argparse
import os
from datetime import timedelta

from eflips.model import *
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from eflips.depot.api import (
    _add_evaluation_to_database,
    _init_simulation,
    _run_simulation,
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


def add_simple_depot(scenario: Scenario, session: Session):
    # Create a simple depot
    # See if a depot already exists
    depot_q = session.query(Depot).filter(Depot.scenario_id == scenario.id)
    if depot_q.count() > 0:
        return depot_q.one()

    depot = Depot(scenario=scenario, name="Test Depot", name_short="TD")
    session.add(depot)

    # Create plan

    plan = Plan(scenario=scenario, name="Test Plan")
    session.add(plan)

    depot.default_plan = plan

    # Create processes
    standby_arrival = Process(
        name="Standby Arrival",
        scenario=scenario,
        dispatchable=False,
    )
    clean = Process(
        name="Arrival Cleaning",
        scenario=scenario,
        dispatchable=False,
        duration=timedelta(minutes=30),
    )
    charging = Process(
        name="Charging",
        scenario=scenario,
        dispatchable=False,
        electric_power=90,
    )
    standby_departure = Process(
        name="Standby Pre-departure",
        scenario=scenario,
        dispatchable=True,
    )
    session.add(standby_arrival)
    session.add(clean)
    session.add(charging)
    session.add(standby_departure)

    # Create areas for each vehicle type
    for vehicle_type in scenario.vehicle_types:
        CAPACITY = 2000
        # Create areas
        arrival_area = Area(
            scenario=scenario,
            name=f"Arrival for {vehicle_type.name_short}",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=CAPACITY,
        )
        session.add(arrival_area)
        arrival_area.vehicle_type = vehicle_type

        cleaning_area = Area(
            scenario=scenario,
            name=f"Cleaning Area for {vehicle_type.name_short}",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=CAPACITY,
        )
        session.add(cleaning_area)
        cleaning_area.vehicle_type = vehicle_type

        charging_area = Area(
            scenario=scenario,
            name=f"Direct Charging Area for {vehicle_type.name_short}",
            depot=depot,
            area_type=AreaType.DIRECT_ONESIDE,
            capacity=CAPACITY,
        )
        session.add(charging_area)
        charging_area.vehicle_type = vehicle_type

        cleaning_area.processes.append(clean)
        arrival_area.processes.append(standby_arrival)
        charging_area.processes.append(charging)
        charging_area.processes.append(standby_departure)

    assocs = [
        AssocPlanProcess(
            scenario=scenario, process=standby_arrival, plan=plan, ordinal=0
        ),
        AssocPlanProcess(scenario=scenario, process=clean, plan=plan, ordinal=1),
        AssocPlanProcess(scenario=scenario, process=charging, plan=plan, ordinal=2),
        AssocPlanProcess(
            scenario=scenario, process=standby_departure, plan=plan, ordinal=3
        ),
    ]
    session.add_all(assocs)
    session.flush()


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
        add_simple_depot(scenario, session)
        for vehicle_type in scenario.vehicle_types:
            vehicle_type.consumption = 1

        simulation_host = _init_simulation(
            scenario=scenario,
            simple_consumption_simulation=True,
            repetition_period=timedelta(days=7),
        )

        depot_evaluation = _run_simulation(simulation_host)

        vehicle_counts = depot_evaluation.nvehicles_used_calculation()
        simulation_host = _init_simulation(
            scenario=scenario,
            simple_consumption_simulation=True,
            vehicle_count_dict=vehicle_counts,
        )
        depot_evaluation = _run_simulation(simulation_host)

        os.makedirs(os.path.join("output", scenario.name), exist_ok=True)
        depot_evaluation.path_results = os.path.join("output", scenario.name)

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

        # Delete all vehicles and events, also disconnect the vehicles from the rotations
        rotation_q = session.query(Rotation).filter(Rotation.scenario_id == scenario.id)
        rotation_q.update({"vehicle_id": None})
        session.query(Event).filter(Event.scenario_id == scenario.id).delete()
        session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).delete()
        _add_evaluation_to_database(scenario.id, depot_evaluation, session)
        session.commit()
