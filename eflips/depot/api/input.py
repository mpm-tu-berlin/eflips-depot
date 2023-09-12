"""Read and pre-process data from database"""
import simpy
import json
import eflips.depot

from datetime import datetime
from dataclasses import dataclass

from ebustoolbox.models import Trip, Rotation, VehicleClass, VehicleType
from eflips.depot.standalone import SimpleTrip
from eflips.depot.simple_vehicle import SimpleVehicle


def get_rotation_from_database(rid: int) -> Rotation:
    """this method return data units from rotation-table"""

    current_rotation = Rotation.objects.filter(id=rid).all()
    return current_rotation[0]


def get_start_time(dt):
    """Temporarily take 0 am of the earliest day in the trip schedule as start of the simulation

    :return: 0 am of the earliest day in the trip schedule
    :rtype: datetime
    """

    # TODO: check timezone
    return datetime(dt.year, dt.month, dt.day, 0, 0, 0, tzinfo=dt.tzinfo)


class RotationFromDatabase:
    """
    TODO: actually rotation from database and simba. Change the name of this class later
    """

    def __init__(self, query_id):
        self.id = int(query_id)
        self.name = None
        self.vehicle_class = None
        self.scenario = None
        self.vehicle_type = None
        self.departure_soc = None
        self.arrival_soc = None
        self.minimal_soc = None
        self.delta_soc = None
        self.charging_type = None

    # TODO: move this and the next method to __init__()
    def read_data_from_database(self):
        """This method reads data from database according to given rotation id"""

        current_rotation = get_rotation_from_database(self.id)
        self.name = current_rotation.id
        self.vehicle_class = current_rotation.vehicle_class
        self.scenario = current_rotation.scenario  # TODO: do we need this?

    def read_data_for_simba(self, simba_result):
        """This method reads simba output according to given rotation id"""

        self.departure_soc = simba_result["departure_soc"]
        self.arrival_soc = simba_result["arrival_soc"]
        self.minimal_soc = simba_result["minimal_soc"]
        self.delta_soc = (
            simba_result["delta_soc"] if "delta_soc" in simba_result else None
        )
        self.charging_type = simba_result["charging_type"]
        self.vehicle_type = simba_result["vehicle_type"]

    def vehicle_class(self):
        vehicle_class_query = VehicleClass.objects.filter(id=self.vehicle_class).all()
        return vehicle_class_query[0]

    # @property
    # def departure_soc(self):
    #     return self.departure_soc
    #
    # @property
    # def arrival_soc(self):
    #     return self.arrival_soc
    #
    # @property
    # def minimal_soc(self):
    #     return self.minimal_soc
    #
    # @property
    # def delta_soc(self):
    #     return self.delta_soc
    #
    # @property
    # def charging_type(self):
    #     return self.charging_type

    def vehicle_types(self) -> list:
        """Return a list of vehicle types for this rotation. Prioritize reading from simBA output, if available."""
        if self.vehicle_type is not None:
            return [self.vehicle_type]
        else:
            return [
                v.name
                for v in VehicleType.objects.filter(vehicle_class_id=self.vehicle_class)
            ]

    def get_eflips_input(self):
        pass

    def get_trips(self):
        return VehicleClass.objects.filter(rotation_id=self.id).all()


def get_simba_output(eflips_input_path):
    """This method get the simba output data from json file...
    Should call after run_ebus_toolbox()


    """
    # read json files
    with open(eflips_input_path, "r") as f:
        simba_output = json.load(f)
    return simba_output


def create_rotation_from_simba_output(eflips_input_path) -> list:
    """This method creates a list of RotationFromDatabase object from simba output data"""
    simba_output = get_simba_output(eflips_input_path)
    rotation_list = []
    for rotation_id, results in simba_output.items():
        rotation = RotationFromDatabase(rotation_id)
        rotation.read_data_for_simba(results)
        rotation.read_data_from_database()
        rotation_list.append(rotation)

    return rotation_list


def read_timetable(
    env: simpy.Environment, rotation_from_simba
) -> list:  # or better return Timetable?
    """The method reads trips from database and returns a list of SimpleTrip for configuration a DepotEvaluation object

    Parameters:
    :param env: simulation environment object
    :type env: simpy.Environment
    """

    # Use 0am of the first trip date as simulation 0-second. Might be changed later

    rotations = []

    r_ids = []
    for rotation in rotation_from_simba:
        r_ids.append(rotation.id)

    trips_from_rotations = (
        Trip.objects.filter(rotation_id__in=r_ids).order_by("departure_time").all()
    )
    start_trip = trips_from_rotations[0]
    start_time = get_start_time(start_trip.departure_time)

    for rotation in rotation_from_simba:  # rotations...
        trips = (
            trips_from_rotations.filter(rotation_id=rotation.id)
            .order_by("departure_time")
            .all()
        )

        first_trip = trips.first()
        start_station = first_trip.departure_stop_id
        last_trip = trips.last()
        last_station = last_trip.arrival_stop_id
        distance = 0.0
        for t in trips:
            distance += t.distance

        rotations.append(
            SimpleTrip(
                env,
                str(rotation.id),
                first_trip.line,  # might be changed later
                str(start_station),
                str(last_station),
                rotation.vehicle_types(),  # need to see where vehicle_types will be converted to SimpleVehicle
                int((first_trip.departure_time - start_time).total_seconds()),
                int((last_trip.arrival_time - start_time).total_seconds()),
                # a calculation from timestamp -> seconds after simulation begin
                distance,  # sum of all trips
                rotation.departure_soc,
                rotation.arrival_soc,
                rotation.charging_type == "oppb",
            )
        )

    return rotations
