"""Initializers for population and attributes. Related helpers and constraints.
"""
from operator import attrgetter
from collections import Counter
import random
from copy import deepcopy
from abc import ABC
from deap import creator
from eflips.depot.layout_opt import packing
from eflips.depot.layout_opt.settings import OPT_CONSTANTS as OC


# Functions for precomputing


def get_capacity_max(visu_class, capacity_min, limit=200):
    """Determine an area type's maximum capacity within DEPOT_A, DEPOT_B (the
    b-dimension). Uses BinWithDistances.try_put as a lightweight packing test.
    Return the capacity [int] or None if not even the minimum fits.
    """
    dims = packing.BinWithDistances(
        OC["scenario"]["DEPOT_A"], OC["scenario"]["DEPOT_B"]
    )
    for c in range(capacity_min, limit + 1):
        candidate = visu_class(capacity=c)
        success, _ = dims.try_put(candidate)
        if not success:
            if c == capacity_min:
                # Not even the minimum fits
                return None
            else:
                # Previous fits
                return c - 1
    raise RuntimeError(
        "Determination of the max capacity for %s exceeded the "
        "limit of %d areas." % (visu_class, limit)
    )


def get_count_max(visu_class, capacity):
    """Determine the maximum number of areas of *visu_class* with *capacity*
    within DEPOT_A, DEPOT_B. Return the count [int] or None if not even one
    area fits and the BinWithDistances object populated with the max count.
    """
    dvisu = packing.BinWithDistances(
        OC["scenario"]["DEPOT_A"], OC["scenario"]["DEPOT_B"]
    )
    item = visu_class(capacity=capacity)
    dvisu.items.append(item)
    dvisu.pack()
    while dvisu.feasible:
        item = visu_class(capacity=capacity)
        dvisu.items.append(item)
        dvisu.repack()

    if len(dvisu.items) == 1:
        # Not even 1 area fits
        return None, dvisu
    else:
        # Remove the last item as it didn't fit
        dvisu.items.remove(item)
        # dvisu.repack()    # skipped, dvisu.feasible only seems False until calling repack()
        return len(dvisu.items), dvisu


def get_count_max_with_capacity_max(visu_class, capacity_min):
    """Determine the maximum number of areas of *visu_class* with maximum
    capacity within DEPOT_A, DEPOT_B (the a-dimension). Return the count [int]
    or None if not even one area fits and the BinWithDistances object populated
    with the max count.
    """
    capacity_max = get_capacity_max(visu_class, capacity_min)
    return get_count_max(visu_class, capacity_max)


def get_count_max_with_capacity_min(visu_class, capacity_min):
    """Determine the maximum number of areas of *visu_class* with minimum
    capacity within DEPOT_A, DEPOT_B. Return the count [int] or None if not
    even one area fits and the BinWithDistances object populated with the max
    count.
    """
    return get_count_max(visu_class, capacity_min)


# Initializers


class AreaPrototype(ABC):
    """Abstract base class for a simple area representation."""

    capacity_min = int()
    capacity_max = int()
    typename = str()
    visu_class = None

    def __init__(self, capacity):
        self.capacity = capacity
        self._visu = None  # take care: needs reset after mutation

    def __repr__(self):
        return "{%s} cap=%s" % (self.typename, self.capacity)

    @property
    def visu(self):
        if self._visu is None:
            self._visu = self.visu_class(capacity=self.capacity)
        return self._visu

    @visu.setter
    def visu(self, value):
        self._visu = value

    def to_other_type(self, other_type):
        """Split self. into the least amount of area prototype objects of
        *other_type* with a total capacity of at least self.capacity. Return a
        list of the generated objects.

        other_type: type of other area prototype
        """
        if other_type is type(self):
            return ValueError("Cannot convert to the same type.")

        result = []

        q = self.capacity // other_type.capacity_max
        for i in range(q):
            result.append(other_type(capacity=other_type.capacity_max))

        r = self.capacity % other_type.capacity_max
        if r:
            if r >= other_type.capacity_min:
                result.append(other_type(capacity=r))
            else:
                # unexact return value: capacity is increased to the mininmum
                result.append(other_type(capacity=other_type.capacity_min))

        return result

    def clone(self):
        """Return a new instance with same capacity and no visu."""
        new = type(self)(self.capacity)
        return new


class DSRPrototype(AreaPrototype):
    """Class for a simple DSR area representation."""

    capacity_min = 1
    capacity_max = get_capacity_max(packing.VisuDataDirectSingleRow, capacity_min)
    typename = "DSR"
    visu_class = packing.VisuDataDirectSingleRow

    def __init__(self, capacity):
        super(DSRPrototype, self).__init__(capacity)


