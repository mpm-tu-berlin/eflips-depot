

import os
import pathlib
import uuid
from decimal import Decimal
from typing import Tuple
import json

import djangosettings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.djangosettings")
import django
from django.core import management
django.setup()
management.call_command("migrate")

import pytest
from ebustoolbox.models import Scenario, Rotation, Trip, VehicleType

from ebustoolbox.tasks import run_ebus_toolbox, get_args, get_schedule_from_args, \
    stations_to_db, vehicles_to_db, schedule_to_db





class TestAccessingSimBaModels:
    def scenario_to_db(self, cleaned_data):
        """
        Variant of ebustoolbox.tasks.scenario_to_db that does not use Django forms.
        """

        scenario = Scenario.objects.create(name=cleaned_data["title"])
        args = dict(cleaned_data)
        args["mode"] = list(map(lambda s: s.strip(), args["modes"].split(',')))
        # decimal -> float
        for k, v in args.items():
            if type(v) == Decimal:
                args[k] = float(v)
        scenario.options = args

        scenario.opps_charging_power = scenario.options["cs_power_opps"]
        scenario.deps_charging_power = scenario.options["cs_power_deps_depb"]
        scenario.save()
        return scenario

    def fill_db_with_input_files(self, cleaned_data):
        django_scenario = self.scenario_to_db(cleaned_data)
        original_args = get_args(django_scenario)
        simba_schedule, new_args = get_schedule_from_args(original_args)

        stations_to_db(django_scenario.options["station_data_path"], django_scenario.options["electrified_stations"],
                       django_scenario)
        vehicles_to_db(django_scenario.options["vehicle_types"], django_scenario)

        schedule_to_db(simba_schedule, django_scenario)

        return django_scenario, simba_schedule, original_args

    @pytest.fixture
    def eflips_input_path(self) -> pathlib.Path:
        """
        This method calls Django-Simba using the sample files in order to create the input files for eFlips.

        Returns:
            A tupple of pathlib.Path objects containing the absolute paths to the created files.
        """

        # Find the absolute path to the test folder (the folder this file is in)
        absolute_path = os.path.dirname(__file__)
        path_to_sample_simulation = os.path.join(absolute_path, 'sample_simBA_input')

        # This is a copy-paste from running the django web app in a debugger
        input_data = {
            'title': 'SimBA',
            'preferred_charging_type': 'oppb',
            'modes': 'sim,report',
            'gc_power_opps': Decimal('100000.0'),
            'gc_power_deps': Decimal('100000.0'),
            'cs_power_opps': Decimal('300'),
            'cs_power_deps_depb': Decimal('150'),
            'cs_power_deps_oppb': Decimal('150'),
            'default_voltage_level': 'MV',
            'desired_soc_deps': Decimal('1'),
            'desired_soc_opps': Decimal('1'),
            'min_recharge_deps_oppb': Decimal('1'),
            'min_recharge_deps_depb': Decimal('1'),
            'min_charging_time': Decimal('0'),
            'default_buffer_time_opps': Decimal('0'),
            'input_schedule': os.path.join(path_to_sample_simulation, 'trips_example.csv'),
            'electrified_stations': os.path.join(path_to_sample_simulation, 'electrified_stations.json'),
            'vehicle_types': os.path.join(path_to_sample_simulation, 'vehicle_types.json'),
            'station_data_path': os.path.join(path_to_sample_simulation, 'all_stations.csv'),
            'outside_temperature_over_day_path': os.path.join(path_to_sample_simulation, 'default_temp_summer.csv'),
            'level_of_loading_over_day_path': os.path.join(path_to_sample_simulation, 'default_level_of_loading_over_day.csv'),
            'cost_parameters_file': os.path.join(path_to_sample_simulation, 'cost_params.json'),
            'strategy': 'distributed',
            'interval': Decimal('15'),
            'signal_time_dif': Decimal('10'),
            'days': None,
            'include_price_csv': None,
            'seed': '',
            'cost_calculation': False
        }
        django_scenario, simba_schedule, args = self.fill_db_with_input_files(input_data)

        task_id = str(uuid.uuid4())

        django_scenario.task_id = task_id
        django_scenario.save()
        run_ebus_toolbox(simba_schedule, args, task_id)

        # By API contract, the files are located in settings.BASE_DIR / settings.UPLOAD_PATH / task_id
        path_to_files = pathlib.Path(djangosettings.BASE_DIR) / djangosettings.UPLOAD_PATH / task_id

        # The files are named eflips_input.json and rotation_socs.csv
        eflips_input_path = path_to_files / "report_1"/ "eflips_input.json"

        return eflips_input_path

    def test_eflips_from_simba_output(self, eflips_input_path: pathlib.Path):
        """
        This method tests for the presence and validity of the simBA output file.

        Args:
            eflips_input_paths: A tupple of pathlib.Path objects containing the absolute paths to the created files.
        """

        eflips_input_path

        # Assert that the files exist
        assert eflips_input_path.exists()

        # Assert that the files are not empty
        assert eflips_input_path.stat().st_size > 0

        # Assert that eflips_input_path is a valid JSON file
        with open(eflips_input_path, 'r') as f:
            simba_output = json.load(f)

        # The file should be a dictionary of rotation IDs, with a dict of values for each. Let's load the rotations
        for rotation_id, results in simba_output.items():
            # Check if the rotation ID exists
            assert Rotation.objects.filter(id=rotation_id).exists(), "Rotation ID does not exist"

            # Load the trip with the lowest arrival time for this rotation
            first_trip = Trip.objects.filter(rotation_id=rotation_id).order_by('arrival_time').first()

            # Load the trip with the highest departure time for this rotation
            last_trip = Trip.objects.filter(rotation_id=rotation_id).order_by('-departure_time').first()

            # Check if the trip goes from the same station as the first trip to the same station as the last trip
            assert first_trip.departure_stop == last_trip.arrival_stop, \
                "First trip departure stop does not match last trip arrival stop"

            # For each rotation, the vehicle_type in the results should be either a string or a list of strings,
            # dependeing on the number of VehicleTypes for the VehicleClass fot the rotation
            rotation = Rotation.objects.get(id=rotation_id)
            database_vehicle_types = VehicleType.objects.filter(vehicle_class=rotation.vehicle_class).all()

            if len(database_vehicle_types) == 1:
                assert results["vehicle_type"] == database_vehicle_types[0].id, \
                    "vehicle_type does not match vehicle_class"
            else:
                assert isinstance(results["vehicle_type"], list), "vehicle_type is not a list"
                assert len(results["vehicle_type"]) == len(database_vehicle_types), \
                    "vehicle_type list has different length than vehicle_class list"
                assert set([v.id for v in database_vehicle_types]) == set(results["vehicle_type"]), \
                    "vehicle_type list does not match vehicle_class list"

            # Depending on the charging type, we are either looking for the "delta_soc" for depot chargers ("depb")
            # or for "minimal_soc" for opportunity chargers ("oppb")
            if results["charging_type"] == "depb":
                assert "delta_soc" in results, "delta_soc not found in results"
                assert isinstance(results["delta_soc"], float) or isinstance(results["delta_soc"], list), \
                    "delta_soc is not a float or list"
                if isinstance(results["delta_soc"], list):
                    assert len(results["delta_soc"]) == len(results["vehicle_type"]), \
                        "delta_soc list has different length than vehicle_type list"
                    for delta_soc in results["delta_soc"]:
                        assert isinstance(delta_soc, float), "delta_soc list contains non-float value"
            elif results["charging_type"] == "oppb":
                assert "minimal_soc" in results, "minimal_soc not found in results"
                assert isinstance(results["minimal_soc"], float) or isinstance(results["minimal_soc"], list), \
                    "minimal_soc is not a float or list"
                if isinstance(results["minimal_soc"], list):
                    assert len(results["minimal_soc"]) == len(results["vehicle_type"]), \
                        "minimal_soc list has different length than vehicle_type list"
                    for minimal_soc in results["minimal_soc"]:
                        assert isinstance(minimal_soc, float), "minimal_soc list contains non-float value"







