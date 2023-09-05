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
