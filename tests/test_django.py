"""Test script for external django model usage"""
if __name__ == '__main__':
    import django
    from django.conf import settings
    import busmodel.settings

    if not settings.configured:
        settings.configure(default_settings=busmodel.settings)

    django.setup()


from busmodel.models import VehicleType

v1 = VehicleType(vehicle_type_id=1, vehicle_type_name="SB", effective_capacity="300.0",
                 opportunity_charging_possible="True", effective_charging_curve="0.1, 0.2",  #
                 effective_v2g_curve="0.3, 0.4", charging_efficiency="0.5",
                 minimal_charging_power="30.5", constant_evergy_consumption="10.2",
                 vehicle_length="8.7")
v1.save()
v2 = VehicleType(vehicle_type_id=2, vehicle_type_name="AB", effective_capacity="250.0",
                 opportunity_charging_possible="False", effective_charging_curve="0.3, 0.3",
                 effective_v2g_curve="0.4, 0.4", charging_efficiency="0.45",
                 minimal_charging_power="35.5", constant_evergy_consumption="15.4",
                 vehicle_length="14.3")
v2.save()
sb = VehicleType.objects.get(vehicle_type_id=1)
ab = VehicleType.objects.get(vehicle_type_id=2)

print(ab)
print(sb)
