import copy
import os
from datetime import datetime

import eflips
import pytest
import pytz

from depot import Timetable
from depot.api import VehicleType, VehicleSchedule
from depot.api.input import Process, Area, Depot, Plan, AreaType, ProcessType
from eflips.depot.simple_vehicle import VehicleType as EflipsVehicleType


class TestDepot:
    @pytest.fixture
    def depot(self):
        # A depot with a representative set of areas
        # Create an arrival cleaning process
        arrival_cleaning = Process(
            id=1,
            name="Arrival Cleaning",
            dispatchable=False,
            areas=[],  # Connect the areas later
            duration=240,
            electric_power=None,
        )

        arrival_area = Area(
            id=1,
            name="Arrival Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=None,  # we connect the depot later
            available_processes=[arrival_cleaning],
            vehicle_classes=None,
            capacity=6,
        )

        # Connect the areas and processes
        arrival_cleaning.areas = [arrival_area]

        # Create a charging process
        charging = Process(
            id=2,
            name="Charging",
            dispatchable=False,
            areas=[],  # Connect the areas later
            duration=None,
            electric_power=150,
        )

        # And a pre-conditioning process
        preconditioning = Process(
            id=3,
            name="Pre-conditioning",
            dispatchable=False,
            areas=[],  # Connect the areas later
            duration=30 * 60,
            electric_power=20,
        )

        # And a standby pre-departure process
        standby_pre_departure = Process(
            id=4,
            name="Standby Pre-departure",
            dispatchable=True,
            areas=[],  # Connect the areas later
            duration=None,
            electric_power=None,
        )

        # Create a line charging area
        line_charging_area = Area(
            id=2,
            name="Line Charging Area",
            type=AreaType.LINE,
            depot=None,  # we connect the depot later
            available_processes=[charging, preconditioning, standby_pre_departure],
            vehicle_classes=None,
            capacity=24,
            row_count=4,
        )

        # Create a direct charging area
        direct_charging_area = Area(
            id=3,
            name="Direct Charging Area",
            type=AreaType.DIRECT_TWOSIDE,
            depot=None,  # we connect the depot later
            available_processes=[charging, preconditioning, standby_pre_departure],
            vehicle_classes=None,
            capacity=6,
        )

        # Create another area that just does standby pre-departure
        standby_pre_departure_area = Area(
            id=4,
            name="Standby Pre-departure Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=None,  # we connect the depot later
            available_processes=[standby_pre_departure],
            vehicle_classes=None,
            capacity=6,
        )

        # Connect the areas and processes
        charging.areas = [line_charging_area, direct_charging_area]
        preconditioning.areas = [line_charging_area, direct_charging_area]
        standby_pre_departure.areas = [
            line_charging_area,
            direct_charging_area,
            standby_pre_departure_area,
        ]

        # Create a plan
        plan = Plan(
            id=1,
            processes=[
                arrival_cleaning,
                charging,
                preconditioning,
                standby_pre_departure,
            ],
        )

        # Create a depot
        depot = Depot(
            id=1,
            name="Test Depot",
            areas=[
                arrival_area,
                line_charging_area,
                direct_charging_area,
                standby_pre_departure_area,
            ],
            plan=plan,
        )

        # Connect the areas and depot
        arrival_area.depot = depot
        line_charging_area.depot = depot
        direct_charging_area.depot = depot
        standby_pre_departure_area.depot = depot

        return depot

    def test_create_depot(self, depot):
        assert isinstance(depot, Depot)
        depot.validate()

    def test_depot_invalid(self, depot):
        """Test for various invalid depot configurations."""

        new_depot = copy.deepcopy(depot)

        # Add a process to the plan (that is not available in any area)
        new_process = Process(
            id=5,
            name="Test Process",
            dispatchable=True,
            areas=[],  # Connect the areas later
            duration=240,
            electric_power=None,
        )
        new_depot.plan.processes.append(new_process)
        with pytest.raises(AssertionError):
            new_depot.validate()

        # Make the last proces of the plan not dispatchable
        new_depot = copy.deepcopy(depot)
        new_depot.plan.processes[-1].dispatchable = False
        with pytest.raises(AssertionError):
            new_depot.validate()

        # Add an area that does not have any processes
        new_depot = copy.deepcopy(depot)
        new_area = Area(
            id=5,
            name="Test Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=new_depot,
            capacity=6,
            available_processes=[],
        )
        new_depot.areas.append(new_area)
        with pytest.raises(AssertionError):
            new_depot.validate()

        # Add an area to the depot that is not in depot.areas
        new_depot = copy.deepcopy(depot)
        new_area = Area(
            id=5,
            name="Test Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=new_depot,
            available_processes=[new_depot.plan.processes[0]],
            vehicle_classes=None,
            capacity=6,
        )
        new_depot.areas.append(new_area)
        with pytest.raises(AssertionError):
            new_depot.validate()

        # Add a process to an area that is not part of the plan
        new_depot = copy.deepcopy(depot)
        area = new_depot.areas[0]
        new_process = Process(
            id=5,
            name="Test Process",
            dispatchable=True,
            areas=[area],
            duration=240,
            electric_power=None,
        )
        area.available_processes.append(new_process)
        with pytest.raises(AssertionError):
            new_depot.validate()

        # Add a process to an area and don't add the area to the process
        new_depot = copy.deepcopy(depot)
        area = new_depot.areas[0]
        process = new_depot.plan.processes[-1]
        assert process not in area.available_processes
        area.available_processes.append(process)
        with pytest.raises(AssertionError):
            new_depot.validate()


