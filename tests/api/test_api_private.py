from datetime import datetime, timedelta, timezone

import pytest
import simpy
from eflips.model import Event, EventType, Rotation, Vehicle, VehicleType

from api.test_api import TestHelpers
from eflips.depot import Depotinput, SimpleTrip, SimulationHost
from eflips.depot.api.private.depot import depot_to_template
from eflips.depot.api.private.util import (
    vehicle_type_to_global_constants_dict,
    VehicleSchedule,
    check_depot_validity,
)
from eflips.depot.standalone import Timetable as EflipsTimeTable


class TestVehicleType(TestHelpers):
    def test_vehicle_type_to_gc(self, session, scenario):
        # Add a vehicle type
        vehicle_type = VehicleType(
            scenario=scenario,
            name="Test Vehicle Type",
            battery_capacity=100,
            charging_curve=[[0, 150], [1, 150]],
            opportunity_charging_capable=True,
        )
        session.add(vehicle_type)
        session.commit()

        eflips_dict = vehicle_type_to_global_constants_dict(vehicle_type)
        assert isinstance(eflips_dict, dict)


class TestVehicleSchedule(TestHelpers):
    @pytest.fixture
    def eflips_vehicle_schedule(self, session, full_scenario):
        # Set the vehicle types consumption values
        for vt in full_scenario.vehicle_types:
            vt.consumption = 1.0

        # Create an eflips vehicle schedule from a rotation
        rotation = full_scenario.rotations[0]

        vehicle_schedule = VehicleSchedule.from_rotation(
            rotation, full_scenario, session
        )
        return vehicle_schedule

    def test_vehicle_schedule_no_events_fail(self, session, full_scenario):
        # Delete old events, rotation-id-assignments and vehicle-id-assignments
        session.query(Event).filter(Event.scenario_id == full_scenario.id).delete()
        session.query(Rotation).filter(Rotation.scenario_id == full_scenario.id).update(
            {"vehicle_id": None}
        )
        session.query(Vehicle).filter(Vehicle.scenario_id == full_scenario.id).delete()

        # Unset the vehicle types consumption values
        for vt in full_scenario.vehicle_types:
            vt.consumption = None

        session.commit()

        # Create an eflips vehicle schedule from a rotation
        rotation = full_scenario.rotations[0]

        with pytest.raises(ValueError):
            vehicle_schedule = VehicleSchedule.from_rotation(
                rotation, full_scenario, session
            )

    def test_vehicle_schedule_events(self, session, full_scenario):
        # Delete all events
        session.query(Event).delete()

        # Set the vehicle types consumption values
        for vt in full_scenario.vehicle_types:
            vt.consumption = 1.0

        # Create an eflips vehicle schedule from a rotation
        rotation = full_scenario.rotations[0]

        # Create driving events for all trips in the rotation
        trip_count = len(rotation.trips)
        soc_per_trip = 1 / trip_count
        current_soc = 1
        for trip in rotation.trips:
            session.add(
                Event(
                    scenario=full_scenario,
                    event_type=EventType.DRIVING,
                    vehicle_type=rotation.vehicle_type,
                    trip=trip,
                    time_start=trip.departure_time,
                    time_end=trip.arrival_time,
                    soc_start=current_soc,
                    soc_end=current_soc - soc_per_trip,
                )
            )
            current_soc -= soc_per_trip

        session.commit()

        vehicle_schedule = VehicleSchedule.from_rotation(
            rotation, full_scenario, session
        )
        assert vehicle_schedule is not None
        assert isinstance(vehicle_schedule, VehicleSchedule)

    def test_to_simpletrip(self, eflips_vehicle_schedule):
        env = simpy.Environment()
        simulation_start_time = datetime.min.replace(tzinfo=timezone.utc)

        simple_trip = eflips_vehicle_schedule._to_simple_trip(
            simulation_start_time, env
        )
        assert simple_trip is not None
        assert isinstance(simple_trip, SimpleTrip)

    def test_repeat(self, eflips_vehicle_schedule):
        for td in (
            timedelta(days=1),
            timedelta(days=-2),
        ):
            new_schedule = eflips_vehicle_schedule.repeat(td)
            assert new_schedule is not None

            # It should be a new object
            assert new_schedule is not eflips_vehicle_schedule

            # It should be the same, except for the times and the _is_copy flag
            assert new_schedule.id == eflips_vehicle_schedule.id
            assert new_schedule.vehicle_type == eflips_vehicle_schedule.vehicle_type
            assert new_schedule.departure == eflips_vehicle_schedule.departure + td
            assert new_schedule.arrival == eflips_vehicle_schedule.arrival + td
            assert new_schedule.arrival_soc == eflips_vehicle_schedule.arrival_soc
            assert new_schedule.departure_soc == eflips_vehicle_schedule.departure_soc
            assert new_schedule.minimal_soc == eflips_vehicle_schedule.minimal_soc
            assert (
                new_schedule.opportunity_charging
                == eflips_vehicle_schedule.opportunity_charging
            )
            assert new_schedule._is_copy == True

    def test_to_timetable(self, session, full_scenario):
        # Set the vehicle types consumption values
        for vt in full_scenario.vehicle_types:
            vt.consumption = 1.0

        vehicle_schedules = []
        for rotation in full_scenario.rotations:
            vehicle_schedules.append(
                VehicleSchedule.from_rotation(rotation, full_scenario, session)
            )

        env = simpy.Environment()
        simulation_start_time = datetime.min.replace(tzinfo=timezone.utc)
        timetable = VehicleSchedule._to_timetable(
            vehicle_schedules, env, simulation_start_time
        )

        assert timetable is not None
        assert isinstance(timetable, EflipsTimeTable)


class TestDepot(TestHelpers):
    def test_depot_template_gen(self, session, full_scenario):
        depot = full_scenario.depots[0]

        # Create a template
        template = depot_to_template(depot)
        assert template is not None
        assert isinstance(template, dict)

        # Check that the template is valid by creating a new depot from it
        eflips_depot = Depotinput(filename_template=template, show_gui=False)
        simulation_host = SimulationHost([eflips_depot], print_timestamps=False)

    def test_check_depot_validity(self, session, full_scenario):
        depot = full_scenario.depots[0]

        # Check the depot for validity
        check_depot_validity(depot)

        areas = depot.areas
        for area in areas:
            if area.vehicle_type is None:
                area.vehicle_type_id = full_scenario.vehicle_types[0].id

        session.flush()

        with pytest.raises(AssertionError):
            check_depot_validity(depot)
        session.rollback()

        plan = depot.default_plan
        last_process = plan.processes[-1]
        last_process.duration = timedelta(minutes=3)
        session.flush()

        with pytest.raises(AssertionError):
            check_depot_validity(depot)
        session.rollback()

        first_process = plan.processes[0]
        first_process.duration = None
        session.flush()

        with pytest.raises(AssertionError):
            check_depot_validity(depot)
        session.rollback()