class DSR_90Prototype(AreaPrototype):
    """Class for a simple DSR_90 area representation."""

    capacity_min = 1
    capacity_max = get_capacity_max(packing.VisuDataDirectSingleRow_90, capacity_min)
    typename = "DSR_90"
    visu_class = packing.VisuDataDirectSingleRow_90

    def __init__(self, capacity):
        super(DSR_90Prototype, self).__init__(capacity)


class DDRPrototype(AreaPrototype):
    """Class for a simple DDR area representation."""

    capacity_min = 2
    capacity_max = get_capacity_max(packing.VisuDataDirectDoubleRow, capacity_min)
    typename = "DDR"
    visu_class = packing.VisuDataDirectDoubleRow

    def __init__(self, capacity):
        super(DDRPrototype, self).__init__(capacity)


class LinePrototype(AreaPrototype):
    """Class for a simple Line area representation."""

    capacity_min = 2
    capacity_max = get_capacity_max(packing.VisuDataLine, capacity_min)

    typename = "L"
    visu_class = packing.VisuDataLine

    def __init__(self, capacity):
        super(LinePrototype, self).__init__(capacity)


OTHER_AREA_TYPES = {
    DSRPrototype: (DSR_90Prototype, DDRPrototype, LinePrototype),
    DSR_90Prototype: (DSRPrototype, DDRPrototype, LinePrototype),
    DDRPrototype: (DSRPrototype, DSR_90Prototype, LinePrototype),
    LinePrototype: (DSRPrototype, DSR_90Prototype, DDRPrototype),
}


# Do some precomputing

# Estimate the total maximum capacity (max with all areas of the same type
# with maximum capacity)
count_max_with_capacity_max_dsr, dims_dsr_cap_max = get_count_max_with_capacity_max(
    packing.VisuDataDirectSingleRow, DSRPrototype.capacity_min
)
(
    count_max_with_capacity_max_dsr_90,
    dims_dsr_90_cap_max,
) = get_count_max_with_capacity_max(
    packing.VisuDataDirectSingleRow_90, DSR_90Prototype.capacity_min
)
count_max_with_capacity_max_ddr, dims_ddr_cap_max = get_count_max_with_capacity_max(
    packing.VisuDataDirectDoubleRow, DDRPrototype.capacity_min
)
count_max_with_capacity_max_line, dims_line_cap_max = get_count_max_with_capacity_max(
    packing.VisuDataLine, LinePrototype.capacity_min
)
# count_max_single = max(count_max_dsr, count_max_ddr, count_max_line)
CAPACITY_MAX = max(
    dims_dsr_cap_max.count_inner,
    dims_dsr_90_cap_max.count_inner,
    dims_ddr_cap_max.count_inner,
    dims_line_cap_max.count_inner,
)

# Estimate the total maximum number of areas (max of all areas of the same
# type with minimum capacity)
count_max_with_capacity_min_dsr, dims_dsr_count_max = get_count_max_with_capacity_min(
    packing.VisuDataDirectSingleRow, DSRPrototype.capacity_min
)
(
    count_max_with_capacity_min_dsr_90,
    dims_dsr_90_count_max,
) = get_count_max_with_capacity_min(
    packing.VisuDataDirectSingleRow_90, DSR_90Prototype.capacity_min
)
count_max_with_capacity_min_ddr, dims_ddr_count_max = get_count_max_with_capacity_min(
    packing.VisuDataDirectDoubleRow, DDRPrototype.capacity_min
)
count_max_with_capacity_min_line, dims_line_count_max = get_count_max_with_capacity_min(
    packing.VisuDataLine, LinePrototype.capacity_min
)
COUNT_MAX = max(
    count_max_with_capacity_min_dsr,
    count_max_with_capacity_min_dsr_90,
    count_max_with_capacity_min_ddr,
    count_max_with_capacity_min_line,
)
COUNT_MIN = 1


def print_area_precomps():
    print(
        "Depot a: %d m, b: %d m"
        % (OC["scenario"]["DEPOT_A"], OC["scenario"]["DEPOT_B"])
    )
    print(
        "capacity_max_dsr: %d, count_max_dsr: %d, total slots: %d"
        % (
            dims_dsr_cap_max.items[0].count_inner,
            count_max_with_capacity_max_dsr,
            dims_dsr_cap_max.count_inner,
        )
    )
    print(
        "capacity_max_dsr_90: %d, count_max_dsr_90: %d, total slots: %d"
        % (
            dims_dsr_90_cap_max.items[0].count_inner,
            count_max_with_capacity_max_dsr_90,
            dims_dsr_90_cap_max.count_inner,
        )
    )
    print(
        "capacity_max_ddr: %d, count_max_ddr: %d, total slots: %d"
        % (
            dims_ddr_cap_max.items[0].count_inner,
            count_max_with_capacity_max_ddr,
            dims_ddr_cap_max.count_inner,
        )
    )
    print(
        "capacity_max_line: %d, count_max_line: %d, total slots: %d"
        % (
            dims_line_cap_max.items[0].count_inner,
            count_max_with_capacity_max_line,
            dims_line_cap_max.count_inner,
        )
    )


