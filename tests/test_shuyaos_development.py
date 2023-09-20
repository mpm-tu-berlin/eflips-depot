import os
import pathlib

import eflips

from depot import SimulationHost

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.djangosettings")
import django
from django.core import management

django.setup()
management.call_command("flush", "--noinput")
management.call_command("migrate")

from ebustoolbox.models import Scenario

from eflips.depot.api.django_simba.input import VehicleType


class TestAccessingSimBaModels:
    def test_load_vehicle_types(self, eflips_input_path):
        """TODO: load VehicleType objects into gc["depot"]["vehicle_types"] or find a better way to do it without global constants"""
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

    def test_init_settings(
        self, simulation_host: SimulationHost, eflips_input_path: pathlib.Path
    ):
        absolute_path = os.path.dirname(__file__)
        filename_eflips_settings = os.path.join(
            absolute_path, "sample_simulation", "settings"
        )
        filename_schedule = os.path.join(absolute_path, "sample_simulation", "schedule")

        #   load_eflips_settings() in simulation.py
        eflips.load_settings(filename_eflips_settings)
        vehicle_types, vehicle_types_obj = load_vehicle_type_to_gc()

        gc["depot"]["vehicle_types"] = vehicle_types

        gc["depot"]["vehicle_count"]["KLS"] = {
            "articulated bus - depot charging": 5,
            "articulated bus - opportunity charging": 4,
            "solo bus - depot charging": 5,
            "solo bus - opportunity charging": 4,
        }
        gc["depot"]["slot_length"] = {
            "default": 133,
            "articulated bus - depot charging": 91,
            "articulated bus - opportunity charging": 91,
            "solo bus - depot charging": 133,
            "solo bus - opportunity charging": 133,
        }
        gc["depot"]["substitutable_types"] = [
            ["SB_DC", "solo bus - depot charging", "solo bus - opportunity charging"],
            [
                "AB_OC",
                "articulated bus - depot charging",
                "articulated bus - " "opportunity charging",
            ],
        ]

        eflips.depot.settings_config.check_gc_validity()

        eflips.depot.settings_config.complete_gc()

        # re-write complente_gc()

        # vehicle_type_data = gc["depot"]["vehicle_types"]
        # gc["depot"]["vehicle_types_obj"] = vehicle_types_obj
        #
        # eflips.depot.settings_config.complete_gc()

        rotations = create_rotation_from_simba_output(eflips_input_path)
        trips = read_timetable(simulation_host.env, rotations)

        # basically init_timetable() in simulation.py
        timetable = Timetable(simulation_host.env, trips)
        simulation_host.timetable = timetable

        # Add group for each VehicleType
        # for vt_obj, vt_dict in zip(gc["depot"]["vehicle_types_obj"], gc["depot"]["vehicle_types_obj_dict"]):
        #     group =

        # vt_obj.group = vt_dict["group"]
        # copied from standard_setup() in simulation.py
        for dh, di in zip(simulation_host.depot_hosts, simulation_host.to_simulate):
            dh.load_and_complete_template(di.filename_template)
        simulation_host.complete()

        simulation_host.run()

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
