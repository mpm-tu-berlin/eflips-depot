from eflips.depot.api.input import VehicleType as ApiVehicleType
from eflips.model import VehicleType as ModelVehicleType


class VehicleType(ApiVehicleType):
    @staticmethod
    def _validate(vehicle_type: ModelVehicleType) -> bool:
        # TODO complete validation

        assert (
            vehicle_type.battery_capacity > 0
        ), "Battery capacity must be greater than 0"
        assert all(
            i >= 0 for i in vehicle_type.charging_curve[0]
        ), "soc must be positive"
        assert all(
            i <= 1 for i in vehicle_type.charging_curve[0]
        ), "soc must be less than 1"

    @classmethod
    def from_eflips_db(cls, vehicle_type: ModelVehicleType) -> "VehicleType":
        cls._validate(vehicle_type)
        return cls(
            id=vehicle_type.name,
            # TODO deal with vehicle class later
            vehicle_class="",
            battery_capacity_total=vehicle_type.battery_capacity,
            charging_curve=(
                vehicle_type.charging_curve[0],
                vehicle_type.charging_curve[1],
            ),
            v2g_curve=(vehicle_type.v2g_curve[0], vehicle_type.v2g_curve[1])
            if vehicle_type.v2g_curve
            else None,
        )
