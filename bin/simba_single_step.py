import os
import warnings
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ds_wrapper import DjangoSimbaWrapper

from eflips.depot.api import (
    delete_depots,
    simple_consumption_simulation,
    generate_depot_layout,
    init_simulation,
    run_simulation,
    add_evaluation_to_database,
    apply_even_smart_charging,
)
from eflips.model import Rotation, Scenario, Event, Vehicle, ConsistencyWarning

if __name__ == "__main__":
    engine = create_engine(os.environ.get("DATABASE_URL"))
    session = Session(engine)
    scenario_id = 8

    with session:
        # Run single step electrification once, one station will be electrified as long as there are rotations with
        # negative SOC
        session.commit()
        ds_wrapper = DjangoSimbaWrapper(os.environ["DATABASE_URL"])
        ds_wrapper.single_step_electrification(scenario_id)
        session.commit()
        session.expire_all()

        # Run eflips-depot to get rotations assigned. Here we use the consumption simulation of eflips-depot

        scenario = session.query(Scenario).filter(Scenario.id == scenario_id).first()
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
        generate_depot_layout(
            scenario=scenario, charging_power=150, delete_existing_depot=True
        )

        ##### Step 3: Run the simulation
        # This can be done using eflips.api.run_simulation. Here, we use the three steps of
        # eflips.api.init_simulation, eflips.api.run_simulation, and eflips.api.add_evaluation_to_database
        # in order to show what happens "under the hood".

        simulation_host = init_simulation(
            scenario=scenario,
            session=session,
            repetition_period=timedelta(days=7),
        )
        depot_evaluations = run_simulation(simulation_host)

        add_evaluation_to_database(scenario, depot_evaluations, session)

        ##### Step 3.5: Apply even smart charging
        # This step is optional. It can be used to apply even smart charging to the vehicles, reducing the peak power
        # consumption. This is done by shifting the charging times of the vehicles. The method is called
        # apply_even_smart_charging and is part of the eflips.depot.api module.
        apply_even_smart_charging(scenario)

        #### Step 4: Consumption simulation, a second time
        # The depot simulation merges vehicles (e.g. one vehicle travels only monday, one only wednesday – they
        # can be the same vehicle). Therefore, the driving events for the vehicles are deleted and the vehicles
        # are re-initialized. In order to have consumption values for the vehicles, we need to run the consumption
        # simulation again. This time, we do not need to initialize the vehicles, because they are already initialized.
        simple_consumption_simulation(scenario=scenario, initialize_vehicles=False)

        # The simulation is now complete. The results are stored in the database and can be accessed using the
        session.commit()

        print("Simulation complete.")
