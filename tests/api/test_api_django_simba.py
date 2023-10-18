import dataclasses
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.api.djangosettings")
import django

django.setup()
from django.core import management

management.call_command("migrate")
from datetime import datetime

from depot.api import init_simulation, run_simulation
from depot.api.django_simba.output import to_simba


from api import djangosettings
from depot.api.django_simba.input import VehicleSchedule


import os
import pathlib
import uuid
from decimal import Decimal
from typing import Callable
import json


import pytest

from ebustoolbox.models import Scenario, VehicleClass, Trip, Rotation
from ebustoolbox.models import VehicleType as DjangoSimbaVehicleType

from eflips.depot.api.django_simba.input import VehicleType as EflipsVehicleType

from ebustoolbox.tasks import (
    run_ebus_toolbox,
    get_args,
    get_schedule_from_args,
    stations_to_db,
    vehicles_to_db,
    schedule_to_db,
    add_classes_to_vehicle_types,
)


class TestApiDjangoSimba:
    @pytest.fixture
    def input_data(self):
        # Find the absolute path to the test folder (the folder this file is in)
        absolute_path = os.path.dirname(__file__)
        path_to_sample_simulation = os.path.join(absolute_path, "sample_simBA_input")

        # This is a copy-paste from running the django web app in a debugger
        input_data = {
            "title": "SimBA",
            "preferred_charging_type": "oppb",
            "modes": "sim,report",
            "gc_power_opps": Decimal("100000.0"),
            "gc_power_deps": Decimal("100000.0"),
            "cs_power_opps": Decimal("300"),
            "cs_power_deps_depb": Decimal("150"),
            "cs_power_deps_oppb": Decimal("150"),
            "default_voltage_level": "MV",
            "desired_soc_deps": Decimal("1"),
            "desired_soc_opps": Decimal("1"),
            "min_recharge_deps_oppb": Decimal("1"),
            "min_recharge_deps_depb": Decimal("1"),
            "min_charging_time": Decimal("0"),
            "default_buffer_time_opps": Decimal("0"),
            "input_schedule": os.path.join(
                path_to_sample_simulation, "trips_example.csv"
            ),
            "electrified_stations": os.path.join(
                path_to_sample_simulation, "electrified_stations.json"
            ),
            "vehicle_types": os.path.join(
                path_to_sample_simulation, "vehicle_types.json"
            ),
            "station_data_path": os.path.join(
                path_to_sample_simulation, "all_stations.csv"
            ),
            "outside_temperature_over_day_path": os.path.join(
                path_to_sample_simulation, "default_temp_summer.csv"
            ),
            "level_of_loading_over_day_path": os.path.join(
                path_to_sample_simulation, "default_level_of_loading_over_day.csv"
            ),
            "cost_parameters_file": os.path.join(
                path_to_sample_simulation, "cost_params.json"
            ),
            "strategy": "distributed",
            "interval": Decimal("15"),
            "signal_time_dif": Decimal("10"),
            "days": None,
            "include_price_csv": None,
            "seed": "",
            "cost_calculation": False,
        }

        return input_data

    @pytest.fixture
    def scenario(self, input_data):
        """
        Variant of ebustoolbox.tasks.scenario_to_db that does not use Django forms.
        """
        management.call_command("flush", "--noinput")

        scenario = Scenario.objects.create(name=input_data["title"])
        args = dict(input_data)
        args["mode"] = list(map(lambda s: s.strip(), args["modes"].split(",")))
        # decimal -> float
        for k, v in args.items():
            if type(v) == Decimal:
                args[k] = float(v)
        scenario.options = args

        scenario.save()
        return scenario

    @pytest.fixture(autouse=True)
    def simulation_input(self, scenario):
        """
        Variant of ebustoolbox.tasks.fill_db_with_input_files that does not use Django forms.

        This one (and the ones it depends on) clear the database and fill it with the sample data from the
        sample_simBA_input folder.
        """

        original_args = get_args(scenario)
        simba_schedule, new_args = get_schedule_from_args(original_args)

        stations_to_db(
            scenario.options["station_data_path"],
            scenario.options["electrified_stations"],
            scenario,
        )
        vehicles_to_db(scenario.options["vehicle_types"], scenario)

        schedule_to_db(simba_schedule, scenario)

        add_classes_to_vehicle_types(scenario)

        return scenario, simba_schedule, original_args

    @pytest.fixture
    def eflips_input_path(self, simulation_input) -> pathlib.Path:
        """
        This method calls Django-Simba using the sample files in order to create the input files for eFlips.

        Returns:
            A pathlib.Path object containing the absolute paths to the created files.
        """

        django_scenario, simba_schedule, args = simulation_input

        task_id = str(uuid.uuid4())

        django_scenario.task_id = task_id
        django_scenario.save()
        run_ebus_toolbox(simba_schedule, args, task_id)

        # By API contract, the files are located in settings.BASE_DIR / settings.UPLOAD_PATH / task_id
        path_to_files = (
            pathlib.Path(djangosettings.BASE_DIR) / djangosettings.UPLOAD_PATH / task_id
        )

        # The files are named eflips_input.json and rotation_socs.csv
        eflips_input_path = path_to_files / "report_1" / "eflips_input.json"

        # Do a manual modification of the result file until RLI figures out how to fix it
        with open(eflips_input_path, "r") as f:
            simba_output = json.load(f)

        # TODO: REMOVE THIS LATER. We are modifying the JSON file's contents after loading
        # Once django-simba fixes their #28, we can remove this
        for rotation_id, results in simba_output.items():
            # Make all the "vehicle_type" lists contain only distinct items
            if isinstance(results["vehicle_type"], list):
                results["vehicle_type"] = [results["vehicle_type"][0]]
                results["delta_soc"] = [results["delta_soc"][0]]
                if results["delta_soc"][0] > 1:
                    results["delta_soc"][0] = 1.0

        with open(eflips_input_path, "w") as f:
            json.dump(simba_output, f)

        return eflips_input_path

    def test_database_query(self):
        """Playground for reading data from database"""

        vehicle_type = DjangoSimbaVehicleType.objects.all()[0]
        vehicle_classes = vehicle_type.vehicle_class.all()
        for vehicle_class in vehicle_classes:
            vehicle_type_ids = [v.id for v in vehicle_class.vehicletype_set.all()]

    def test_fill_vehicle_type_from_djangosimba(self):
        """This method tests if a VehicleType object can be correctly created from a DjangoSimbaVehicleType object"""
        vehicle_from_database = DjangoSimbaVehicleType.objects.all()[0]

        # Test if the vehicle type is a DjangoSimbaVehicleType
        assert isinstance(vehicle_from_database, DjangoSimbaVehicleType)

        # Test if VehicleType can be generated from a DjangoSimbaVehicleType object
        vehicle_eflips = EflipsVehicleType(vehicle_from_database)
        assert isinstance(vehicle_eflips, EflipsVehicleType)

        # Test if the properties of the VehicleType object are correct
        assert isinstance(vehicle_eflips.id, str)
        assert isinstance(vehicle_eflips.vehicle_class, str)
        assert vehicle_eflips.id == str(vehicle_from_database.id)
        assert isinstance(vehicle_eflips.battery_capacity_total, float)
        assert (
            vehicle_eflips.battery_capacity_total
            == vehicle_from_database.battery_capacity
        )
        assert isinstance(vehicle_eflips.charging_curve, Callable)

        assert isinstance(vehicle_eflips.soc_min, float)
        assert isinstance(vehicle_eflips.soc_max, float)
        assert isinstance(vehicle_eflips.soh, float)

        # Temporary test if default values are correct
        assert vehicle_eflips.soc_max == 1.0
        assert vehicle_eflips.soc_min == 0.0
        assert vehicle_eflips.soh == 1.0

        assert isinstance(vehicle_eflips.net_battery_capacity, float)
        assert (
            vehicle_eflips.net_battery_capacity
            == vehicle_eflips.battery_capacity_total
            * vehicle_eflips.soh
            * (vehicle_eflips.soc_max - vehicle_eflips.soc_min)
        )

        # Test if the charging curve is correct. Might be better ways but leave it there for now
        assert isinstance(vehicle_eflips.charging_curve(0), float)
        assert isinstance(vehicle_eflips.charging_curve(0.8), float)
        assert isinstance(vehicle_eflips.charging_curve(1), float)
        assert (
            vehicle_eflips.charging_curve(0)
            == vehicle_from_database.charging_curve[0][1]
        )
        assert (
            vehicle_eflips.charging_curve(0.8)
            == vehicle_from_database.charging_curve[1][1]
        )
        assert (
            vehicle_eflips.charging_curve(1)
            == vehicle_from_database.charging_curve[2][1]
        )

    def test_vehicle_schedule_from_django_simba(self, eflips_input_path):
        """This method tests if a VehicleSchedule object can be correctly created from a simba output JSON file"""

        vehicle_schedule_list = VehicleSchedule.from_rotations(eflips_input_path)

        assert isinstance(vehicle_schedule_list, list)

        for schedule in vehicle_schedule_list:
            assert isinstance(schedule, VehicleSchedule)
            assert isinstance(schedule.id, str)
            assert isinstance(schedule.vehicle_class, str)
            assert isinstance(schedule.departure, datetime)
            assert isinstance(schedule.arrival, datetime)
            assert isinstance(schedule.departure_soc, float)
            assert isinstance(schedule.arrival_soc, dict)
            if schedule.opportunity_charging is True:
                assert isinstance(schedule.minimal_soc, float)
                # TODO: fix minimum_soc to be a dict
                # assert isinstance(schedule.minimal_soc, dict)
            assert isinstance(schedule.opportunity_charging, bool)

    def test_eflips_from_simba_output(self, eflips_input_path: pathlib.Path, tmp_path):
        """
        This method tests for the presence and validity of the simBA output file.

        Args:
            eflips_input_path: A pathlib.Path object containing the absolute paths to the created files.
        """

        # Assert that the files exist
        assert eflips_input_path.exists()

        # Assert that the files are not empty
        assert eflips_input_path.stat().st_size > 0

        # Assert that eflips_input_path is a valid JSON file
        with open(eflips_input_path, "r") as f:
            simba_output = json.load(f)

        # The file should be a dictionary of rotation IDs, with a dict of values for each. Let's load the rotations
        for rotation_id, results in simba_output.items():
            # Check if the rotation ID exists
            assert Rotation.objects.filter(
                id=rotation_id
            ).exists(), "Rotation ID does not exist"

            # Load the trip with the lowest arrival time for this rotation
            first_trip = (
                Trip.objects.filter(rotation_id=rotation_id)
                .order_by("arrival_time")
                .first()
            )

            # Load the trip with the highest departure time for this rotation
            last_trip = (
                Trip.objects.filter(rotation_id=rotation_id)
                .order_by("-departure_time")
                .first()
            )

            # Check if the trip goes from the same station as the first trip to the same station as the last trip
            assert (
                first_trip.departure_stop == last_trip.arrival_stop
            ), "First trip departure stop does not match last trip arrival stop"

            # For each rotation, the vehicle_type in the results should be either a string or a list of strings,
            # dependeing on the number of VehicleTypes for the VehicleClass fot the rotation
            rotation = Rotation.objects.get(id=rotation_id)
            # The vehicle type should be in the vehicle class of the rotation
            vehicle_class = VehicleClass.objects.get(id=rotation.vehicle_class_id)
            if isinstance(results["vehicle_type"], list):
                vehicle_type_id = results["vehicle_type"][0]
            else:
                vehicle_type_id = results["vehicle_type"]
            vehicle_type = DjangoSimbaVehicleType.objects.get(id=vehicle_type_id)
            vehicle_class_for_vehicle_type = [
                vt.id for vt in vehicle_type.vehicle_class.all()
            ]
            assert (
                len(vehicle_class_for_vehicle_type) == 1
            ), "We do not support multiple vehicle classes per vehicle type yet"

            assert (
                vehicle_class_for_vehicle_type[0] == vehicle_class.id
            ), "Vehicle type does not match vehicle class"

            # Depending on the charging type, we are either looking for the "delta_soc" for depot chargers ("depb")
            # or for "minimal_soc" for opportunity chargers ("oppb")
            if results["charging_type"] == "depb":
                assert "delta_soc" in results, "delta_soc not found in results"
                assert isinstance(results["delta_soc"], float) or isinstance(
                    results["delta_soc"], list
                ), "delta_soc is not a float or list"
                if isinstance(results["delta_soc"], list):
                    assert len(results["delta_soc"]) == len(
                        results["vehicle_type"]
                    ), "delta_soc list has different length than vehicle_type list"
                    for delta_soc in results["delta_soc"]:
                        assert isinstance(
                            delta_soc, float
                        ), "delta_soc list contains non-float value"
            elif results["charging_type"] == "oppb":
                assert "minimal_soc" in results, "minimal_soc not found in results"
                assert isinstance(results["minimal_soc"], float) or isinstance(
                    results["minimal_soc"], list
                ), "minimal_soc is not a float or list"
                if isinstance(results["minimal_soc"], list):
                    assert len(results["minimal_soc"]) == len(
                        results["vehicle_type"]
                    ), "minimal_soc list has different length than vehicle_type list"
                    for minimal_soc in results["minimal_soc"]:
                        assert isinstance(
                            minimal_soc, float
                        ), "minimal_soc list contains non-float value"

    def test_whole_stack(self, eflips_input_path, tmp_path):
        """
        This method is a sample of how to use the eflips API in a Django project.

        :param eflips_input_path: A pathlib.Path object containing the inout JSON in the agreed
        """

        # We have the input JSON. Create a VehicleSchedule object from it
        vehicle_schedule_list = VehicleSchedule.from_rotations(eflips_input_path)

        # Get the Vehicle Types
        vehicle_types = []
        for djangosimba_vehicle_type in DjangoSimbaVehicleType.objects.all():
            vehicle_type = EflipsVehicleType(djangosimba_vehicle_type)
            vehicle_types.append(vehicle_type)

        # Initialize the simulation
        simulation_host = init_simulation(vehicle_types, vehicle_schedule_list)

        # Run the simulation
        depot_evaluation = run_simulation(simulation_host)

        # Optional: Find the total number of vehicles used and run the simulation again in order to get continuous
        # vehicle IDs and nicer plots *This is not relevant for the export back to django-simba*
        vehicle_counts = depot_evaluation.nvehicles_used_calculation()
        simulation_host = init_simulation(
            vehicle_types, vehicle_schedule_list, vehicle_counts
        )
        depot_evaluation = run_simulation(simulation_host)

        # Save the results to a folder
        output_for_simba = to_simba(depot_evaluation)
        with open(tmp_path / "output_for_simba.json", "w") as f:
            json.dump([dataclasses.asdict(o) for o in output_for_simba], f, indent=4)

        # Optional: Create a plot of the results
        depot_evaluation.path_results = str(tmp_path)

        depot_evaluation.vehicle_periods(
            periods={
                "depot general": "darkgray",
                "park": "lightgray",
                "serve_supply_clean_daily": "steelblue",
                "serve_clean_ext": "darkblue",
                "charge_dc": "forestgreen",
                "charge_oc": "forestgreen",
                "precondition": "black",
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

        # Check if the output file exists
        assert os.path.isfile(os.path.join(tmp_path, "vehicle_periods.pdf"))
        assert os.stat(os.path.join(tmp_path, "vehicle_periods.pdf")).st_size > 0

        assert os.path.isfile(os.path.join(tmp_path, "vehicle_periods.png"))
        assert os.stat(os.path.join(tmp_path, "vehicle_periods.png")).st_size > 0
