"""
Validation tests for after executing the depot simulation.

"""
from collections import Counter


class Validator:
    """

    ev: [DepotEvaluation]
    """

    def __init__(self, ev):
        self.ev = ev
        self.results = {}

    @property
    def valid(self):
        return all(ri["valid"] for ri in self.results.values())

    def all_periods(self, periods_map):
        """Prepare and call all periods-related tests.

        periods_map: [dict] of period key and their template-specific name as
        str. For the charging process, the value is a list.
            E.g.:
            {'depot general': 'depot general',
            'park': 'park',
            'serve': 'serve',
            'charge': ['charge_dc', 'charge_oc']}
        """
        # Unpack names for getting data
        period_names = []
        for v in periods_map.values():
            if isinstance(v, str):
                period_names.append(v)
            else:
                for vi in v:
                    period_names.append(vi)

        # Get data
        vehicledata = {}
        vehicle_no = 0
        for vehicle in self.ev.vehicle_generator.items:
            vehicledata[vehicle.ID] = {}
            vehicledata[vehicle.ID]["plotdata"] = self.ev.get_periods(
                vehicle, vehicle_no, periods=period_names
            )
            vehicle_no += 1

        # Run specific tests
        self._park_inside_depot(
            vehicledata,
            name_general=periods_map["depot general"],
            name_park=periods_map["park"],
        )
        self._charge_inside_park(
            vehicledata,
            name_park=periods_map["park"],
            names_charge=periods_map["charge"],
        )
        self._service_outside_park(
            vehicledata, name_park=periods_map["park"], name_serve=periods_map["serve"]
        )
        self._trip_outside_depot(vehicledata, name_general=periods_map["depot general"])

    def _park_inside_depot(self, vehicledata, name_general, name_park):
        """Check if all parking periods are inside general depot periods."""
        result = {}
        for vID in vehicledata:
            generals = vehicledata[vID]["plotdata"][name_general]["xranges"]
            parks = vehicledata[vID]["plotdata"][name_park]["xranges"]

            found = [False] * len(parks)
            i = 0
            for park_start, park_dur in parks:
                for general_start, general_dur in generals:
                    if (
                        park_start >= general_start
                        and park_start + park_dur <= general_start + general_dur
                    ):
                        found[i] = True
                        i += 1
                        break
            result[vID] = found

        valid = True
        for ri in result.values():
            if not all(ri):
                valid = False
                break

        self.results["park_inside_depot"] = {"valid": valid, "result": result}

    def _charge_inside_park(self, vehicledata, name_park, names_charge):
        """Check if all charging periods are inside parking periods.

        names_charge relies on vehicles only having one type of charging
        process (e.g. charge_fc or charge_oc, but not both)
        """
        result = {}
        for vID in vehicledata:
            parks = vehicledata[vID]["plotdata"][name_park]["xranges"]
            # Find the applicable charging process name
            for name_charge in names_charge:
                # if 'GN' in vID:
                #     print(vID, name_charge)
                #     print(vehicledata[vID]['plotdata'])
                if vehicledata[vID]["plotdata"][name_charge]["xranges"]:
                    break
            charges = vehicledata[vID]["plotdata"][name_charge]["xranges"]

            found = [False] * len(charges)
            i = 0
            for charge_start, charge_dur in charges:
                for park_start, park_dur in parks:
                    if (
                        charge_start >= park_start
                        and charge_start + charge_dur <= park_start + park_dur
                    ):
                        found[i] = True
                        i += 1
                        break
            result[vID] = found

        valid = True
        for ri in result.values():
            if not all(ri):
                valid = False
                break

        self.results["charge_inside_park"] = {"valid": valid, "result": result}

    def _service_outside_park(self, vehicledata, name_park, name_serve):
        """Check if all service periods are outside parking periods."""
        result = {}
        for vID in vehicledata:
            parks = vehicledata[vID]["plotdata"][name_park]["xranges"]
            serves = vehicledata[vID]["plotdata"][name_serve]["xranges"]

            not_found = [True] * len(serves)
            i = 0
            for serve_start, serve_dur in serves:
                for park_start, park_dur in parks:
                    park_end = park_start + park_dur
                    if (
                        park_start < serve_start < park_end
                        or park_start < serve_start + serve_dur < park_end
                    ):
                        not_found[i] = False
                        i += 1
                        break
            result[vID] = not_found

        valid = True
        for ri in result.values():
            if not all(ri):
                valid = False
                break

        self.results["service_outside_park"] = {"valid": valid, "result": result}

    def _trip_outside_depot(self, vehicledata, name_general):
        """Check if all trip periods are outside general depot periods."""
        result = {}

        for vID in vehicledata:
            generals = vehicledata[vID]["plotdata"][name_general]["xranges"]
            trips = self.ev.vehicle_generator.select(vID).finished_trips

            not_found = [True] * len(trips)
            i = 0
            for trip in trips:
                for general_start, general_dur in generals:
                    general_end = general_start + general_dur
                    if (
                        general_start < trip.atd < general_end
                        or general_start < trip.ata < general_end
                    ):
                        not_found[i] = False
                        i += 1
                        break
            result[vID] = not_found

        valid = True
        for ri in result.values():
            if not all(ri):
                valid = False
                break

        self.results["trip_outside_depot"] = {"valid": valid, "result": result}

    def single_matches(self):
        """Check if trips are assigned only to one vehicle each."""
        trips = Counter()
        for vehicle in self.ev.vehicle_generator.items:
            trips.update(trip.ID for trip in vehicle.finished_trips)
        self.results["single_matches"] = {
            "valid": len(trips) == sum(trips.values()),
            "trips": trips,
        }
