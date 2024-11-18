import math

import numpy as np
from eflips.model import VehicleType, AreaType

from api.test_api import TestHelpers
from eflips.depot.api.private.depot import area_needed_for_vehicle_parking


class TestDepot(TestHelpers):
    def test_area_calc_line(self, session, full_scenario):
        vt = session.query(VehicleType).first()
        vt.length = 12
        vt.width = 2.35

        # The area should remain equal for +1…+6 vehicles, as one line is added for each 6 vehicles.
        area_size = area_needed_for_vehicle_parking(
            vt, area_type=AreaType.LINE, count=13
        )
        for i in range(14, 19):
            assert (
                area_needed_for_vehicle_parking(vt, area_type=AreaType.LINE, count=i)
                == area_size
            )

    def test_are_calc_direct_one(self, session, full_scenario):
        # We test by comparing the results with danial's formula, which should give the same results for zero spacing.
        vt = session.query(VehicleType).first()
        vt.length = 12
        vt.width = 2.35

        counts = range(1, 10)
        spacing = 0
        angle = 45

        for count in counts:
            width_danial = (
                math.sin(math.radians(angle)) * vt.width
                + math.sin(math.radians(angle)) * vt.length
            )
            length_danial = (
                math.sin(math.radians(angle)) * vt.length
                + math.sin(math.radians(angle)) * vt.width
                + (count - 1) * (2 * math.cos(math.radians(angle)) * vt.width)
            )
            area_danial = width_danial * length_danial

            area = area_needed_for_vehicle_parking(
                vt,
                area_type=AreaType.DIRECT_ONESIDE,
                count=count,
                spacing=spacing,
                angle=angle,
            )
            assert np.isclose(area, area_danial, rtol=1e-5)
