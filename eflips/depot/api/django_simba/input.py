"""

This module contains the classes to process the output of django-simba into the input of eFLIPS-Depot. Please note that
`import as` statements are used to distinguish between classes with the same name from eFLIPS-Depot and django-simba.

"""
import json
import os
from typing import List, Union, Dict, Any

from eflips.depot.api.input import VehicleType as ApiVehicleType
from eflips.depot.api.input import VehicleSchedule as ApiVehicleSchedule

from ebustoolbox.models import VehicleType as DjangoSimbaVehicleType, Rotation, Trip


class VehicleType(ApiVehicleType):
    """
    This class represents a vehicle type in eFLIPS-Depot. It is a subclass of
    :class:`eflips.depot.api.input.VehicleType` and overrides the :meth:`__init__()` method to read the data from the
    django-simba database.
    """

    def __init__(self, vehicle_type: DjangoSimbaVehicleType):
        """
        Create a new VehicleType object from a django-simba VehicleType object.
        :param vehicle_type: A django-simba VehicleType object.
        """

        self.id = str(vehicle_type.id)
        vehicle_classes = [vc.id for vc in vehicle_type.vehicle_class.all()]
        assert (
            len(vehicle_classes) == 1
        ), "We do not support multiple vehicle classes yet"
        self.vehicle_class = str(vehicle_classes[0])
        self.battery_capacity_total = vehicle_type.battery_capacity
        self.charging_curve = tuple(zip(*vehicle_type.charging_curve))
        if vehicle_type.v2g:
            self.v2g_curve = tuple(zip(*vehicle_type.v2g_curve))
        else:
            self.v2g_curve = None

        self.__post_init__()