class TestArea:
    """Test the "Area" class."""

    @pytest.fixture
    def area(self):
        return Area(
            id=15,
            name="Test Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=None,
            available_processes=[],
            vehicle_classes=[],
            capacity=1,
        )

    def test_create_area(self, area):
        assert isinstance(area, Area)

    def test_area_post_init(self):
        """Test the various checks in the post_init method."""

        # Try to create an area with 0 capacity for the different area types
        for t in AreaType:
            with pytest.raises(AssertionError):
                area = Area(
                    id=15,
                    name="Test Area",
                    type=t,
                    depot=None,
                    available_processes=[],
                    vehicle_classes=[],
                    capacity=0,
                )

        # Try to create a DIRECT_TWO_SIDE area with an odd capacity
        with pytest.raises(AssertionError):
            area = Area(
                id=15,
                name="Test Area",
                type=AreaType.DIRECT_TWOSIDE,
                depot=None,
                available_processes=[],
                vehicle_classes=[],
                capacity=3,
            )

        # Try to create a DIRECT_TWO_SIDE area with an even capacity
        area = Area(
            id=15,
            name="Test Area",
            type=AreaType.DIRECT_TWOSIDE,
            depot=None,
            available_processes=[],
            vehicle_classes=[],
            capacity=4,
        )

        # Create a LINE Area with a row_count of 0
        with pytest.raises(AssertionError):
            area = Area(
                id=15,
                name="Test Area",
                type=AreaType.LINE,
                depot=None,
                available_processes=[],
                vehicle_classes=[],
                capacity=1,
                row_count=0,
            )

        # And a row_count that does not evenly divide the capacity
        with pytest.raises(AssertionError):
            area = Area(
                id=15,
                name="Test Area",
                type=AreaType.LINE,
                depot=None,
                available_processes=[],
                vehicle_classes=[],
                capacity=4,
                row_count=3,
            )

        # And a row_count that does evenly divide the capacity
        area = Area(
            id=15,
            name="Test Area",
            type=AreaType.LINE,
            depot=None,
            available_processes=[],
            vehicle_classes=[],
            capacity=12,
            row_count=3,
        )


class TestProcessAndPlan:
    """Test the process and plan classes."""

    @pytest.fixture
    def area(self):
        return Area(
            id=15,
            name="Test Area",
            type=AreaType.DIRECT_ONESIDE,
            depot=None,
            available_processes=[],
            vehicle_classes=[],
            capacity=1,
        )

    @pytest.fixture
    def process(self, area):
        process = Process(id=42, name="Test Process", dispatchable=False, areas=[area])
        process.areas[0].processes = [process]
        return process

    def test_create_process(self, process):
        assert isinstance(process, Process)

    def test_process_type(self, process):
        assert isinstance(process, Process)

        process.duration = 123
        process.electric_power = None
        assert process.type == ProcessType.SERVICE

        process.duration = None
        process.electric_power = 123
        assert process.type == ProcessType.CHARGING

        process.duration = 123
        process.electric_power = 123
        assert process.type == ProcessType.PRECONDITION

        process.duration = None
        process.electric_power = None
        assert process.type == ProcessType.STANDBY

        process.duration = None
        process.electric_power = None
        process.dispatchable = True
        assert process.type == ProcessType.STANDBY_DEPARTURE

    def test_plan(self, process):
        """The plan class needs just one test."""
        plan = Plan(id=42, processes=[process])
        assert isinstance(plan, Plan)
