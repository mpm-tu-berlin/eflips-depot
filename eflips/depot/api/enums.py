from enum import Enum, auto


class ProcessType(Enum):
    """This class represents the types of a process in eFLIPS-Depot."""

    SERVICE = auto()
    """This process represents a bus service by workers. It does not require a charging_power and has a fixed 
    duration."""
    CHARGING = auto()
    """This process represents a bus charging process. It requires a charging_power and has no fixed duration."""
    STANDBY = auto()
    """This process represents an arriving bus that is waiting for a service. It does not require a charging_power 
    and has no fixed duration."""
    STANDBY_DEPARTURE = auto()
    """This process represents a bus ready for departure. It does not require a charging_power and has no fixed 
    duration."""
    PRECONDITION = auto()
    """This process represents a bus preconditioning process. It requires a charging_power and has a fixed duration."""


class AreaType(Enum):
    """This class represents the type of an area in eFLIPS-Depot"""

    DIRECT_ONESIDE = auto()
    """A direct area where vehicles drive in form one side only."""

    DIRECT_TWOSIDE = auto()
    """A direct area where vehicles drive in form both sides. Also called a "herringbone" configuration."""

    LINE = auto()
    """A line area where vehicles are parked in a line. There might be one or more rows in the area."""