class VehicleSchedule(ApiVehicleSchedule):
    """
    This class represents a vehicle schedule in eFLIPS-Depot. It is a subclass of
    :class:`eflips.depot.api.input.VehicleSchedule` and overrides the :meth:`__init__()` method to read the data from the
    django-simba database.
    """

    @classmethod
    def from_rotations(
        cls, input_path: Union[str, bytes, os.PathLike]
    ) -> List["VehicleSchedule"]:
        """
        Create a new VehicleSchedule object from a django-simba Rotation object.
        :param input_path: A Path-like object pointing to the input file. The input file format is specified below

        The input file format is as follows: It should be a JSON file containing a dictionary, with the key being a
        "rotation ID" for the database. The format of the dictionary for each rotation is specified in the __init__()
        method.

        :param input_path: A Path-like object pointing to the input file. The input file format is specified below.
        :return: a list of VehicleSchedule objects.
        """
        with open(input_path, "r") as f:
            input_data = json.load(f)

        result = []
        for rotation_id, rotation_info in input_data.items():
            rotation_id = int(rotation_id)
            result.append(VehicleSchedule(rotation_id, rotation_info))
        return result

    def __init__(self, rotation_id: int, rotation_info: Dict[str, Any]):
        """
        :param rotation_id: The ID of the rotation in the database.
        :param rotation_info: A dictionary containing the information about the rotation. It is specified as follows:

        This dictionary for each rotation contains the following keys:

        - `charging_type`: The charging type of the vehicle during the rotation. This is a string, either "oppb" for
            for vehicles that charge outside of the depot, or "depb" for vehicles that only charge in the depot.
        - `departure_soc`: The state of charge of the vehicle at the departure from the depot. This is a float between
            0 and 1.
        - `arrival_soc`: The state of charge of the vehicle at the arrival at the depot. This is a float between
            0 and 1. Only provided for oppb vehicles, 'null' for depb vehicles.
        - `minimal_soc`: The minimal state of charge of the vehicle during the rotation. This is a float between
            0 and 1. Only provided for oppb vehicles, 'null' for depb vehicles.
        - `delta_soc`: A list of floats, representing the discharge of the vehicle during the rotation. The list
        contains one float for each vehicle type. Only provided for depb vehicles, not given for oppb vehicles.
        - `vehicle_type`: An integer (oppb) or a list of integers (depb), representing the vehicle type(s) of the
            vehicle during the rotation. The integers correspond to the vehicle types in the database.

        """

        # Validate the input file
        self._validate_input_data(rotation_id, rotation_info)

        # Fill in the values from the rotation_info dictionary
        self.id = str(rotation_id)
        self.departure_soc = rotation_info["departure_soc"]

        self.arrival_soc = {}
        if rotation_info["charging_type"] == "depb":
            # For the depb buses, arrival_soc is not provided, so we have to calculate it from the departure_soc and
            # delta_soc
            for i in range(len(rotation_info["vehicle_type"])):
                vehicle_type_key = str(rotation_info["vehicle_type"][i])
                delta_soc = rotation_info["delta_soc"][i]
                arrival_soc = rotation_info["departure_soc"] - delta_soc
                self.arrival_soc[vehicle_type_key] = arrival_soc
        elif rotation_info["charging_type"] == "oppb":
            # For the oppb buses, arrival_soc is provided, so we can just use that
            # And there is only one vehicle type
            vehicle_type_key = str(rotation_info["vehicle_type"])
            self.arrival_soc[vehicle_type_key] = rotation_info["arrival_soc"]
        else:
            raise AssertionError("Invalid charging_type")

        if rotation_info["charging_type"] == "oppb":
            self.minimal_soc = rotation_info["minimal_soc"]
            self.opportunity_charging = True
        else:
            self.minimal_soc = None
            self.opportunity_charging = False

        self._fill_in_from_database()

    def _fill_in_from_database(self):
        """
        This method takes a populated VehicleSchedule object and fills in the remaining values from the database.

        :return: Nothing.
        """

        # Load the rotation from the database
        rotation = Rotation.objects.get(id=self.id)
        self.vehicle_class = str(rotation.vehicle_class.id)

        # Load the arrival and departure times by looking through the trips
        trips = (
            Trip.objects.filter(rotation_id=self.id).order_by("departure_time").all()
        )  # TODO: Can we filter by rotation_id?

        first_trip = trips.first()
        start_station = first_trip.departure_stop_id
        last_trip = trips.last()
        last_station = last_trip.arrival_stop_id
        assert (
            first_trip.departure_stop == last_trip.arrival_stop
        ), "First trip departure stop does not match last trip arrival stop"

        self.departure = first_trip.departure_time
        self.arrival = last_trip.arrival_time

    @staticmethod
    def _validate_input_data(rotation_id: int, rotation_info: Dict[str, Any]) -> bool:
        """
        This method validates whether a given rotation_info dictionary is valid. The rotation_info dictionary is
        one of the values from the django-simBA rotation JSON. The rotation_id is the key of the rotation_info
        dictionary.

        :param rotation_id: The ID of the rotation in the database.
        :param rotation_info: A dictionary containing the information about the rotation. It is specified in the
            __init__() method.
        :return: True if the input file is valid. Throws an AssertionError if the input file is invalid.
        """

        assert isinstance(rotation_id, int), "Rotation ID is not an integer"
        assert isinstance(rotation_info, dict), "Rotation info is not a dictionary"

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

        # For each rotation, the vehicle_type in the rotation_info should be either a string or a list of strings,
        # dependeing on the number of VehicleTypes for the VehicleClass fot the rotation
        rotation = Rotation.objects.get(id=rotation_id)

        # For now, we are only allowing on vehicle type per vehicle class, which simplifies the handling here
        assert isinstance(rotation_info["vehicle_type"], int) or isinstance(
            rotation_info["vehicle_type"], list
        ), "vehicle_type is not an int or list"
        if isinstance(rotation_info["vehicle_type"], list):
            vehicle_type_id = rotation_info["vehicle_type"][0]
        else:
            vehicle_type_id = rotation_info["vehicle_type"]
        vehicle_type_from_database = DjangoSimbaVehicleType.objects.get(
            id=vehicle_type_id
        )
        vehicle_class_for_type = [
            vt.id for vt in vehicle_type_from_database.vehicle_class.all()
        ]
        assert (
            len(vehicle_class_for_type) == 1
        ), "We do not support multiple vehicle classes yet"
        vehicle_class_for_type = vehicle_class_for_type[0]

        vehicle_class_for_rotation = rotation.vehicle_class.id

        assert (
            vehicle_class_for_type == vehicle_class_for_rotation
        ), "Vehicle type does not match vehicle class"

        # Depending on the charging type, we are either looking for the "delta_soc" for depot chargers ("depb")
        # or for "minimal_soc" for opportunity chargers ("oppb")
        if rotation_info["charging_type"] == "depb":
            assert "delta_soc" in rotation_info, "delta_soc not found in rotation_info"
            assert isinstance(rotation_info["delta_soc"], float) or isinstance(
                rotation_info["delta_soc"], list
            ), "delta_soc is not a float or list"
            if isinstance(rotation_info["delta_soc"], list):
                assert len(rotation_info["delta_soc"]) == len(
                    rotation_info["vehicle_type"]
                ), "delta_soc list has different length than vehicle_type list"
                for delta_soc in rotation_info["delta_soc"]:
                    assert isinstance(
                        delta_soc, float
                    ), "delta_soc list contains non-float value"
        elif rotation_info["charging_type"] == "oppb":
            assert (
                "minimal_soc" in rotation_info
            ), "minimal_soc not found in rotation_info"
            assert isinstance(rotation_info["minimal_soc"], float) or isinstance(
                rotation_info["minimal_soc"], list
            ), "minimal_soc is not a float or list"
            if isinstance(rotation_info["minimal_soc"], list):
                assert len(rotation_info["minimal_soc"]) == len(
                    rotation_info["vehicle_type"]
                ), "minimal_soc list has different length than vehicle_type list"
                for minimal_soc in rotation_info["minimal_soc"]:
                    assert isinstance(
                        minimal_soc, float
                    ), "minimal_soc list contains non-float value"

        return True
