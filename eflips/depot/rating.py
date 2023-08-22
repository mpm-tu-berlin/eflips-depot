# -*- coding: utf-8 -*-
"""
Components to model multi criteria decision problems. Application for park- and
dispatch strategies.

"""
import numpy as np
from abc import ABC, abstractmethod
import eflips


# Settings

# For ParkRating
rfd_diff_park_lower_bound = -1200  # s
rfd_diff_park_upper_bound = 7200  # s
park_rating_weights = {
    "buffer": 22 / 72,  # PM 7/22
    "typestack": 13 / 72,  # PM 2/11
    "rfd_diff_pos": 8 / 72,  # PM 7/66
    "rfd_diff_neg": 29 / 144,  # PM 5/22
    "available_power": 2 / 72,  # PM 1/66
    "empty_slots_exit": 25 / 144,  # PM5/33
}

# For DispatchRating
rfd_diff_dispatch_upper_bound = 10800  # s
dispatch_rating_weights = {
    "buffer": 10 / 50,  # PM 1/5
    "typestack": 16 / 50,  # PM 17/45
    "rfd_diff": 25 / 100,  # PM 13/45
    "available_power": 3 / 50,  # PM 1/45
    "empty_slots_exit": 17 / 100,  # PM 1/9
}


class Rating:
    """Data container with methods to solve multi criteria decision problems.

    Parameters:
    alternatives: [list or 2D numpy.ndarray] of lists that represent an
        alternative. Alternatives must contain numerical data and have the same
        length. Is converted to a 2D numpy.ndarray during init.
    weights: [list or 1D numpy.ndarray or None] of factors for weighting values
        of an alternative. Is converted to a 1D numpy.ndarray during init.

    Attributes:
    best_value: [int or float] the best value after solving.
    best_alternative_nos: [tuple] from np.where with the row indices of the
        best alternatives in alternatives. E.g.: (array([2], dtype=int64),)
    best_alternatives: [list] of the best alternatives.

    """

    def __init__(self, alternatives, weights=None):
        self.alternatives, self.weights = self.check_input(alternatives, weights)
        self.weighted = None
        self.sums = None
        self.best_value = None
        self.best_alternative_nos = None
        self.best_alternatives = None

    @staticmethod
    def check_input(alternatives, weights):
        """Check alternatives and weights for validity."""
        if not isinstance(alternatives, list) or not all(
            isinstance(alt, list) for alt in alternatives
        ):
            raise ValueError(
                "Rating parameter 'alternatives' must be a list " "of lists."
            )
        if not alternatives:
            raise ValueError("Rating parameter 'alternatives' cannot be " "empty.")
        len_first = len(alternatives[0])
        if not all(len(i) == len_first for i in alternatives):
            raise ValueError(
                "Entries in Rating parameter 'alternatives' must"
                " have the same length."
            )
        if weights is not None:
            if not isinstance(weights, list) or not len(weights) == len_first:
                raise ValueError(
                    "Rating parameter 'weights' must be a list with the same "
                    "length as entries in 'alternatives'."
                )
        return np.asarray(alternatives), np.asarray(weights)

    def weighted_sum(self):
        """Locate the maximum weighted sums in self.alternatives."""
        if self.weights is None:
            raise ValueError("weights must be supplied for weighted_sum")

        self.weighted = self.weights * self.alternatives
        self.sums = self.weighted.sum(axis=1)
        self.best_value = self.sums.max()
        self.best_alternative_nos = np.where(self.sums == self.best_value)
        self.best_alternatives = self.alternatives[self.best_alternative_nos].tolist()


class BaseCriterion(ABC):
    """Base class for a criterion.
    Subclasses must implement attibute 'value' and method 'calculate'.

    """

    @abstractmethod
    def calculate(self, *args, **kwargs):
        """Determine self.value from inputs."""


