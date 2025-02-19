from dataclasses import dataclass
from math import ceil
from typing import Dict

import sqlalchemy.orm.session
from eflips.model import Area, Scenario, Depot

from eflips.depot import VehicleType


@dataclass
class DrivewayAndSpacing:
    """
    Driveway and spacing information.

    All numbers are in meters

    TODO: Verify that these are the values in VDV 822
    """

    side_by_side: float = 0.9
    """Distance between two buses that are side by side."""
    front_to_back: float = 0.5
    """Distance between the front of one bus and the back of the next bus."""

    # TODO: Validate the assumption that direct areas MUST be accessed without reversing

    #        TOP
    #          /
    #         /
    # LEFT   / /   RIGHT
    #         /
    #        /
    #     BOTTOM
    # Note: Buses enter and exit driving downwards

    direct_area_top = 10
    """The total driving space required from a bus to enter the topmost parking spot."""
    direct_area_bottom = direct_area_top
    """The total driving space required from a bus to exit the bottommost parking spot."""
    direct_area_left = 10
    """The total driving space required from a bus to exit onto the left."""
    direct_area_right = direct_area_left
    """The total driving space required from a bus to enter from the right."""

    #     TOP
    #       | |
    # LEFT  | | RIGHT
    #       | |
    #       | |
    #     BOTTOM
    # Note: Buses enter at the top, exit at the bottom

    line_area_top = 12
    """The total driving space required from a bus to enter the topmost parking spot."""
    line_area_bottom = line_area_top
    """The total driving space required from a bus to exit the bottommost parking spot."""
    line_area_left = side_by_side
    """The space needed to be kept free left of a line of buses."""
    line_area_right = line_area_left
    """The space needed to be kept free right of a line of buses."""


@dataclass
class CapacityEstimate:
    """This class is the capacity estimate for the number of areas needed for a given vehicle type at a given depot."""

    line_peak_util: int
    """
    The peak utilization of the line area.

    This is the maximum number of buses that will be parked in the line area.
    """

    line_length: int
    """
    The length of the line area.

    This is the number of buses that will be parked in the line area behind each other.
    """

    direct_count: int
    """
    The number of parking spots in direct areas needed.

    This is the same as the peak utilization of the direct area.
    """

    area_square_meters: float | None = None
    """
    The area needed in square meters.

    Is calculated by `capacity_estimation`. If you are manually creating this
    object, you can set it to `None`.
    """

    @property
    def line_count(self) -> int:
        """
        The count of rows of line areas needed.

        This is calculated by dividing the total number of buses by the `line_length`.
        """
        return ceil(self.line_peak_util / self.line_length)


def area_square_meters(
    area: Area, spacing_params: DrivewayAndSpacing = DrivewayAndSpacing()
) -> float:
    """
    For a given `Area` object, calculate the actual area needed in square meters.

    :param area: An `Area` object. Vehicle length and width will be taken from `Area.vehicle_type`. The area
    type and size will be taken directly from the `Area`. Note that `AreaType.DIRECT_TWOSIDE` is not supported.
    :return: The area required in square meters.
    """
    raise NotImplementedError("This function is not yet implemented")


def capacity_estimation(
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    spacing_params: DrivewayAndSpacing = DrivewayAndSpacing(),
) -> Dict[Depot, Dict[VehicleType, CapacityEstimate]]:
    """
    Find the capacity estimates for all depots in the scenario.

    This is done be TODO DANIAL SUMMARIZE HOW.

    :param scenario: A `Scenario` object. It must have the `Depot` objects for each depot and DRIVING `Event`s. So
                     the consumption simulation should have been run before calling this function.
    :param session:  An open database session.
    :param spacing_params: A `DrivewayAndSpacing` object that contains the driveway and spacing information. Default
                           values are taken from the VDV 822 standard.
    :return: A nested dictionary with the depots as the first key, the vehicle types as the second key, and the
             `CapacityEstimate` object as the value.
    """
    raise NotImplementedError("This function is not yet implemented")


def update_depot_capacities(
    scenario: Scenario,
    session: sqlalchemy.orm.session.Session,
    capacity_estimates: Dict[Depot, Dict[VehicleType, CapacityEstimate]],
):
    """
    Update the depots in the database from a dictionary of capacity estimates, as returned by `capacity_estimation`.

    :param scenario: A `Scenario` object. It must have `Depot` objects for each depot in the keys of `capacity_estimates`.
    :param session: An open database session.
    :param capacity_estimates: A nested dictionary with the depots as the first key, the vehicle types as the second key, and the
                               `CapacityEstimate` object as the value.
    :return:
    """
    raise NotImplementedError("This function is not yet implemented")
