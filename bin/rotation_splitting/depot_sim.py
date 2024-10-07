import argparse
import os
import warnings
from datetime import timedelta

from eflips.model import *
from eflips.model import ConsistencyWarning
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from eflips.depot.api import (
    add_evaluation_to_database,
    delete_depots,
    init_simulation,
    run_simulation,
    generate_depot_layout,
    simple_consumption_simulation,
    apply_even_smart_charging,
)

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
    parser.add_argument(
        "--visualize",
        action="store_true",
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url)
    with Session(engine) as session:
        scenario = session.query(Scenario).filter(Scenario.id == args.scenario_id).one()
        assert isinstance(scenario, Scenario)

        rotation_q = session.query(Rotation).filter(Rotation.scenario_id == scenario.id)
        rotation_q.update({"vehicle_id": None})
        session.query(Event).filter(Event.scenario_id == scenario.id).delete()
        session.query(Vehicle).filter(Vehicle.scenario_id == scenario.id).delete()

        delete_depots(scenario, session)

        for vehicle_type in scenario.vehicle_types:
            if vehicle_type.name_short == 'EN':
                vehicle_type.consumption = 1.35
            elif vehicle_type.name_short in ('DD', 'GN'):
                vehicle_type.consumption = 2.2

        warnings.simplefilter("ignore", category=ConsistencyWarning)
        simple_consumption_simulation(scenario=scenario, initialize_vehicles=True)

        generate_depot_layout(
            scenario=scenario, charging_power=300, delete_existing_depot=True
        )

        simulation_host = init_simulation(
            scenario=scenario,
            session=session,
            repetition_period=timedelta(days=7),
        )
        depot_evaluations = run_simulation(simulation_host)

        add_evaluation_to_database(scenario, depot_evaluations, session)

        apply_even_smart_charging(scenario)

        simple_consumption_simulation(scenario=scenario, initialize_vehicles=False)

        session.commit()

        if args.visualize:
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
                # The visualization functions are now available. You can use them to visualize the results.
                # For example, to visualize the departure and arrival SoC, you can use the following code:
                OUTPUT_DIR = os.path.join("../output", scenario.name)
                os.makedirs(OUTPUT_DIR, exist_ok=True)
                for depot in scenario.depots:
                    DEPOT_NAME = depot.station.name
                    DEPOT_OUTPUT_DIR = os.path.join(OUTPUT_DIR, DEPOT_NAME)
                    os.makedirs(DEPOT_OUTPUT_DIR, exist_ok=True)

                    # Find all the rotations that use the depot
                    rotations = (
                        session.query(Rotation)
                        .filter(Rotation.scenario_id == scenario.id)
                        .options(
                            sqlalchemy.orm.joinedload(Rotation.trips).joinedload(Trip.route)
                        )
                    )
                    rotation_ids = set()
                    for rotation in rotations:
                        if rotation.trips[0].route.departure_station_id == depot.station.id:
                            rotation_ids.add(rotation.id)
                    rotation_ids = list(rotation_ids)

                    rotation_info = eflips.eval.input.prepare.rotation_info(
                        scenario_id=scenario.id, session=session, rotation_ids=rotation_ids
                    )
                    fig = eflips.eval.input.visualize.rotation_info(rotation_info)
                    fig.update_layout(title=f"Rotation information for {DEPOT_NAME}")
                    fig.write_html(os.path.join(DEPOT_OUTPUT_DIR, "rotation_info.html"))
                    fig.show()

                    # Visualize the load of the depot
                    areas = session.query(Area).filter(Area.depot_id == depot.id).all()
                    area_ids = [area.id for area in areas]
                    df = eflips.eval.output.prepare.power_and_occupancy(area_ids, session)
                    fig = eflips.eval.output.visualize.power_and_occupancy(df)
                    fig.update_layout(title=f"Power and occupancy for {DEPOT_NAME}")
                    fig.write_html(
                        os.path.join(DEPOT_OUTPUT_DIR, "power_and_occupancy.html")
                    )
                    fig.show()

                    # Visualize a timeline for what happens in the depot
                    vehicles = (
                        session.query(Vehicle)
                        .join(Event)
                        .join(Area)
                        .filter(Area.depot_id == depot.id)
                        .all()
                    )
                    vehicle_ids = [vehicle.id for vehicle in vehicles]
                    df = eflips.eval.output.prepare.depot_event(
                        scenario.id, session, vehicle_ids
                    )
                    for color_scheme in "event_type", "soc", "location":
                        fig = eflips.eval.output.visualize.depot_event(
                            df, color_scheme=color_scheme
                        )
                        fig.update_layout(
                            title=f"Depot events for {DEPOT_NAME}, color scheme: {color_scheme}"
                        )
                        fig.write_html(
                            os.path.join(
                                DEPOT_OUTPUT_DIR, f"depot_event_{color_scheme}.html"
                            )
                        )
                        fig.show()

                    # Visualize each vehicle's SoC over time
                    # Here, we don't show it, since it's a lot of plots
                    VEHICLE_OUTPUT_DIR = os.path.join(DEPOT_OUTPUT_DIR, "vehicles")
                    os.makedirs(VEHICLE_OUTPUT_DIR, exist_ok=True)
                    for vehicle in vehicles:
                        df, descriptions = eflips.eval.output.prepare.vehicle_soc(
                            vehicle.id, session
                        )
                        fig = eflips.eval.output.visualize.vehicle_soc(df, descriptions)
                        fig.update_layout(title=f"Vehicle {vehicle.id} SoC over time")
                        fig.write_html(
                            os.path.join(
                                VEHICLE_OUTPUT_DIR, f"vehicle_{vehicle.id}_soc.html"
                            )
                        )
                        # fig.show()
