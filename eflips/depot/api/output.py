"""Classes repre

"""
from dataclasses import dataclass
from eflips.depot import SimpleVehicle
from eflips.depot.standalone import SimpleTrip


@dataclass
class InputForSimba:
    """Input Data for Simba

    :param rotation_id: ID of rotation
    :type rotation_id: int
    :param vehicle_id: ID of vehicle
    :type vehicle_id: str
    :param soc_departure: soc at departure of each vehicle
    :type soc_departure: float
    """
    rotation_id: int
    vehicle_id: str
    soc_departure: float


class SimpleTripOutput(SimpleTrip):
    """

    Parameters:
    :param origin, destination: Depot ID of departure and destination depot upon init. Is converted to :class:`depot.depot.Depot`
        instance by Timetable
    :type origin, destination: str
    :param vehicle_types: list of :class:`eflips.depot.simple_vehicle.VehicleType` as str upon import,
        converted to :class:`eflips.depot.simple_vehicle.VehicleType` before simulation start.
    :type vehicle_types: list
    :param std: scheduled time of departure in total seconds since sim start
    :type std: int
    :param sta: scheduled time of arrival in total seconds since sim start
    :type sta: int
    :param distance: total trip distance in km
    :type distance: int, float
    :param start_soc, end_soc: soc upon departure from and
        arrival at the depot. Must be provided if globalConstants['depot'][
        'consumption_calc_mode'] == 'soc_given'. May be omitted if consumption
        calculation is CR-based.
    :type start_soc, end_soc: int, float, None
    :param charge_on_track: For the use with globalConstants['depot'][
        'consumption_calc_mode'] == 'soc_given'. If True, opportunity charging
        on track is assumed and start_soc is interpreted as sufficient soc for
        departure and end_soc is interpreted as fixed value upon arrival. If
        False, depot charging is assumed and start_soc and end_soc are used to
        determine the required energy. In this case, start_soc must be higher
        than end_soc. May be omitted if consumption calculation is CR-based.
    :type charge_on_track: bool

    Attributes:
    :param eta: scheduled time of arrival
    :type eta: int
    :param atd: actual time of departure
    :type atd: int or None
    :param ata: actual time of arrival
    :type ata: int or None
    :param vehicle: :class:`eflips.depot.simple_vehicle.SimpleVehicle` assigned to before and after departure
    :type vehicle: :class:`eflips.depot.simple_vehicle.SimpleVehicle`
    :param reserved_for_init: see option prioritize_init_store in globalConstants
    :type reserved_for_init: bool
    :param vehicle_from: ID of the area the executing vehicle was located at
        when calling checkout.
    :type vehicle_from: str
    :param copy_of: Is set to :class:`eflips.depot.standalone.SimpleTrip` object if trip was
        copied in Timetable.repeat_trips.
    :type copy_of: None or :class:`eflips.depot.standalone.SimpleTrip`
    :param t_match: time of matching this trip and a vehicle. If a
        vehicle from a depot's init store is assigned, this attribute stays
        None. If the match changes, only the last matching time is saved.
    :type t_match: None or int
    :param got_early_vehicle: [bool] True if the trip was served by a vehicle where
        the battery wasn't fully charged upon departure.
    :type got_early_vehicle: bool
    :param periodic_trigger_scheduled: bool flag to prevent recursion when
        scheduling a dispatch trigger for delayed trips
    :type periodic_trigger_scheduled: bool
    :param ID_orig: str ID of trip this trip is a copy from, if not an original
    :type ID_orig: str
    """

    def __init__(self, trip):
        super().__init__(None, trip.ID, trip.line_name, trip.origin.ID,
                         trip.destination.ID, trip.vehicle_types,
                         trip.std, trip.sta, trip.distance, trip.start_soc,
                         trip.end_soc, trip.charge_on_track)
        # TODO: find a better way to decouple env
        # TODO do we need all these properties?
        self.scheduled_time_arrival = trip.eta
        self.actual_time_departure = trip.atd
        self.actual_time_arrival = trip.ata

        # TODO: better way to pack vehicle?
        self.vehicle = trip.vehicle

        # I don't think these are necessary for the users. Reserved for later
        # self.reserved_for_init = trip.reserved_for_init
        # self.copy_of = trip.copy_of
        # self.t_match = trip.t_match

        self.vehicle_from = trip.vehicle_from

        # Battery related
        self.got_early_vehicle = trip.got_early_vehicle
        self.t_got_early_vehicle = trip.t_got_early_vehicle

    # TODO: add @property for each important property necessary?

    # @property
    # def origin_depot(self):
    #     """
    #     TODO: return an object which can represent depot better, or only return an ID and try to
    #     implement depot otherwise
    #     """
    #     pass
    # @property
    #
    # def destination_depot(self):
    #     """TODO:
    #
    #     """
    #     pass
    # @property
    # def vehicle(self):
    #     """
    #     TODO: return assigned vehicle type
    #     """
    #     pass

    @property
    def delay_departure(self):
        """Return the departure delay in seconds. Return 0 if not delayed

        :return: delayed time in seconds
        :rtype: int
        """

        return self.atd - self.std

    @property
    def delay_arrival(self):
        """Return the departure delay in seconds. Return 0 if not delayed

        :return: delayed time in seconds
        :rtype: int
        """

        return self.ata - self.sta

    @property
    def duration(self):
        """Return the scheduled duration of the trip. Must greater than 0

        :return: scheduled duration time in seconds
        :rtype: int
        """
        return self.sta - self.std

    @property
    def actual_duration(self):
        """Return the actual duration of the trip. Must greater than 0


        :return: actual duration time of a trip
        :rtype: int
        """
        return self.ata - self.atd
