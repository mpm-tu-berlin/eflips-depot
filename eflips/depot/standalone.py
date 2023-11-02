# -*- coding: utf-8 -*-
"""
Created on Sun Oct  1 12:50:18 2017

@author: p.mundt, e.lauth

Complementary components that are necessary for a standalone run of the depot
simulation.

"""
from math import ceil
from warnings import warn

from eflips.evaluation import DataLogger
from eflips.helperFunctions import flexprint
from eflips.settings import globalConstants
from xlrd import open_workbook

from eflips.depot.depot import VehicleFilter, BackgroundStore
from eflips.depot.simple_vehicle import SimpleVehicle


class VehicleGenerator(BackgroundStore):
    """Initialize SimpleVehicle objects at the start of the simulation.

    Attributes:
    items: [list] containing all generated vehicles

    """

    def __init__(self, env):
        super(VehicleGenerator, self).__init__(env, "VehicleGenerator")
        self.vIDCounter = {}
        self.map_depots = None

    def _complete(self, depots):
        """Preparations that must take place before self.run and simulation
        start, but may not be possible during the depot configuration phase.
        """
        self.map_depots = {depot.ID: depot for depot in depots}

        if globalConstants["general"]["LOG_ATTRIBUTES"]:
            self.logger = DataLogger(self.env, self, "BACKGROUNDSTORE")

    def run(self, depots):
        """Intialize vehicles based on data in eflips settings. Executed and
        completed at simulation start.

        depots: [list] of Depot objects
        """
        self._complete(depots)

        vehicle_count = globalConstants["depot"]["vehicle_count"]

        for depotID in vehicle_count:
            if depotID in self.map_depots:
                home_depot = self.map_depots[depotID]

                for vtID in vehicle_count[depotID]:
                    if vtID not in self.vIDCounter:
                        self.vIDCounter[vtID] = 0

                    for no in range(vehicle_count[depotID][vtID]):
                        self.vIDCounter[vtID] += 1

                        # Initialize vehicle object
                        vehicle = SimpleVehicle(
                            self.env,
                            vtID + " " + str(self.vIDCounter[vtID]),
                            next(
                                vt
                                for vt in globalConstants["depot"]["vehicle_types_obj"]
                                if vt.ID == vtID
                            ),
                            home_depot,
                        )

                        # Assign vehicle to the depot
                        self.put(vehicle)
                        home_depot.init_store.put(vehicle)

            else:
                warn(
                    "HomeDepotID '%s' did not match any ID of known depots." % depotID
                    + " Vehicle entry skipped."
                )

        self.check_arrival()

        # Calculate count and share of vehicles for vehicle types and vehicle
        # type groups
        for depotID in vehicle_count:
            depot = self.map_depots[depotID]
            total = sum(vehicle_count[depotID].values())

            for vtID, count in vehicle_count[depotID].items():
                vehicle_type = next(
                    vt
                    for vt in globalConstants["depot"]["vehicle_types_obj"]
                    if vt.ID == vtID
                )

                vehicle_type.count[depot] = count
                vehicle_type.share[depot] = count / total

        for vtg in globalConstants["depot"]["vehicle_type_groups"]:
            for vehicle_type in vtg.types:
                for depot in vehicle_type.count:
                    if depot not in vtg.count:
                        vtg.count[depot] = 0

                    vtg.count[depot] += vehicle_type.count[depot]

            for depot in vtg.count:
                total = sum(vehicle_type.count[depot] for vehicle_type in vtg.types)
                vtg.share[depot] = total / sum(vehicle_count[depot.ID].values())

    def check_arrival(self):
        for depot in self.map_depots.values():
            if depot.init_store.count > depot.default_plan[0].capacity:
                warn(
                    "The first default plan entry '%s' of depot '%s' should "
                    "have a capacity at least as high as the amount of "
                    "vehicles used in the depot." % (depot.default_plan[0].ID, depot.ID)
                )


