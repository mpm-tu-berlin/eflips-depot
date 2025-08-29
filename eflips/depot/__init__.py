# 1st: Namespaced Package Compat., see https://packaging.python.org/en/latest/guides/packaging-namespace-packages/
__path__ = __import__("pkgutil").extend_path(__path__, __name__)

# 2nd: Importing of the eflips modules -> TODO Cleanups here to avoid all those imports


import eflips.depot.layout_opt
import eflips.depot.settings_config
from eflips.depot.configuration import DepotConfigurator
from eflips.depot.depot import (
    DepotWorkingData,
    BackgroundStore,
    Depot,
    DepotControl,
    BaseArea,
    DirectArea,
    LineArea,
    AreaGroup,
    ParkingAreaGroup,
    DefaultActivityPlan,
    SpecificActivityPlan,
)
from eflips.depot.evaluation import DepotEvaluation
from eflips.depot.filters import VehicleFilter
from eflips.depot.input_epex_power_price import InputReader, PowerFrame
from eflips.depot.processes import (
    ProcessStatus,
    EstimateValue,
    Serve,
    ChargeAbstract,
    Charge,
    ChargeSteps,
    ChargeEquationSteps,
    Standby,
    Repair,
    Maintain,
    Precondition,
)
from eflips.depot.resources import DepotResource, DepotChargingInterface, ResourceSwitch
from eflips.depot.simple_vehicle import (
    VehicleType,
    VehicleTypeGroup,
    SimpleVehicle,
    SimpleBattery,
)
from eflips.depot.simulation import (
    DepotHost,
    SimulationHost,
    Depotinput,
    BaseMultipleSimulationHost,
)
from eflips.depot.smart_charging import SmartCharging, ControlSmartCharging
from eflips.depot.standalone import VehicleGenerator, SimpleTrip, Timetable
from eflips.depot.validation import Validator


class DelayedTripException(Exception):
    def __init__(self):
        self._delayed_trips = []

    def raise_later(self, simple_trip):
        self._delayed_trips.append(simple_trip)

    @property
    def has_errors(self):
        return len(self._delayed_trips) > 0

    def __str__(self):
        trip_names = ", ".join(
            f"{trip.ID} originally departure at {trip.std}"
            for trip in self._delayed_trips
        )

        return (
            f"The following blocks/rotations are delayed. "
            f"Ignoring this error will write related depot events into database. However, this may lead to errors due "
            f"to conflicts with driving events: {trip_names}"
        )


class UnstableSimulationException(Exception):
    def __init__(self):
        self._unstable_trips = []

    def raise_later(self, simple_trip):
        self._unstable_trips.append(simple_trip)

    @property
    def has_errors(self):
        return len(self._unstable_trips) > 0

    def __str__(self):
        trip_names = ", ".join(trip.ID for trip in self._unstable_trips)
        return (
            f"The following blocks/rotations require a new vehicle. This suggests an unstable "
            f" simulation result, where a repeated schedule might require more vehicles: {trip_names}"
        )