class BufferPark(BaseCriterion):
    """Criterion for ParkRating.
    Exclusive for Direct areas. Value is 0 for Line.

    """

    def __init__(self, area, vehicle):
        self.value = self.calculate(area, vehicle)

    def calculate(self, area, vehicle):
        if not isinstance(area, eflips.depot.DirectArea):
            return 0

        # Check if Direct areas are all emtpy
        count_direct = sum(
            sum(a.count for a in g.direct_areas) for g in area.depot.parking_area_groups
        )
        if count_direct == 0:
            return 1

        total_buffer_capacity = area.depot.parking_capacity_direct

        if vehicle.vehicle_type.group:
            count = sum(
                sum(
                    v.vehicle_type.group is vehicle.vehicle_type.group
                    for v in a.items
                    if v
                )
                for a in area.parking_area_group.direct_areas
            )
            share_target = vehicle.vehicle_type.group.share[area.depot]
            # print('group. count: %d, share_target: %f' % (count, share_target))
        else:
            count = sum(
                sum(v.vehicle_type is vehicle.vehicle_type for v in a.items if v)
                for a in area.parking_area_group.direct_areas
            )
            share_target = vehicle.vehicle_type.share[area.depot]
            # print('lone. count: %d, share_target: %f' % (count, share_target))

        share = count / total_buffer_capacity

        result = share < share_target
        # print('BufferPark for vehicle %s: count=%s, total=%s, share=%s, share_target=%s'
        #       % (vehicle.ID, count, total_buffer_capacity, share, share_target))
        if result:
            return 1
        else:
            return 0


class TypestackPark(BaseCriterion):
    """Criterion for ParkRating.
    Exclusive for Line areas. Value is 0 for Direct.

    """

    def __init__(self, area, vehicle):
        self.value = self.calculate(area, vehicle)

    def calculate(self, area, vehicle):
        if not isinstance(area, eflips.depot.LineArea):
            return 0

        result = area.istypestack_with(vehicle)
        if result is None:
            return 0
        elif result:
            return 1
        else:
            return -1


class RfdDiffPark(BaseCriterion):
    """Criterion for ParkRating.
    Exclusive for Line areas where *vehicle* would be blocked. Value is 0 for
    Direct.

    slot: [tuple] with items (area, index of slot)

    """

    lower_bound = rfd_diff_park_lower_bound
    upper_bound = rfd_diff_park_upper_bound

    def __init__(self, slot, vehicle):
        self.value, self.diff = self.calculate(slot, vehicle)

    def calculate(self, slot, vehicle):
        area, index = slot[0], slot[1]

        if not isinstance(area, eflips.depot.LineArea):
            return 0, None

        # Get the blocking vehicle, if there is one
        side_get = area.side_get_default
        index_blocking = area.index_neighbour(index, side_get)
        if index_blocking is None:
            return 0, None
        blocking_vehicle = area.items[index_blocking]

        # Get the rfd-diff
        charge_proc = area.charge_proc  # type of charging process at the slot
        dur_est_blocked = charge_proc.estimate_duration(
            vehicle, slot[0].charging_interfaces[slot[1]]
        )
        etc_blocking = blocking_vehicle.dwd.etc_processes
        diff = 0
        if isinstance(etc_blocking, int):
            diff = slot[0].env.now + dur_est_blocked - etc_blocking
        elif etc_blocking is eflips.depot.EstimateValue.UNKNOWN:
            diff = dur_est_blocked - charge_proc.estimate_duration(
                blocking_vehicle, slot[0].charging_interfaces[index_blocking]
            )
        elif etc_blocking is eflips.depot.EstimateValue.COMPLETED:
            diff = dur_est_blocked

        # Determine value based on rfd-diff
        if diff < self.lower_bound:
            return -1, diff
        elif self.lower_bound <= diff < 0:
            return (1 / 1200) * diff, diff
        elif 0 <= diff < self.upper_bound:
            return -(1 / 3600) * diff + 1, diff
        elif self.upper_bound <= diff:
            return -1, diff


class AvailablePower(BaseCriterion):
    """Criterion for ParkRating and DispatchRating.
    For both Direct and Line areas.

    slot: [tuple] with items (area, index of slot)

    """

    def __init__(self, slot, max_power):
        self.value = self.calculate(slot, max_power)

    def calculate(self, slot, max_power):
        normalization_factor = 1 / max_power
        return slot[0].charging_interfaces[slot[1]].max_power * normalization_factor