class SimpleTrip:
    """Data for one trip outside the depot, from departure until arrival.

    Parameters:
    origin, destination: Depot ID [str] upon init. Is converted to [Depot]
        instance by Timetable
    vehicle_types: [list] of SimpleVehicle.VehicleType as [str] upon import,
        converted to [VehicleType] before simulation start.
    std: [int] scheduled time of departure in total seconds since sim start
    sta: [int] scheduled time of arrival in total seconds since sim start
    distance: [int] total trip distance in km
    start_soc, end_soc: [int or float or None] soc upon departure from and
        arrival at the depot. Must be provided if globalConstants['depot'][
        'consumption_calc_mode'] == 'soc_given'. May be omitted if consumption
        calculation is CR-based.
    charge_on_track: [bool] For the use with globalConstants['depot'][
        'consumption_calc_mode'] == 'soc_given'. If True, opportunity charging
        on track is assumed and start_soc is interpreted as sufficient soc for
        departure and end_soc is interpreted as fixed value upon arrival. If
        False, depot charging is assumed and start_soc and end_soc are used to
        determine the required energy. In this case, start_soc must be higher
        than end_soc. May be omitted if consumption calculation is CR-based.

    Attributes:
    eta: [int] scheduled time of arrival
    atd: [int or None] actual time of departure
    ata: [int or None] actual time of arrival
    vehicle: [SimpleVehicle] assigned to before and after departure
    reserved_for_init: see option prioritize_init_store in globalConstants
    vehicle_from: [str] ID of the area the executing vehicle was located at
        when calling checkout.
    is_copy: boolean flag to indicate if this trip is a copy of another trip
    t_match: [None or int] time of matching this trip and a vehicle. If a
        vehicle from a depot's init store is assigned, this attribute stays
        None. If the match changes, only the last matching time is saved.
    got_early_vehicle: [bool] True if the trip was served by a vehicle where
        the battery wasn't fully charged upon departure.
    periodic_trigger_scheduled: [bool] flag to prevent recursion when
        scheduling a dispatch trigger for delayed trips
    ID_orig: [str] ID of trip this trip is a copy from, if not an original

    """

    delay_event_cls = None
    """Event that succeeds if departure delay occurs. Must be set before 
    calling init to be applied."""

    def __init__(
        self,
        env,
        ID,
        line_name,
        origin,
        destination,
        vehicle_types,
        std,
        sta,
        distance,
        start_soc=None,
        end_soc=None,
        charge_on_track=False,
        is_copy=False,
    ):
        self.env = env
        self.ID = ID
        self.line_name = line_name
        self.origin = origin
        self.destination = destination
        self.vehicle_types = vehicle_types
        self.std = std
        self.sta = sta
        self.distance = distance
        self.start_soc = start_soc
        self.end_soc = end_soc
        self.charge_on_track = charge_on_track
        self.is_copy = is_copy

        if start_soc is not None and not charge_on_track and start_soc < end_soc:
            raise ValueError(
                "For depot chargers start_soc cannot be lower "
                "than end_soc. Trip: %s" % ID
            )

        self.eta = sta
        self.atd = None
        self.ata = None

        self.vehicle = None
        self.reserved_for_init = False
        self.vehicle_from = None

        self.t_match = None
        self.got_early_vehicle = False
        self.t_got_early_vehicle = None
        self.periodic_trigger_scheduled = False

        if self.delay_event_cls is not None:
            self.env.process(self.notify_delay())

    def __repr__(self):
        return "{%s} %s" % (type(self).__name__, self.ID)

    @property
    def vehicle_types_str(self):
        """Return IDs of self.vehicle_types in a list."""
        return [vt.ID for vt in self.vehicle_types]

    @property
    def vehicle_types_joinedstr(self):
        """Return the IDs of self.vehicle_types comma-separated in a single
        string.
        Example returns:
        ['EN'] -> 'EN'
        ['EN', 'DL'] -> 'EN, DL'
        """
        return ", ".join(vt.ID for vt in self.vehicle_types)

    @property
    def delay_departure(self):
        """Return the departure delay [int] in seconds. Return None until
        scheduled time of departure.
        """
        if self.env.now < self.std:
            return None
        else:
            if self.atd is None:
                # trip hasn't started yet and is due or delayed
                return self.env.now - self.std
            else:
                # trip has started
                return self.atd - self.std

    @property
    def delayed_departure(self):
        """Return True if the trip is delayed upon departure or False if not.
        Return None until scheduled time of departure.
        """
        delay = self.delay_departure
        if delay is None:
            return None
        else:
            return delay > 0

    @property
    def delay_arrival(self):
        """Return the arrival delay [int] in seconds. Return None until
        scheduled time of arrival.
        """
        if self.env.now < self.sta:
            return None
        else:
            if self.ata is None:
                return self.env.now - self.sta
            else:
                return self.ata - self.sta

    @property
    def delayed_arrival(self):
        """Return True if the trip is delayed upon arrival or False if not.
        Return None until scheduled time of arrival.
        """
        delay = self.delay_arrival
        if delay is None:
            return None
        else:
            return delay > 0

    @property
    def duration(self):
        """Return the scheduled duration [int] of the trip."""
        return self.sta - self.std

    @property
    def actual_duration(self):
        """Return the actual duration [int] of the trip. Return None until the
        trip is finished."""
        return self.ata - self.atd if self.ata is not None else None

    @property
    def lead_time_match(self):
        """Return the time interval [int] of how early the trip was matched
        with a vehicle. May be negative if the trip is delayed. Return None if
        self.t_match is None.
        """
        if self.t_match is None:
            return None
        else:
            return self.std - self.t_match

    def notify_due_departure(self, t):
        """Wait until simulation time *t* and then trigger the depot's
        assignment process if the trip has no vehicle. No action if *t* < now.
        """
        if t >= self.env.now:
            yield self.env.timeout(t - self.env.now)
            if self.vehicle is None:
                self.origin.depot_control.trigger_dispatch()

    def notify_delay(self):
        """Instantiate self.delay_event_cls if the trip hasn't started one
        second after self.std.
        """
        yield self.env.timeout(self.std + 1 - self.env.now)
        if self.atd is None:
            delay_event = self.delay_event_cls(self.env, self)
            delay_event.trip = self
            delay_event.succeed()


