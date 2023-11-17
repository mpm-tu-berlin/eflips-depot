import copy

import pytest

import eflips
from eflips.depot.api.enums import ProcessType, AreaType
from eflips.depot.api.input import Process, Area, Depot, Plan
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
            duration=4800,
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
            electric_power=20.0,
        )

        # And a pre-conditioning process
        preconditioning = Process(
            id=3,
            name="Pre-conditioning",
            dispatchable=False,
            areas=[],  # Connect the areas later
            duration=30 * 60,
            electric_power=20.0,
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

    def test_to_template(self, depot):
        template_dict = depot._to_template()
        assert isinstance(template_dict, dict)
        assert isinstance(template_dict["resources"], dict)

        # Check resources
        # check if the amount of interfaces equals to the sum of area capacity with process CHARGING
        total_interfaces = 0
        for area in depot.areas:
            for process in area.available_processes:
                if process.type == ProcessType.CHARGING:
                    total_interfaces += area.capacity

        # Check if template_dict["resource"]["charging_interfaces"] has the right data format

        for k, v in template_dict["resources"].items():
            if v["typename"] == "DepotChargingInterface":
                assert isinstance(v["max_power"], float) and v["max_power"] >= 0.0

        # Check areas
        # if the amount of area dictionaries equals to number of areas in depot
        assert len(template_dict["areas"]) == len(depot.areas)

        # If the dictionary has the right data format. See template_creation.py for reference
        for area_name, area_dict in template_dict["areas"].items():
            assert (
                area_dict["typename"] == "DirectArea"
                or area_dict["typename"] == "LineArea"
            )
            assert isinstance(area_dict["capacity"], int) and area_dict["capacity"] > 0
            assert (
                isinstance(area_dict["available_processes"], list)
                and len(area_dict["available_processes"]) > 0
            )
            for p in area_dict["available_processes"]:
                assert isinstance(p, str)

            assert isinstance(area_dict["issink"], bool)
            assert (
                isinstance(area_dict["entry_filter"], list)
                or area_dict["entry_filter"] is None
            )

        # Check groups
        for group_name, group_dict in template_dict["groups"].items():
            assert (
                group_dict["typename"] == "ParkingAreaGroup"
                or group_dict["typename"] == "AreaGroup"
            )
            if group_dict["typename"] == "ParkingAreaGroup":
                assert isinstance(group_dict["parking_strategy_name"], str)

            assert (
                isinstance(group_dict["stores"], list) and len(group_dict["stores"]) > 0
            )

        # Check plans
        assert template_dict["plans"]["default"]["typename"] == "DefaultActivityPlan"
        assert isinstance(template_dict["plans"]["default"]["locations"], list) and len(
            template_dict["plans"]["default"]["locations"]
        ) == len(depot.plan.processes)

        parking_group_name = template_dict["plans"]["default"]["locations"][-1]
        assert (
            template_dict["groups"][parking_group_name]["typename"]
            == "ParkingAreaGroup"
        )
        # TODO check if locations are correct in thie

        # Check processes
        for process_name, process_dict in template_dict["processes"].items():
            assert isinstance(process_dict, dict)
            assert (
                process_dict["typename"] == "Serve"
                or process_dict["typename"] == "Charge"
                or process_dict["typename"] == "Precondition"
                or process_dict["typename"] == "Standby"
                or process_dict["typename"] == "Repair"
                or process_dict["typename"] == "Maintain"
            )

            # TODO: rewrite the asserts of duration till we figure out if a duration is mandatory for Stand

            # assert (isinstance(process_dict["dur"], float) and process_dict["dur"] >= 0.0) or process_dict[
            #     "dur"] is None

    def test_write_process_to_template(self, depot):
        template_dict = depot._to_template()
        assert isinstance(template_dict["processes"], dict)

        for process_name, process_dict in template_dict["processes"].items():
            assert isinstance(process_dict, dict)
            assert (
                process_dict["typename"] == "Serve"
                or process_dict["typename"] == "Charge"
                or process_dict["typename"] == "Precondition"
                or process_dict["typename"] == "Standby"
                or process_dict["typename"] == "Repair"
                or process_dict["typename"] == "Maintain"
            )
            assert isinstance(process_dict["ismandatory"], bool)
            assert isinstance(process_dict["cancellable_for_dispatch"], bool)

            # Test for Serve

            if process_dict["typename"] == "Serve":
                assert isinstance(process_dict["dur"], int) and process_dict["dur"] >= 0

                # assert isinstance(process_dict["vehicle_filter"]["filter_names"], list)
                if "in_period" in process_dict:
                    assert isinstance(process_dict["in_period"], list)

            if process_dict["typename"] == "Precondition":
                assert isinstance(process_dict["dur"], int) and process_dict["dur"] >= 0
                assert isinstance(process_dict["power"], float)

    def test_depot_configuration(self, depot):
        template_dict = depot._to_template()

        # Initialize a SimulationHost from the dictinary from Depot
        simulation_host = eflips.depot.SimulationHost(
            [eflips.depot.Depotinput(filename_template=template_dict, show_gui=False)],
            run_progressbar=True,
            print_timestamps=True,
            tictocname="",
        )

        assert isinstance(simulation_host, eflips.depot.SimulationHost)

        assert isinstance(simulation_host.depot_hosts[0].depot, eflips.depot.Depot)


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

    def test_process_post_init(self):
        "Test checks in __post_init__()"
        with pytest.raises(AssertionError):
            # process with electric_power and duration not a float
            process = Process(
                id=2,
                dispatchable=False,
                areas=None,
                name="precondition",
                electric_power=150,
                duration=-240.0,
            )

            process = Process(
                id=2,
                dispatchable=False,
                areas=None,
                name="precondition",
                electric_power=-150.0,
                duration=240,
            )

    def test_plan(self, process):
        """The plan class needs just one test."""
        plan = Plan(id=42, processes=[process])
        assert isinstance(plan, Plan)
