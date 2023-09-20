from depot.api import VehicleType


class TestApiInput:
    def test_vehicle_type(self):
        """This method tests if a VehicleType object can be correctly successfully initialized and the curves are properly functioning"""

        vehicle_type = VehicleType("SB_DC", 100, 15.0, None, 0.8, 0.2, 1.0)

        assert isinstance(vehicle_type, VehicleType)

        # Test if charging curve can be initialized with a constant float
        assert isinstance(vehicle_type.charging_curve(0.5), float)

        assert vehicle_type.v2g_curve is None
        assert vehicle_type.soc_min == 0.2
        assert vehicle_type.soc_max == 0.8
        assert vehicle_type.soh == 1.0

        # Test if the charging curve can be successfully initialized with tuple of lists
        soc_list = [0, 0.8, 1]
        power_list = [0, 15, 0]

        vehicle_type = VehicleType(
            "SB_DC", 100, (soc_list, power_list), None, 0.8, 0.2, 1.0
        )
        assert isinstance(vehicle_type.charging_curve(0.5), float)

        # Test if the charging curve can be successfully initialized with dict
        charging_curve_dict = {0.0: 0.0, 0.8: 15.0, 1.0: 0.0}
        vehicle_type = VehicleType(
            "SB_DC", 100, charging_curve_dict, None, 0.8, 0.2, 1.0
        )
        assert isinstance(vehicle_type.charging_curve(0.5), float)