class Timetable:
    """Timetable with a list of SimpleTrip objects from imported data.
    Processes the list of trips and requests matching vehicles from a depot.

    Parameters:
        trips: [list] of SimpleTrip objects. Has to be sorted by departure time
        in ascending order. Only contains trips that are not copies.
    days_ahead: [int] number of days that trips will be issued early. For
        making depot-side planning possible. Should be higher than
        *interval_covered* (in days).

    Attributes:
    trips_issued: [list] of all trips issued to depots. Is empty at the start
        and filled during simulation.
    repetitions: [int] number of times the basic list of trips was
        copied during runtime
    interval_covered: [int] days in seconds that are covered by trips
    all_trips: working [list] of all trips after scheduling including copies
    reservations: {dict} helper variable for the prioritize_init_store option
    fully_reserved: [bool] helper variable for the prioritize_init_store option

    """

    def __init__(self, env, trips):
        self.env = env
        self.trips = trips
        self.repetitions = 0

        interval_covered_sharp = self.trips[-1].std - self.trips[0].std
        self.interval_covered = int(86400 * ceil(interval_covered_sharp / 86400))
        self.trips_issued = []

        self.all_trips = self.trips.copy()
        self.reservations = {}
        self.fully_reserved = False

    def _complete(self, depots):
        """Completion of trip instantiation that must take place before
        simulation start, but may not be possible during the depot
        configuration phase.
        """
        # Convert depot IDs of trips from str to actual Depot objects
        map_ids = {depot.ID: depot for depot in depots}
        for trip in self.trips:
            trip.origin = map_ids[trip.origin]
            trip.destination = map_ids[trip.destination]

        # Check if VehicleGenerator.complete has been called
        if "vehicle_types_obj" not in globalConstants["depot"]:
            raise ValueError(
                "VehicleGenerator.complete must be called before " "Timetable.complete."
            )

        # Check if trip data and vehicle data are valid
        for trip in self.trips:
            for vtID in trip.vehicle_types:
                if vtID not in globalConstants["depot"]["vehicle_types"]:
                    raise ValueError(
                        'vehicle_types "%s" of trip %s is not listed in'
                        % (vtID, trip.ID)
                        + " globalConstants['depot']['vehicle_types']"
                    )

        # Convert trip.vehicle_types from a list of str to a list of
        # VehicleType objects
        for trip in self.trips:
            trip.vehicle_types = [
                next(
                    vt
                    for vt in globalConstants["depot"]["vehicle_types_obj"]
                    if vt.ID == vtID
                )
                for vtID in trip.vehicle_types
            ]

        # Preparation for prioritize_init_store
        if globalConstants["depot"]["prioritize_init_store"]:
            self.reservations = {
                depotID: {
                    vtID: 0
                    for vtID in globalConstants["depot"]["vehicle_count"][depotID]
                }
                for depotID in globalConstants["depot"]["vehicle_count"]
            }
            self.reserve_trips(self.trips)

    def run(self, depots):
        """Assure that trips are issued at depots with a time buffer of
        self.days_ahead.
        Infinite loop that checks if there is enough buffer. If not, copy the
        basic list of trips, raise the time values and issue them at the
        depots.
        *depots*: [list] ob Depot objects. Must contain all depots that are
        mentioned as trip attribute.
        """
        self._complete(depots)
        self.issue_requests(self.trips)

        while True:
            yield self.env.timeout(self.interval_covered)

    def issue_requests(self, trips):
        """Issue requests for all trips in trips."""
        for trip in trips:
            self.issue_request(trip)
            self.trips_issued.append(trip)

    @staticmethod
    def issue_request(trip):
        """Issue request for vehicle at the trip's departure depot. The vehicle
        has to match *filter*. Waiting time is possible. Called in run().
        """
        # Please dont add restrictions here!
        # Go to dispatch strategies in depot.py instead.
        vf = VehicleFilter(filter_names=["trip_vehicle_match"], trip=trip)

        trip.origin.request_vehicle(trip, filter=vf)

    def reserve_trips(self, trips):
        """Mark the first trips equal to the amount of matching vehicles to
        be served by the depot's init store.
        Call only if option prioritize_init_store is on.
        """
        if not self.fully_reserved:
            for trip in trips:
                # look for the first best match
                for vt in trip.vehicle_types:
                    if (
                        self.reservations[trip.origin.ID][vt.ID]
                        < globalConstants["depot"]["vehicle_count"][trip.origin.ID][
                            vt.ID
                        ]
                    ):
                        self.reservations[trip.origin.ID][vt.ID] += 1
                        trip.reserved_for_init = True
                        # vehicle found, stop search for this trip
                        break
            flexprint(
                "reservations: %s" % self.reservations, env=self.env, switch="dispatch2"
            )

            # Check if there are any available vehicles left to possibly
            # shorten next method call (checking if init store is empty is not
            # safe if interval_covered is very small)
            for depotID in self.reservations:
                for vtID in self.reservations[depotID]:
                    if (
                        self.reservations[depotID][vtID]
                        < globalConstants["depot"]["vehicle_count"][depotID][vtID]
                    ):
                        break
                else:
                    continue
                # break outer loop if break was called in the inner loop
                break
            else:
                self.fully_reserved = True