def init_random_area():
    """Return a new area prototype of random type with random capacity within
    bounds.
    """
    area_type = random.choice(
        [DSRPrototype, DSR_90Prototype, DDRPrototype, LinePrototype]
    )
    capacity = random.randint(area_type.capacity_min, area_type.capacity_max)
    return area_type(capacity)


class DepotPrototype:
    """Very basic depot representation."""

    def __init__(self):
        self.areas = []
        self._visu = None  # take care: needs reset after mutation
        self._ID = None
        self._ID_dm = None
        self.results = {"simulated": False}

    @property
    def visu(self):
        if self._visu is None:
            self._visu = packing.BinWithDistances(
                OC["scenario"]["DEPOT_A"], OC["scenario"]["DEPOT_B"], False
            )
            for area in self.areas:
                self._visu.items.append(area.visu)
        return self._visu

    @visu.setter
    def visu(self, value):
        self._visu = value

    @property
    def capacity(self):
        """Total parking area capacity."""
        return sum(area.capacity for area in self.areas)

    @property
    def ID(self):
        """Automatic ID based on self.areas."""
        if self._ID is None:
            self.ID = self.generate_ID()
        return self._ID

    @ID.setter
    def ID(self, value):
        self._ID = value

    def generate_ID(self):
        if self.areas:
            self.sort_areas()

            c = {
                DSRPrototype.typename: Counter(),
                DSR_90Prototype.typename: Counter(),
                DDRPrototype.typename: Counter(),
                LinePrototype.typename: Counter(),
            }
            for area in self.areas:
                c[area.typename][area.capacity] += 1

            ID = ""
            for typename, subcounter in c.items():
                for capacity, n in subcounter.items():
                    ID += "_" + str(n) + "x" + str(capacity) + typename
            ID = ID[1:]
            return ID
        else:
            return None

    @property
    def ID_dm(self):
        """ID with DSR and DDR merged."""
        if self._ID_dm is None:
            self.ID_dm = self.generate_ID_dm()
        return self._ID_dm

    @ID_dm.setter
    def ID_dm(self, value):
        self._ID_dm = value

    def generate_ID_dm(self):
        if self.areas:
            self.sort_areas()

            transl = {
                DSRPrototype.typename: "d",
                DSR_90Prototype.typename: "d",
                DDRPrototype.typename: "d",
                LinePrototype.typename: "l",
            }
            c = {
                DSRPrototype.typename: Counter(),
                DSR_90Prototype.typename: Counter(),
                DDRPrototype.typename: Counter(),
                LinePrototype.typename: Counter(),
            }
            for area in self.areas:
                c[area.typename][area.capacity] += 1

            # merge
            c[DSRPrototype.typename] += c[DDRPrototype.typename]
            del c[DDRPrototype.typename]

            ID = ""
            for typename, subcounter in c.items():
                for capacity, n in subcounter.items():
                    ID += "_" + str(n) + "x" + str(capacity) + transl[typename]
            ID = ID[1:]
            return ID
        else:
            return None

    def sort_areas(self):
        self.areas.sort(key=attrgetter("capacity"), reverse=True)
        self.areas.sort(key=attrgetter("typename"))

    def reset_results(self):
        """Reset of evaluation results that is required after changes such as
        crossover and mutation.
        """
        self.visu = None
        self.results.clear()
        self.results["simulated"] = False

    def __eq__(self, other):
        """Return True if DepotPrototype objects *self* and *other* have the
        same areas.
        """
        return self.ID == other.ID


def clone_depot(ind):
    """Return a new DepotPrototype instance with cloned areas.
    As function and not method to be applicable with map.
    """
    new = creator.Individual()
    new.areas = [area.clone() for area in ind.areas]
    # Keep the visu reference, simulation results and fitness for now. Must be
    # reset if anything changes before packing and simulation.
    new.visu = ind.visu
    new.results = deepcopy(ind.results)
    new.fitness = deepcopy(ind.fitness)
    return new


def init_random_depot(dcls):
    """Return a new DepotPrototype instance with a random amount of areas
    within bounds.
    """
    depot = dcls()
    area_count = random.randint(COUNT_MIN, COUNT_MAX)
    for i in range(area_count):
        depot.areas.append(init_random_area())
    return depot


if __name__ == "__main__":

    def _instantiate_areas():
        """Instantiate all possible areas within slot capacity bounds."""
        aid = {}
        ail = []
        for area_type in [DSRPrototype, DSR_90Prototype, DDRPrototype, LinePrototype]:
            aid[area_type] = {}
            for capacity in range(area_type.capacity_min, area_type.capacity_max + 1):
                aid[area_type][capacity] = area_type(capacity)
                ail.append(area_type(capacity))
        return aid, ail