class EmptySlotsExitPark(BaseCriterion):
    """Criterion for ParkRating.
    Exclusive for Line areas. Value is 0 for Direct.

    """

    def __init__(self, area, max_capacity_line):
        self.value = self.calculate(area, max_capacity_line)

    def calculate(self, area, max_capacity_line):
        if not isinstance(area, eflips.depot.LineArea):
            return 0

        if area.capacity == 2:
            # Can't have a blocked emtpy slot at the exit and another unblocked
            # empty slot if capacity=2
            return 0

        normalization_factor = 1 / (max_capacity_line - 2)
        return -area.vacant_blocked * normalization_factor


class SlotAlternative:
    """Summary of criteria for one slot for ParkRating.

    slot: [tuple] with items (area, index of slot)
    vehicle: [SimpleVehicle]
    max_power: [int] maximum power in kW of all charging interfaces at parking
        areas
    max_capacity_line: [int or None] max capacity of Line parking areas

    """

    def __init__(self, slot, vehicle, max_power, max_capacity_line):
        self.slot = slot
        self.vehicle = vehicle

        # Determine criteria
        self.buffer = BufferPark(slot[0], vehicle)
        self.typestack = TypestackPark(slot[0], vehicle)
        self.rfd_diff = RfdDiffPark(slot, vehicle)
        self.available_power = AvailablePower(slot, max_power)
        self.empty_slots_exit = EmptySlotsExitPark(slot[0], max_capacity_line)

        # Split rfd_diff value into pos and neg
        if self.rfd_diff.diff is None:
            self.rfd_diff_pos = 0
            self.rfd_diff_neg = 0
        elif self.rfd_diff.diff >= 0:
            self.rfd_diff_pos = self.rfd_diff.value
            self.rfd_diff_neg = 0
        else:
            self.rfd_diff_pos = 0
            self.rfd_diff_neg = self.rfd_diff.value

        # Get values suitable for rating
        self.values = [
            self.buffer.value,
            self.typestack.value,
            self.rfd_diff_pos,
            self.rfd_diff_neg,
            self.available_power.value,
            self.empty_slots_exit.value,
        ]


class ParkRating(Rating):
    """

    alternatives_obj: [list] of SlotAlternative instances

    """

    weights = [
        park_rating_weights["buffer"],
        park_rating_weights["typestack"],
        park_rating_weights["rfd_diff_pos"],
        park_rating_weights["rfd_diff_neg"],
        park_rating_weights["available_power"],
        park_rating_weights["empty_slots_exit"],
    ]

    def __init__(self, alternatives_obj):
        self.alternatives_obj = alternatives_obj

        super(ParkRating, self).__init__(
            [alt.values for alt in alternatives_obj], self.weights
        )
        self.weighted_sum()


class BufferDispatch(BaseCriterion):
    """Criterion for DispatchRating.
    Exclusive for Direct areas. Value is 0 for Line.

    """

    def __init__(self, area, vehicle):
        self.value = self.calculate(area, vehicle)

    def calculate(self, area, vehicle):
        if not isinstance(area, eflips.depot.DirectArea):
            return 0
        else:
            return -1


class TypestackDispatch(BaseCriterion):
    """Criterion for DispatchRating.
    Exclusive for Line areas. Value is 0 for Direct.

    """

    def __init__(self, area):
        self.value = self.calculate(area)

    def calculate(self, area):
        if not isinstance(area, eflips.depot.LineArea):
            return 0

        result, vtype = area.istypestack()
        if result is None:
            raise RuntimeError(
                "Area %s cannot be empty when applying the"
                "TypestackDispatch criterion." % area.ID
            )
        elif result:
            # typestack
            return 0
        else:
            # mixed types
            return 1


