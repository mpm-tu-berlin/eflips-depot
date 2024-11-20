import math

import numpy as np
import pytest
from eflips.model import (
    VehicleType,
    AreaType,
    Area,
    AssocAreaProcess,
    AssocPlanProcess,
    Plan,
    Depot,
    Station,
    Process,
    Vehicle,
    Event,
    Rotation,
)

from api.test_api import TestHelpers
from eflips.depot.api import simple_consumption_simulation, simulate_scenario
from eflips.depot.api.private.depot import (
    area_needed_for_vehicle_parking,
    generate_depot,
)


class TestAreaCalc(TestHelpers):
    def test_area_calc_line(self, session, full_scenario):
        vt = session.query(VehicleType).first()
        vt.length = 12
        vt.width = 2.35

        # The area should remain equal for +1…+6 vehicles, as one line is added for each 6 vehicles.
        area_size = area_needed_for_vehicle_parking(
            vt, area_type=AreaType.LINE, count=13
        )
        for i in range(14, 19):
            assert (
                area_needed_for_vehicle_parking(vt, area_type=AreaType.LINE, count=i)
                == area_size
            )

    def test_are_calc_direct_one(self, session, full_scenario):
        # We test by comparing the results with danial's formula, which should give the same results for zero spacing.
        vt = session.query(VehicleType).first()
        vt.length = 12
        vt.width = 2.35

        counts = range(1, 10)
        spacing = 0
        angle = 45

        for count in counts:
            width_danial = (
                math.sin(math.radians(angle)) * vt.width
                + math.sin(math.radians(angle)) * vt.length
            )
            length_danial = (
                math.sin(math.radians(angle)) * vt.length
                + math.sin(math.radians(angle)) * vt.width
                + (count - 1) * (2 * math.cos(math.radians(angle)) * vt.width)
            )
            area_danial = width_danial * length_danial

            area = area_needed_for_vehicle_parking(
                vt,
                area_type=AreaType.DIRECT_ONESIDE,
                count=count,
                spacing=spacing,
                angle=angle,
            )
            assert np.isclose(area, area_danial, rtol=1e-5)


class TestGenerateDepot(TestHelpers):
    @pytest.fixture
    def no_depot_scenario(self, session, full_scenario):
        session.query(AssocAreaProcess).delete()
        session.query(Area).delete()
        session.query(AssocPlanProcess).delete()
        session.query(Process).delete()
        session.query(Depot).delete()
        session.query(Plan).delete()

        session.query(Rotation).update({"vehicle_id": None})
        session.query(Event).delete()
        session.query(Vehicle).delete()

        session.flush()
        return full_scenario

    def test_generate_depot_depot_invalid_line_count(self, session, no_depot_scenario):
        # Here, we generate a depot with 11 line, 1 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 11

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        with pytest.raises(ValueError):
            generate_depot(
                capacity_of_areas=vt_capacity,
                station=station_to_create_at,
                scenario=no_depot_scenario,
                session=session,
            )

    def test_generate_depot_depot_invalid_direct_twoside_count(
        self, session, no_depot_scenario
    ):
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 3
                else:
                    vt_capacity[vt][area_type] = 0

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        with pytest.raises(ValueError):
            generate_depot(
                capacity_of_areas=vt_capacity,
                station=station_to_create_at,
                scenario=no_depot_scenario,
                session=session,
            )

    def test_generate_depot(self, session, no_depot_scenario):
        # Here, we generate a depot with 12 line, 12 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 12

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        generate_depot(
            capacity_of_areas=vt_capacity,
            station=station_to_create_at,
            scenario=no_depot_scenario,
            session=session,
        )

        # What should happen is that now, a depot exists in the database
        depot = session.query(Depot).one()
        # The depot should have six charging areas (two line, one direct for each vehicle type), two shunting areas,
        # one cleaning area and one waiting area.
        assert len(depot.areas) == 10

        # The plan should have
        # 1) shunting
        # 2) cleaning
        # 3) shunting
        # 4) charging
        # 5) standby pre_departure
        assert len(depot.default_plan.processes) == 5

        # Simulate the depot
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=True)
        simulate_scenario(no_depot_scenario)
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=False)

    def test_generate_depot_no_shunting(self, session, no_depot_scenario):
        # Here, we generate a depot with 12 line, 12 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 12

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        generate_depot(
            capacity_of_areas=vt_capacity,
            station=station_to_create_at,
            scenario=no_depot_scenario,
            shunting_duration=None,
            session=session,
        )

        # What should happen is that now, a depot exists in the database
        depot = session.query(Depot).one()
        # The depot should have six charging areas (two line, one direct for each vehicle type), ~~two shunting areas~~,
        # one cleaning area and one waiting area.
        assert len(depot.areas) == 8

        # The plan should have
        # 1) ~~shunting~~
        # 2) cleaning
        # 3) ~~shunting~~
        # 4) charging
        # 5) standby pre_departure
        assert len(depot.default_plan.processes) == 3

        # Simulate the depot
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=True)
        simulate_scenario(no_depot_scenario)
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=False)

    def test_generate_depot_no_cleaning(self, session, no_depot_scenario):
        # Here, we generate a depot with 12 line, 12 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 12

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        generate_depot(
            capacity_of_areas=vt_capacity,
            station=station_to_create_at,
            scenario=no_depot_scenario,
            cleaning_duration=None,
            session=session,
        )

        # What should happen is that now, a depot exists in the database
        depot = session.query(Depot).one()
        # The depot should have six charging areas (two line, one direct for each vehicle type), two shunting areas,
        # ~~one cleaning area~~ and one waiting area.
        assert len(depot.areas) == 9

        # The plan should have
        # 1) shunting
        # 2) ~~cleaning~~
        # 3) shunting
        # 4) charging
        # 5) standby pre_departure
        assert len(depot.default_plan.processes) == 4

        # Simulate the depot
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=True)
        simulate_scenario(no_depot_scenario)
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=False)

    def test_generate_depot_no_shunting_no_cleaning(self, session, no_depot_scenario):
        # Here, we generate a depot with 12 line, 12 direct_oneside and 0 direct_twoside areas for each vehicle type.
        vts = session.query(VehicleType).all()
        vt_capacity = dict()
        for vt in vts:
            vt_capacity[vt] = dict()
            for area_type in (
                AreaType.LINE,
                AreaType.DIRECT_ONESIDE,
                AreaType.DIRECT_TWOSIDE,
            ):
                if area_type == AreaType.DIRECT_TWOSIDE:
                    vt_capacity[vt][area_type] = 0
                else:
                    vt_capacity[vt][area_type] = 12

        station_to_create_at = (
            session.query(Station).filter(Station.name_short == "TS1").one()
        )
        generate_depot(
            capacity_of_areas=vt_capacity,
            station=station_to_create_at,
            scenario=no_depot_scenario,
            shunting_duration=None,
            cleaning_duration=None,
            session=session,
        )

        # What should happen is that now, a depot exists in the database
        depot = session.query(Depot).one()
        # The depot should have six charging areas (two line, one direct for each vehicle type), ~~two shunting areas~~,
        # ~~one cleaning area~~ and one waiting area.
        assert len(depot.areas) == 7

        # The plan should have
        # 1) ~~shunting~~
        # 2) ~~cleaning~~
        # 3) ~~shunting~~
        # 4) charging
        # 5) standby pre_departure
        assert len(depot.default_plan.processes) == 2

        # Simulate the depot
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=True)
        simulate_scenario(no_depot_scenario)
        simple_consumption_simulation(no_depot_scenario, initialize_vehicles=False)
