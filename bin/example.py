#! /usr/bin/env python3
import argparse
import os
import warnings

from eflips.model import *
from eflips.model import ConsistencyWarning
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from eflips.depot.api import (
    add_evaluation_to_database,
    delete_depots,
    init_simulation,
    insert_dummy_standby_departure_events,
    run_simulation,
    generate_realistic_depot_layout,
    simple_consumption_simulation,
    apply_even_smart_charging,
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

        ##### Step 1: Consumption simulation
        # Since we are using simple consumption simulation, we also need to make sure that the vehicle types have
        # a consumption value. This is not necessary if you are using an external consumption simulation.
        for vehicle_type in scenario.vehicle_types:
            vehicle_type.consumption = 1

        # Using simple consumption simulation
        # We suppress the ConsistencyWarning, because it happens a lot with BVG data and is fine
        # It could indicate a problem with the rotations with other data sources
        warnings.simplefilter("ignore", category=ConsistencyWarning)
        simple_consumption_simulation(scenario=scenario, initialize_vehicles=True)

        ##### Step 2: Generate the depot layout
        generate_realistic_depot_layout(
            scenario=scenario, charging_power=300, delete_existing_depot=True
        )

        ##### Step 3: Run the simulation
        # This can be done using eflips.api.run_simulation. Here, we use the three steps of
        # eflips.api.init_simulation, eflips.api.run_simulation, and eflips.api.add_evaluation_to_database
        # in order to show what happens "under the hood".

        simulation_host = init_simulation(
            scenario=scenario,
            session=session,
            repetition_period=None,  # You may want to put a specific timedelta here  if the repetition period is not daily or weekly
        )
        depot_evaluations = run_simulation(simulation_host)

        if args.simulation_core_diagram:
            # We print the old-style plot of the simulation core
            # This might be useful for debugging, as it contains the state of the simulation boefore
            # `add_evaluation_to_database()` is called
            OUTPUT_DIR = os.path.join("output", scenario.name)
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            try:
                for depot in scenario.depots:
                    DEPOT_NAME = depot.station.name
                    DEPOT_OUTPUT_DIR = os.path.join(OUTPUT_DIR, DEPOT_NAME)
                    os.makedirs(DEPOT_OUTPUT_DIR, exist_ok=True)

                    depot_evaluation = depot_evaluations[str(depot.id)]
                    depot_evaluation.path_results = DEPOT_OUTPUT_DIR

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
            except AssertionError as e:
                print(
                    "There are vehicle waiting for entering the depot and the simulation core diagram does not support "
                    "this. Please make the capacity of the first area larger to avoid this issue."
                )

        add_evaluation_to_database(scenario, depot_evaluations, session)
        session.expire_all()

        #### Step 4: Consumption simulation, a second time
        # The depot simulation merges vehicles (e.g. one vehicle travels only monday, one only wednesday – they
        # can be the same vehicle). Therefore, the driving events for the vehicles are deleted and the vehicles
        # are re-initialized. In order to have consumption values for the vehicles, we need to run the consumption
        # simulation again. This time, we do not need to initialize the vehicles, because they are already initialized.
        simple_consumption_simulation(scenario=scenario, initialize_vehicles=False)

        # This is something we need to do due to an unclear reason. The depot simulation sometimes does not
        # generate the correct departure events for the vehicles. Therefore, we insert dummy standby departure events
        for depot in (
            session.query(Depot).filter(Depot.scenario_id == scenario.id).all()
        ):
            insert_dummy_standby_departure_events(depot.id, session)

        ##### Step 5: Apply even smart charging
        # This step is optional. It can be used to apply even smart charging to the vehicles, reducing the peak power
        # consumption. This is done by shifting the charging times of the vehicles. The method is called
        # apply_even_smart_charging and is part of the eflips.depot.api module.
        apply_even_smart_charging(scenario)

        # The simulation is now complete. The results are stored in the database and can be accessed using the
        session.commit()

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
            OUTPUT_DIR = os.path.join("output", scenario.name)
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