class RfdDiffDispatch:
    """Criterion for DispatchRating.
    Exclusive for Line areas. Value is 0 for Direct.

    slot: [tuple] with items (area, index of slot). Must contain a vehicle that
        is ready for departure, i.e. dwd.etc_processes returns
        eflips.EstimateValue.COMPLETED.

    """

    upper_bound = rfd_diff_dispatch_upper_bound

    def __init__(self, slot):
        self.value, self.diff = self.calculate(slot)

    def calculate(self, slot):
        area, index = slot[0], slot[1]
        if not isinstance(area, eflips.depot.LineArea):
            return 0, None

        max_diff = self.get_max_diff(slot)
        # Determine value based on rfd-diff
        if 0 <= max_diff < self.upper_bound:
            return -(1 / 10800) * max_diff + 1, max_diff
        elif max_diff >= self.upper_bound:
            return 0, max_diff
        elif max_diff < 0:
            # Negative diff is impossible if the blocking vehicle is rfd. Yet,
            # return 1 in this case to enable considering vehicles that are not
            # rfd.
            return 1, max_diff

    @staticmethod
    def get_max_diff(slot):
        """Get the maximum of all etcs (estimated time of completion) of all
        vehicles at the area of *slot* and return it as [int].

        slot: [tuple] with items (area, index of slot)
        """
        area = slot[0]
        charge_proc = area.charge_proc  # type of charging process at the slot
        diffs = []
        for vehicle in area.vehicles:
            etc = vehicle.dwd.etc_processes
            if isinstance(etc, int):
                diff = etc - slot[0].env.now
                diffs.append(diff)
            # Convert EstimateValue to numerical rfddiff
            elif etc is eflips.depot.EstimateValue.UNKNOWN:
                diff = charge_proc.estimate_duration(
                    vehicle, slot[0].charging_interfaces[area.items.index(vehicle)]
                )
                diffs.append(diff)
            elif etc is eflips.depot.EstimateValue.COMPLETED:
                diff = 0
                diffs.append(diff)

        return max(diffs)


class EmptySlotsExitDispatch(BaseCriterion):
    """Criterion for DispatchRating.
    Exclusive for Line areas. Value is 0 for Direct.

    """

    def __init__(self, area, max_capacity_line):
        self.value = self.calculate(area, max_capacity_line)

    def calculate(self, area, max_capacity_line):
        if not isinstance(area, eflips.depot.LineArea):
            return 0

        normalization_factor = 1 / (max_capacity_line - 1)
        return area.vacant_blocked * normalization_factor


class VehicleAlternative:
    """Summary of criteria for one vehicle for DispatchRating.

    slot: [tuple] with items (area, index of vehicle's slot)
    vehicle: [SimpleVehicle]
    max_power: [int] maximum power in kW of all charging interfaces at parking
        areas
    max_capacity_line: [int or None] max capacity of Line parking areas

    """

    def __init__(self, slot, vehicle, max_power, max_capacity_line):
        self.slot = slot
        self.vehicle = vehicle

        # Determine criteria
        self.buffer = BufferDispatch(slot[0], vehicle)
        self.typestack = TypestackDispatch(slot[0])
        self.rfd_diff = RfdDiffDispatch(slot)
        self.available_power = AvailablePower(slot, max_power)
        self.empty_slots_exit = EmptySlotsExitDispatch(slot[0], max_capacity_line)

        # Get values suitable for rating
        self.values = [
            self.buffer.value,
            self.typestack.value,
            self.rfd_diff.value,
            self.available_power.value,
            self.empty_slots_exit.value,
        ]


class DispatchRating(Rating):
    """

    alternatives_obj: [list] of VehicleAlternative instances

    """

    weights = [
        dispatch_rating_weights["buffer"],
        dispatch_rating_weights["typestack"],
        dispatch_rating_weights["rfd_diff"],
        dispatch_rating_weights["available_power"],
        dispatch_rating_weights["empty_slots_exit"],
    ]

    def __init__(self, alternatives_obj):
        self.alternatives_obj = alternatives_obj

        super(DispatchRating, self).__init__(
            [alt.values for alt in alternatives_obj], self.weights
        )
        self.weighted_sum()
