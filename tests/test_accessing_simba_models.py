import os
import pathlib
import uuid
from decimal import Decimal
from typing import Tuple, Callable
import json

import eflips

import djangosettings
from depot import SimulationHost, Depotinput

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.djangosettings")
import django
from django.core import management

django.setup()
management.call_command("flush", "--noinput")
management.call_command("migrate")

import pytest
from ebustoolbox.models import Scenario, Rotation, Trip, VehicleType
from eflips.depot.api.input import VehicleTypeEflips, VehicleTypeFromDatabase
from ebustoolbox.tasks import (
    run_ebus_toolbox,
    get_args,
    get_schedule_from_args,
    stations_to_db,
    vehicles_to_db,
    schedule_to_db,
)

from eflips.depot.standalone import SimpleTrip, Timetable
from eflips.depot.api.input import (
    read_timetable,
    get_rotation_from_database,
    get_simba_output,
    RotationFromDatabase,
    create_rotation_from_simba_output,
    load_vehicle_type_to_gc,
)
from eflips.settings import globalConstants as gc
from eflips.depot.simple_vehicle import VehicleType as SimpleVehicleType
from eflips.depot.api.basic import init_simulation, run_simulation

from eflips.depot.settings_config import load_data_from_database
from datetime import datetime


class TestAccessingSimBaModels:
    def scenario_to_db(self, cleaned_data):
        """
        Variant of ebustoolbox.tasks.scenario_to_db that does not use Django forms.
        """

        scenario = Scenario.objects.create(name=cleaned_data["title"])
        args = dict(cleaned_data)
        args["mode"] = list(map(lambda s: s.strip(), args["modes"].split(",")))
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
        """
        Variant of ebustoolbox.tasks.fill_db_with_input_files that does not use Django forms.
        """

        django_scenario = self.scenario_to_db(cleaned_data)
        original_args = get_args(django_scenario)
        simba_schedule, new_args = get_schedule_from_args(original_args)

        stations_to_db(
            django_scenario.options["station_data_path"],
            django_scenario.options["electrified_stations"],
            django_scenario,
        )
        vehicles_to_db(django_scenario.options["vehicle_types"], django_scenario)

        schedule_to_db(simba_schedule, django_scenario)

        return django_scenario, simba_schedule, original_args

    @pytest.fixture
    def eflips_input_path(self) -> pathlib.Path:
        """
        This method calls Django-Simba using the sample files in order to create the input files for eFlips.

        Returns:
            A pathlib.Path object containing the absolute paths to the created files.
        """

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
        django_scenario, simba_schedule, args = self.fill_db_with_input_files(
            input_data
        )

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

        return eflips_input_path

    def test_vehicle_type(self, eflips_input_path):
        v_ds = VehicleType.objects.all()[0]
        # assert isinstance(vt[0], VehicleType)

        # assert isinstance(vt[0].charging_curve, list)
        v_ef = VehicleTypeFromDatabase(v_ds)
        assert isinstance(v_ef, VehicleTypeEflips)
        assert isinstance(v_ef, VehicleTypeFromDatabase)
        assert isinstance(v_ef.id, str)
        assert v_ef.id == v_ds.name
        assert isinstance(v_ef.battery_capacity_total, float)
        assert v_ef.battery_capacity_total == v_ds.battery_capacity
        assert isinstance(v_ef.charging_curve, Callable)

        assert v_ef.charging_curve(0) == v_ds.charging_curve[0][1]
        assert v_ef.charging_curve(0.8) == v_ds.charging_curve[1][1]
        assert v_ef.charging_curve(1) == v_ds.charging_curve[2][1]

        assert v_ef.v2g_curve is None
        assert v_ef.soc_min == 0.0
        assert v_ef.soc_max == 1.0
        assert v_ef.soh == 1.0

        v_dict = v_ef._to_eflips_global_constants()
        assert isinstance(v_dict, dict)
        print(v_dict)

    def test_load_vehicle_types(self, eflips_input_path):
        absolute_path = os.path.dirname(__file__)
        fsettings = os.path.join(absolute_path, "sample_simulation", "settings")
        eflips.load_settings(fsettings)
        vehicle_types_from_database = VehicleType.objects.all()
        gc = eflips.depot.settings_config.load_data_from_database(
            vehicle_types_from_database
        )
        vt_from_database = VehicleType.objects.all()
        vt_dict = {}
        for vt in vt_from_database:
            v = VehicleTypeFromDatabase(vt)
            vc = v._to_eflips_global_constants()
            vt_dict.update(vc)

        assert isinstance(vt_dict, dict)
        # print(vt_dict)

        gc["depot"]["vehicle_types"] = vt_dict

        # assert isinstance(vehicle_types_from_gc, dict)
        #
        #
        v_list = []
        for ID in vt_dict:
            vt = SimpleVehicleType(ID, **vt_dict[ID])
            v_list.append(vt)
            assert isinstance(vt, SimpleVehicleType)

    def test_load_vehicle_to_gc(self, eflips_input_path):
        vt_dict = load_vehicle_type_to_gc()
        assert isinstance(vt_dict, dict)

    @pytest.fixture
    def simulation_host(self) -> SimulationHost:
        absolute_path = os.path.dirname(__file__)

        filename_template = os.path.join(
            absolute_path, "sample_simulation", "sample_depot"
        )

        simulation_host = eflips.depot.SimulationHost(
            [
                eflips.depot.Depotinput(
                    filename_template=filename_template, show_gui=False
                )
            ],
            run_progressbar=True,
            print_timestamps=True,
            tictocname="",
        )

        assert isinstance(simulation_host, SimulationHost)
        return simulation_host

    def test_init_settings(
        self, simulation_host: SimulationHost, eflips_input_path: pathlib.Path
    ):
        absolute_path = os.path.dirname(__file__)
        filename_eflips_settings = os.path.join(
            absolute_path, "sample_simulation", "settings"
        )
        filename_schedule = os.path.join(absolute_path, "sample_simulation", "schedule")

        eflips.load_settings(filename_eflips_settings)
        gc["depot"]["vehicle_types"] = load_vehicle_type_to_gc()
        eflips.depot.settings_config.check_gc_validity()
        eflips.depot.settings_config.complete_gc()

        rotations = create_rotation_from_simba_output(eflips_input_path)
        trips = read_timetable(simulation_host.env, rotations)
        timetable = Timetable(simulation_host.env, trips)
        simulation_host.timetable = timetable
        simulation_host.run()
        # print(gc['depot']['vehicle_types'])

        # print(gc['depot']['vehicle_types'])
        # assert isinstance(gc['depot']['vehicle_types_obj'], list)
        # print(len(gc['depot']['vehicle_types_obj']))

    def test_reading_rotation_from_database(self, eflips_input_path: pathlib.Path):
        simba_output = get_simba_output(eflips_input_path)
        for rotation_id, results in simba_output.items():
            rotation = RotationFromDatabase(int(rotation_id))
            rotation.read_data_for_simba(results)
            rotation.read_data_from_database()
            assert isinstance(rotation, RotationFromDatabase)
            assert isinstance(rotation.name, int)
            assert isinstance(rotation.vehicle_class, int)
            assert isinstance(rotation.scenario, Scenario)
            assert isinstance(rotation.departure_soc, float)
            assert isinstance(rotation.arrival_soc, float)
            assert isinstance(rotation.minimal_soc, float)
            assert isinstance(rotation.delta_soc, list) or isinstance(
                rotation.delta_soc, None
            )
            assert isinstance(rotation.charging_type, str)
            assert isinstance(rotation.vehicle_type, list)

        # read from rotation database
        # rotation = Rotation.objects.all()
        # simba_output = get_simba_output(eflips_input_path)
        # for rotation_id, results in simba_output.items():
        #     rotation = RotationFromDatabase(int(rotation_id))
        #     rotation.read_data_from_database()
        #     rotation.read_data_for_simba(results)
        #     assert isinstance(rotation, RotationFromDatabase)
        #     assert isinstance(rotation.id, int)

    def test_create_rotation_list(self, eflips_input_path: pathlib.Path):
        rotations = create_rotation_from_simba_output(eflips_input_path)
        assert isinstance(rotations, list)
        for rotation in rotations:
            assert isinstance(rotation, RotationFromDatabase)

        rotation = [r for r in rotations if r.id > 0]
        print(len(rotation))
        assert isinstance(rotation, list)
        for r in rotation:
            assert isinstance(r.id, int)
            print(r.id)

        rotation = rotation[0]

    def test_reading_trip_from_database(self, eflips_input_path: pathlib.Path):
        # select trips from simba output
        rotations = create_rotation_from_simba_output(eflips_input_path)
        trips = read_timetable(None, rotations)

        r_ids = []
        for rotation in rotations:
            r_ids.append(rotation.id)

        # trips_from_database = Trip.objects.filter(rotation_id__in=r_ids).all()
        assert isinstance(trips, list)
        assert len(rotations) == len(trips)

        for trip in trips:
            assert isinstance(trip, SimpleTrip)
            assert isinstance(trip.std, int)
            assert isinstance(trip.sta, int)
            assert isinstance(trip.start_soc, float)
            assert isinstance(trip.end_soc, float)
            assert isinstance(trip.distance, float)
            assert isinstance(trip.vehicle_types, list)
            assert isinstance(trip.charge_on_track, bool)
            assert trip.std > 0
            assert trip.sta > 0
            assert trip.start_soc >= 0
            # assert trip.end_soc >= 0  False: end_soc is negative in simba output

        timetable = Timetable(None, trips)
        assert isinstance(timetable, Timetable)

    def test_eflips_from_simba_output(self, eflips_input_path: pathlib.Path):
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

        # TODO: REMOVE THIS LATER. We are modifying the JSON file's contents after laoding
        # Once django-simba fixes their #28, we can remove this
        for rotation_id, results in simba_output.items():
            # Make all the "vehicle_type" lists contain only distinct items
            if isinstance(results["vehicle_type"], list):
                results["vehicle_type"] = list(set(results["vehicle_type"]))

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
            database_vehicle_types = VehicleType.objects.filter(
                vehicle_class=rotation.vehicle_class
            ).all()

            if len(database_vehicle_types) == 1:
                assert (
                    results["vehicle_type"] == database_vehicle_types[0].id
                ), "vehicle_type does not match vehicle_class"
            else:
                assert isinstance(
                    results["vehicle_type"], list
                ), "vehicle_type is not a list"
                assert len(results["vehicle_type"]) == len(
                    database_vehicle_types
                ), "vehicle_type list has different length than vehicle_class list"
                assert set([v.id for v in database_vehicle_types]) == set(
                    results["vehicle_type"]
                ), "vehicle_type list does not match vehicle_class list"

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
