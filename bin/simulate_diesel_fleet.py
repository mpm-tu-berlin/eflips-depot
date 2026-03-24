#! /usr/bin/env python3
import argparse
import os
import warnings

from eflips.model import *
from eflips.model import create_engine
from sqlalchemy.orm import Session

from eflips.depot.api import (
    simple_consumption_simulation,
    simulate_scenario,
    delete_depots,
    generate_depot_layout,
    create_diesel_vehicle_type_copies,
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

    engine = create_engine(args.database_url, echo=False)
    with Session(engine) as session:
        orig_scenario = (
            session.query(Scenario).filter(Scenario.id == args.scenario_id).one()
        )

        diesel_scenario = orig_scenario.clone(session)

        # Delete simulation results from the clone (events, vehicles, depot)

        session.query(Rotation).filter(
            Rotation.scenario_id == diesel_scenario.id
        ).update({"vehicle_id": None})
        session.query(Event).filter(
            Event.scenario_id == diesel_scenario.id
        ).delete()
        session.query(Vehicle).filter(
            Vehicle.scenario_id == diesel_scenario.id
        ).delete()

        delete_depots(diesel_scenario, session)

        # Ensure all vehicle types are marked as electric before creating diesel copies

        for vt in (
            session.query(VehicleType)
            .filter(VehicleType.scenario_id == diesel_scenario.id)
            .all()
        ):
            vt.energy_source = EnergySource.BATTERY_ELECTRIC

        # Convert all rotations to diesel

        all_rotations = (
            session.query(Rotation)
            .filter(Rotation.scenario_id == diesel_scenario.id)
            .all()
        )

        selected_vehicle_types = {r.vehicle_type_id for r in all_rotations}
        vehicle_type_mapping = create_diesel_vehicle_type_copies(
            selected_vehicle_types, diesel_scenario, session
        )

        for rotation in all_rotations:
            rotation.vehicle_type_id = vehicle_type_mapping[rotation.vehicle_type_id]

        session.flush()
        session.expire_all()

        # Generate the depot layout. Refueling processes and areas are created for diesel vehicles.

        generate_depot_layout(scenario=diesel_scenario, delete_existing_depot=True)

        # Consumption simulation generates driving events with soc_start = soc_end = 1.0 for diesel vehicles.
        simple_consumption_simulation(scenario=diesel_scenario, initialize_vehicles=True)

        simulate_scenario(diesel_scenario)

        simple_consumption_simulation(scenario=diesel_scenario, initialize_vehicles=False)

        session.commit()
        #
        print("Simulation complete.")

        ##### Optional visualization of the results
        # The results can be visualized using the eflips.eval package.
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
            OUTPUT_DIR = os.path.join("output", diesel_scenario.name)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            for depot in diesel_scenario.depots:
                DEPOT_NAME = depot.station.name
                DEPOT_OUTPUT_DIR = os.path.join(OUTPUT_DIR, DEPOT_NAME)
                os.makedirs(DEPOT_OUTPUT_DIR, exist_ok=True)

                # Find all the rotations that use the depot
                rotations = (
                    session.query(Rotation)
                    .filter(Rotation.scenario_id == diesel_scenario.id)
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
                    scenario_id=diesel_scenario.id,
                    session=session,
                    rotation_ids=rotation_ids,
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
                    diesel_scenario.id, session, vehicle_ids
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
