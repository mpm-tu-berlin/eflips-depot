import os
import pathlib
import uuid
from datetime import datetime
from decimal import Decimal
import json
import pytest

import eflips
from depot import SimulationHost
from eflips.settings import globalConstants as gc

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.api.djangosettings")
import django

django.setup()
from django.core import management

management.call_command("migrate")

from api import djangosettings

from ebustoolbox.models import Scenario, VehicleClass, Trip, Rotation
from ebustoolbox.models import VehicleType as DjangoSimbaVehicleType

from ebustoolbox.tasks import (
    run_ebus_toolbox,
    get_args,
    get_schedule_from_args,
    stations_to_db,
    vehicles_to_db,
    schedule_to_db,
)

from eflips.depot.api.django_simba.input import VehicleType
from eflips.depot.api.django_simba.input import VehicleType as EflipsVehicleType
from eflips.depot.api.django_simba.input import VehicleSchedule as EflipsVehicleSchedule

from eflips.depot.standalone import Timetable, SimpleTrip


class TestInitSimulationHost:
    @pytest.fixture
    def input_data(self):
        # Find the absolute path to the test folder (the folder this file is in)
        absolute_path = os.path.dirname(__file__)
        path_to_sample_simulation = os.path.join(
            absolute_path, "api", "sample_simBA_input"
        )

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

        scenario.opps_charging_power = scenario.options["cs_power_opps"]
        scenario.deps_charging_power = scenario.options["cs_power_deps_depb"]
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
                results["vehicle_type"] = [1]

        with open(eflips_input_path, "w") as f:
            json.dump(simba_output, f)

        return eflips_input_path

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

    def test_load_vehicle_from_api(self, simulation_host):
        # Find the path of settings.json
        absolute_path = os.path.dirname(__file__)
        filename_eflips_settings = os.path.join(
            absolute_path, "sample_simulation", "settings"
        )

        # Load settings.json to globalConstants
        eflips.load_settings(filename_eflips_settings)

        # Substitute VehicleType-related dictionary content in globalConstants with the one from the database

        # TODO: generate the dictionary for EflipsVehicleType from the database OR BETTER
        #  directly create VehicleType objects and give them to gc['depot']['vehicle_types_obj']
        # vehicle_types, vehicle_types_obj = load_vehicle_type_to_gc()
        # Example. See setting.json
        # gc["depot"]["vehicle_types"] = "vehicle_types": {
        #                                   1: {
        #                                     "battery_capacity": 300,
        #                                     "soc_min": 0.1,
        #                                     "soc_max": 0.9,
        #                                     "soc_init": 0.9,
        #                                     "soh": 0.8
        #                                   },
        #                                   2: {
        #                                     "battery_capacity": 174,
        #                                     "soc_min": 0.05,
        #                                     "soc_max": 0.95,
        #                                     "soc_init": 0.95,
        #                                     "soh": 0.85
        #                                   }
        #                             }

        # gc["depot"]["vehicle_count"]["KLS"] = {
        #     "articulated bus - depot charging": 5,
        #     "articulated bus - opportunity charging": 4,
        #     "solo bus - depot charging": 5,
        #     "solo bus - opportunity charging": 4,
        # }
        # gc["depot"]["slot_length"] = {
        #     "default": 133,
        #     "articulated bus - depot charging": 91,
        #     "articulated bus - opportunity charging": 91,
        #     "solo bus - depot charging": 133,
        #     "solo bus - opportunity charging": 133,
        # }

        # Substitutable_types are only related to vehicles in the parking area. Might ignore for now?
        # gc["depot"]["substitutable_types"] = [
        #     ["SB_DC", "solo bus - depot charging", "solo bus - opportunity charging"],
        #     [
        #         "AB_OC",
        #         "articulated bus - depot charging",
        #         "articulated bus - " "opportunity charging",
        #     ],
        # ]

        # Copied from SimulationHost.load_eflips_settings()
        eflips.depot.settings_config.check_gc_validity()

        # Here VehicleType objects are created from the dictionary in globalConstants. It's worth considering that we
        # re-write this method completely by giving VehicleType objects to gc["depot"]["vehicle_types_obj"] directly
        eflips.depot.settings_config.complete_gc()

    def test_load_timetable_from_api(self, simulation_host, eflips_input_path):
        """This method tests if eflips.depot.standalone.Timetable can be created from the simba output JSON file and
        SimulationHost can be initialized with it"""

        vehicle_schedules_from_simba = EflipsVehicleSchedule.from_rotations(
            eflips_input_path
        )

        # Tests if the generated EflipsVehicleSchedule objects are correct
        assert isinstance(vehicle_schedules_from_simba, list)
        # Test if SimpleTrip objects are time-ordered
        # ...

        # Test if the generated Timetable object is correct
        timetable = EflipsVehicleSchedule._to_timetable(
            vehicle_schedules_from_simba, simulation_host.env
        )

        assert isinstance(timetable, Timetable)

        simulation_host.timetable = timetable

    def test_run_simulation(self, simulation_host):
        # Copied from SimulationHost.standard_setup() in simulation.py
        for dh, di in zip(simulation_host.depot_hosts, simulation_host.to_simulate):
            dh.load_and_complete_template(di.filename_template)
        simulation_host.complete()

        # Run simulation
        simulation_host.run()
