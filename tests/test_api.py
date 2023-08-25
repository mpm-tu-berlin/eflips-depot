import os

import pytest

from dataclasses import asdict
import json
from depot import DepotEvaluation
import eflips.api.basic

# import eflips.depot.basic

class TestApiSetup:

    @pytest.fixture
    def depot_evaluation(self):
        """This method creates a sample depot evaluation object containing some sample data using wrapped-up methods
        """
        absolute_path = os.path.dirname(__file__)
        filename_eflips_settings = os.path.join(absolute_path, 'sample_simulation', 'settings')
        filename_schedule = os.path.join(absolute_path, 'sample_simulation', 'schedule')
        filename_template = os.path.join(absolute_path, 'sample_simulation', 'sample_depot')

        host = eflips.api.basic.init_simulation(filename_eflips_settings, filename_schedule,
                                         filename_template)

        ev = eflips.api.basic.run_simulation(host)
        return ev

    def test_result_output(self, depot_evaluation: DepotEvaluation):
        # depot_evaluation.path_results = str(tmp_path)

        ev = depot_evaluation
        absolute_path = os.path.dirname(__file__)
        tmp_path = os.path.join(absolute_path, '..', 'bus_depot', 'results')

        # test save_simulation_result()
        # eflips.depot.basic.save_simulation_result(ev, tmp_path)
        # assert os.path.isfile(os.path.join(tmp_path, 'vehicle_periods.pdf'))
        # assert os.stat(os.path.join(tmp_path, 'vehicle_periods.pdf')).st_size > 0
        #
        # assert os.path.isfile(os.path.join(tmp_path, 'vehicle_periods.png'))
        # assert os.stat(os.path.join(tmp_path, 'vehicle_periods.png')).st_size > 0

        # test save_trips_issued()
        # trips_issued = eflips.depot.basic.save_trips_issued(ev)
        # assert isinstance(trips_issued, list)

        # for trip in trips_issued:
            # if '_r1' in trip.ID:
            # assert isinstance(trip, eflips.depot.output_class.SimpleTripOutput)
            # #     # assert isinstance(trip, SimpleTrip)
            # #     # assert isinstance(trip.env, simpy.Environment)
            # assert isinstance(trip.ID, str)
            # assert isinstance(trip.line_name, str)
            # #
            # assert isinstance(trip.origin, str)
            # assert isinstance(trip.destination, str)
            # assert isinstance(trip.vehicle_types, list)
            #
            # for v in trip.vehicle_types:
            #     assert isinstance(v, eflips.depot.simple_vehicle.VehicleType)
            #
            # assert isinstance(trip.std, int)
            # assert isinstance(trip.sta, int)
            # assert isinstance(trip.distance, float) or isinstance(trip.distance, int)
            #
            # assert isinstance(trip.start_soc, int) or isinstance(trip.start_soc, float) or trip.start_soc is None
            # assert isinstance(trip.start_soc, int) or isinstance(trip.start_soc, float) or trip.end_soc is None
            # assert isinstance(trip.charge_on_track, int) or isinstance(trip.charge_on_track, float)
            #
            # assert isinstance(trip.eta, int)
            #
            # # Can always be not None for _r1 trips
            #
            # assert isinstance(trip.atd, int)
            # assert isinstance(trip.ata, int)
            #
            # assert isinstance(trip.vehicle, SimpleVehicle)
            # assert isinstance(trip.reserved_for_init, bool)
            # assert isinstance(trip.vehicle_from, str)
            # assert isinstance(trip.copy_of, SimpleTrip) or trip.copy_of is None
            # assert isinstance(trip.t_match, int) or trip.t_match is None
            # assert isinstance(trip.got_early_vehicle, bool)
            # assert isinstance(trip.periodic_trigger_scheduled, bool)
            # assert isinstance(trip.ID_orig, str)
            # assert isinstance(trip.t_got_early_vehicle, str) or trip.t_got_early_vehicle is None
            # #     # # #
            # #     #     # Test properties of class SimpleTrip
            # assert isinstance(trip.ID_orig, str)
            # assert isinstance(trip.vehicle_types, list)
            # assert isinstance(trip.vehicle_types_joinedstr, str)  # vehicle_types is a list of strings or SimpleVehicle?
            # assert isinstance(trip.delay_departure, int)
            # assert isinstance(trip.delayed_departure, bool)
            # assert trip.delay_departure <= 0
            #
            # assert isinstance(trip.delay_arrival, int)
            # assert isinstance(trip.delayed_arrival, bool)
            # assert trip.delay_arrival <= 0

            # assert isinstance(trip.duration, int)
            # assert trip.duration > 0
            # assert isinstance(trip.actual_duration, int)
            # assert trip.actual_duration > 0
            # assert isinstance(trip.actual_duration, int)
            # assert isinstance(trip.lead_time_match, int) or trip.lead_time_match is None
        data_to_simba = eflips.api.basic.to_simba(ev)
        assert data_to_simba is not None
        for i in data_to_simba:
            assert i is not None
            assert isinstance(i, eflips.api.basic.InputForSimba)

            assert i.rotation_id is not None
            assert isinstance(i.rotation_id, int)

            assert i.vehicle_id is not None
            assert isinstance(i.vehicle_id, int) or isinstance(i.vehicle_id, str)

            assert i.soc_departure is not None
            assert isinstance(i.soc_departure, float)
            assert 0 <= i.soc_departure <= 1

    def test_json_serialization(self, depot_evaluation: DepotEvaluation):
        # Create a list of SimBaOutputFormat objects

        ev = depot_evaluation
        simba_outputs = eflips.api.basic.to_simba(ev)
        simba_output_as_dicts = [asdict(s) for s in simba_outputs]
        jsonified = json.dumps(simba_output_as_dicts)

        # Re-create the output list
        new_simba_output_dict = json.loads(jsonified)
        new_simba_outputs = [eflips.api.basic.InputForSimba(**kwargs) for kwargs in
                             new_simba_output_dict]
        assert isinstance(new_simba_outputs, list)

        # Check equality
        for old, new in zip(simba_outputs, new_simba_outputs):
            assert old == new

        # for trip in trips_issued:
            # assert isinstance(trip.vehicle, SimpleVehicle)
            # v = trip.vehicle
            # assert isinstance(v.ID, str)
            # assert isinstance(v.vehicle_type, VehicleType)
            # assert isinstance(v.battery, SimpleBattery)
            # assert v.vehicle_type.battery_capacity == v.battery.energy_nominal
            # assert v.vehicle_type.soc_min == v.battery.soc_min
            # assert v.vehicle_type.soc_max == v.battery.soc_max
            # assert v.vehicle_type.soh == v.battery.soh

            # assert isinstance(v.mileage, float) and v.mileage > 0

            # None in ev (output) even for issued trips
            # assert isinstance(v.trip, SimpleTrip)


            # assert isinstance(v.trip_at_departure, SimpleTrip)
            # assert isinstance(v.finished_trips, list) and len(v.finished_trips) != 0
            # for ft in v.finished_trips:
            #     assert isinstance(ft, SimpleTrip)
            #
            # assert isinstance(v.system_entry, bool) and v.system_entry is True
            # assert isinstance(v.battery_logs, list) and len(v.battery_logs) != 0
            # assert isinstance(v.power_logs, dict) and len(v.power_logs) != 1