class ExcelSheetData:
    """Methods to import an Excel sheet and save its data row-wise to a list.

    data: [list] containing the imported data with one entry per row.

    """

    def __init__(self, filename, sheetname):
        self.filename = filename
        self.sheetname = sheetname
        self.data = []

        self.import_data()

    def import_data(self):
        wb = open_workbook(self.filename)
        sheet = wb.sheet_by_name(self.sheetname)

        for row_no in range(sheet.nrows):
            row = sheet.row(row_no)
            row_values = []
            for col_no in range(len(row)):
                row_values.append(row[col_no].value)
            self.data.append(row_values)

    def check_for_same_length(self):
        """Return True if all rows have the same length, else False."""
        length = len(self.data[0])
        for row in self.data[1:]:
            if len(row) != length:
                return False
        return True

    def map_headers(self):
        headers = self.data[0]
        datamap = {}
        for no, header in enumerate(headers):
            datamap[header] = no
        return datamap


def timetabledata_from_excel(filename):
    """Load data from excel to use for timetable initialization. The loaded
    data can be reused for multiple identical independent timetables.
    Return an ExcelSheetData object.

    filename: [str] name of excel file including path, excluding file
    extension. Example:
        '..\\bvg_depot\\tripdata\\tripData_depot_I_vehicleType_EN_GN_DL'
    """
    filename += ".xlsx"
    return ExcelSheetData(filename, "Tripdata")


def timetable_from_timetabledata(env, timetabledata):
    """Initialize and return a Timetable instance with SimpleTrip objects
    based on *timetabledata*.

    timetabledata: [ExcelSheetData] from timetabledata_from_excel()
    """
    trips = timetabledata_to_trips(env, timetabledata)
    timetable = Timetable(env, trips)
    return timetable


def timetabledata_to_trips(env, timetabledata):
    """Convert route data from excel to a list of SimpleTrip objects usable by
    Timetable.

    timetabledata: [ExcelSheetData] object that contains a list of trips. Has
        to be sorted by std in ascending order because run() relies on
        this order. In init(), trips is created from this data.
    """
    assert timetabledata.check_for_same_length()
    datamap = timetabledata.map_headers()

    # Instantiate SimpleTrip objects from the Excel data
    trips = []
    for entry in timetabledata.data[1:]:
        # At least one of trip's vehicle_types entry must be specified in
        # globalConstants
        if any(
            [
                vt in globalConstants["depot"]["vehicle_types"].keys()
                for vt in entry[datamap["vehicle_types"]].split(", ")
            ]
        ):
            trips.append(
                SimpleTrip(
                    env,
                    str(entry[datamap["ID"]]),
                    str(entry[datamap["line_name"]]),
                    entry[datamap["origin"]],
                    entry[datamap["destination"]],
                    entry[datamap["vehicle_types"]].split(", "),
                    int(round(entry[datamap["std [s]"]])),
                    int(round(entry[datamap["sta [s]"]])),
                    entry[datamap["distance [km]"]],
                    entry[datamap["start_soc"]] if "start_soc" in datamap else None,
                    entry[datamap["end_soc"]] if "end_soc" in datamap else None,
                    entry[datamap["charge_on_track"]]
                    if "charge_on_track" in datamap
                    else False,
                )
            )
        else:
            raise ValueError(
                "No matching vehicle type for trip %s (filename=%s) is "
                "defined in globalConstants (filename=%s)."
                % (
                    entry[datamap["ID"]],
                    timetabledata.filename,
                    globalConstants["FILENAME_SETTINGS"],
                )
            )
    return trips
