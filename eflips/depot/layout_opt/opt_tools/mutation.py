"""Mutation operators for DepotPrototype."""
import random
from eflips.depot.layout_opt.opt_tools import init

mutations = []  # for logging


def mutgaussian_area_count_crop(depot, mu, sigma):
    """Random gaussian mutation of area count.
    If less than before, the area list is cropped on the right. If higher than
    before, new areas with random init values are appended to the right.
    """
    diff = round(random.gauss(mu, sigma))
    count_old = len(depot.areas)
    count_new = count_old + diff

    if count_new < init.COUNT_MIN or count_new > init.COUNT_MAX:
        # boundary violation
        return False
    if diff == 0:
        # no need to take action
        return False

    if diff < 0:
        depot.areas = depot.areas[:count_new]
    elif diff > 0:
        new_areas = [init.init_random_area() for _ in range(diff)]
        depot.areas.extend(new_areas)

    assert len(depot.areas) == count_new

    return True


def mutgaussian_area_count_rnd(depot, mu, sigma):
    """Random gaussian mutation of area count.
    If less than before, random areas are removed. If higher than before, new
    areas with random capacities are appended to the right.
    """
    diff = round(random.gauss(mu, sigma))
    count_old = len(depot.areas)
    count_new = count_old + diff

    if count_new < init.COUNT_MIN or count_new > init.COUNT_MAX:
        # boundary violation
        return False
    if diff == 0:
        # no need to take action
        return False

    if diff < 0:
        removals = random.sample(depot.areas, abs(diff))
        for removal in removals:
            depot.areas.remove(removal)
    elif diff > 0:
        new_areas = [init.init_random_area() for _ in range(diff)]
        depot.areas.extend(new_areas)

    assert len(depot.areas) == count_new

    if diff > 0:
        mutations.append("count+")
    else:
        mutations.append("count-")

    return True


def mutuniform_area_count_crop(depot, a, b):
    """Random uniform mutation of area count.
    If less than before, the area list is cropped on the right. If higher than
    before, new areas with random init values are appended to the right.
    """
    if a < 0:
        raise ValueError("a must be > 0.")
    if a > b:
        raise ValueError("a must be < b")

    diff = random.randint(a, b)
    count_old = len(depot.areas)

    if random.random() < 0.5:
        diff = -diff

    count_new = count_old + diff

    if count_new < init.COUNT_MIN or count_new > init.COUNT_MAX:
        # boundary violation
        return False
    if diff == 0:
        # no need to take action
        return False

    if diff < 0:
        depot.areas = depot.areas[:count_new]
    elif diff > 0:
        new_areas = [init.init_random_area() for _ in range(diff)]
        depot.areas.extend(new_areas)

    assert len(depot.areas) == count_new

    return True


def mutuniform_area_count_rnd(depot, a, b):
    """Random uniform mutation of area count.
    If less than before, the area list is cropped on the right. If higher than
    before, new areas with random init values are appended to the right.
    """
    if a < 0:
        raise ValueError("a must be > 0.")
    if a > b:
        raise ValueError("a must be < b")

    diff = random.randint(a, b)
    count_old = len(depot.areas)

    if random.random() < 0.5:
        diff = -diff

    count_new = count_old + diff

    if count_new < init.COUNT_MIN or count_new > init.COUNT_MAX:
        # boundary violation
        return False
    if diff == 0:
        # no need to take action
        return False

    if diff < 0:
        removals = random.sample(depot.areas, abs(diff))
        for removal in removals:
            depot.areas.remove(removal)
    elif diff > 0:
        new_areas = [init.init_random_area() for _ in range(diff)]
        depot.areas.extend(new_areas)

    assert len(depot.areas) == count_new

    return True


def mutgaussian_area_capacity(area, mu, sigma):
    """Random gaussian mutation of area capacity."""
    capacity_old = area.capacity
    capacity_new = capacity_old + round(random.gauss(mu, sigma))

    if capacity_new < area.capacity_min or capacity_new > area.capacity_max:
        # boundary violation
        return False
    if capacity_new == capacity_old:
        # no need to take action
        return False

    area.capacity = capacity_new
    area.visu = None

    if capacity_new > capacity_old:
        mutations.append("capacity+")
    else:
        mutations.append("capacity-")

    return True


def mutuniform_area_capacity(area, a, b):
    """Random uniform mutation of area capacity."""
    if a < 0:
        raise ValueError("a must be > 0.")
    if a > b:
        raise ValueError("a must be < b")

    diff = random.randint(a, b)
    capacity_old = area.capacity

    if random.random() < 0.5:
        capacity_new = capacity_old + diff
    else:
        capacity_new = capacity_old - diff

    if capacity_new < area.capacity_min or capacity_new > area.capacity_max:
        # boundary violation
        return False
    if capacity_new == capacity_old:
        # no need to take action
        return False

    area.capacity = capacity_new
    area.visu = None
    return True


def mut_area_to_other_type(area, depot):
    """Select a random area and convert it to one or more areas of another
    type, keeping the capacity."""
    this_type = type(area)
    other_type = random.choice(init.OTHER_AREA_TYPES[this_type])
    new_areas = area.to_other_type(other_type)

    if not new_areas:
        return False

    depot.areas.remove(area)
    # Append new areas to the end. The change of order is okay because
    # areas are sorted before packing.
    depot.areas.extend(new_areas)
    mutations.append("type_to_other")
    return True


def mut_combine_two_areas(depot):
    """Select two random areas and try to combine them."""
    if len(depot.areas) < 2:
        # Not enough areas
        # print('Not enough areas.')
        return False

    a1, a2 = random.sample(depot.areas, 2)
    # print('Attempting to combine %s into %s' % (a2, a1))

    if a1.capacity + a2.capacity > a1.capacity_max:
        # print('Combination of %s into %s would violate the capacity max of %d (wanted %d).' % (a2.typename, a1.typename, a1.capacity_max, a1.capacity + a2.capacity))
        return False

    a1.capacity += a2.capacity
    a1.visu = None
    depot.areas.remove(a2)
    # print('Successfully combined into %s.' % a1)
    mutations.append("type_combine")
    return True


def mutgaussian_depot_and(
    depot,
    mutsigma_area_count,
    mutpb_area_count,
    mutpb_area_type,
    mutsigma_area_capacity,
    mutpb_area_capacity,
):
    """Wrapper for mutations of a depot individual where area count, type and
    capacity might be mutated.
    """
    mutated = False
    if random.random() < mutpb_area_count:
        mutated = mutgaussian_area_count_rnd(depot, 0, mutsigma_area_count) or mutated

    for area in depot.areas.copy():
        if random.random() < mutpb_area_capacity:
            mutated = (
                mutgaussian_area_capacity(area, 0, mutsigma_area_capacity) or mutated
            )

        if random.random() < mutpb_area_type:
            mutated = mut_area_to_other_type(area, depot) or mutated

    if mutated:
        depot.reset_results()

    return mutated


def mutgaussian_depot_or_partial(
    depot,
    mutsigma_area_count,
    mutpb_area_count,
    mutpb_area_type,
    mutsigma_area_capacity,
    mutpb_area_capacity,
):
    """Wrapper for mutations of a depot individual where area count and either
    type or capacity might be mutated.
    """
    if mutpb_area_capacity + mutpb_area_type > 1:
        raise ValueError(
            "The sum of mutpb_area_type and mutpb_area_capacity "
            "must be smaller or equal to 1."
        )

    mutated = False
    if random.random() < mutpb_area_count:
        mutated = mutgaussian_area_count_rnd(depot, 0, mutsigma_area_count) or mutated

    for area in depot.areas.copy():
        mut_choice = random.random()

        if mut_choice < mutpb_area_capacity:
            mutated = (
                mutgaussian_area_capacity(area, 0, mutsigma_area_capacity) or mutated
            )

        elif mut_choice < mutpb_area_capacity + mutpb_area_type:
            mutated = mut_area_to_other_type(area, depot) or mutated

    if mutated:
        depot.reset_results()

    return mutated


def mutgaussian_depot_or(
    depot,
    mutsigma_area_count,
    mutpb_area_count,
    mutpb_area_type,
    mutsigma_area_capacity,
    mutpb_area_capacity,
    splitpb,
):
    """Wrapper for mutations of a depot individual where either area count or
    type or capacity of one area might be mutated.
    """
    if mutpb_area_count + mutpb_area_capacity + mutpb_area_type > 1:
        raise ValueError(
            "The sum of mutsigma_area_count, mutpb_area_type and "
            "mutpb_area_capacity must be smaller or equal to 1."
        )

    mutated = False
    mut_choice = random.random()

    if mut_choice < mutpb_area_count:
        mutated = mutgaussian_area_count_rnd(depot, 0, mutsigma_area_count) or mutated

    elif mut_choice < mutpb_area_count + mutpb_area_capacity:
        area = random.choice(depot.areas)
        mutated = mutgaussian_area_capacity(area, 0, mutsigma_area_capacity) or mutated

    elif mut_choice < mutpb_area_count + mutpb_area_capacity + mutpb_area_type:
        if random.random() < splitpb:  # p_split
            # Mutate one area to another type, possibly splitting it up
            area = random.choice(depot.areas)
            mutated = mut_area_to_other_type(area, depot) or mutated
        else:
            # Combine two areas
            mutated = mut_combine_two_areas(depot)

    if mutated:
        depot.reset_results()

    return mutated


def mutuniform_depot_or_partial(
    depot,
    a_area_count,
    b_a_area_count,
    mutpb_area_count,
    mutpb_area_type,
    a_area_capacity,
    b_area_capacity,
    mutpb_area_capacity,
):
    """Wrapper for mutations of a depot individual where area count and either
    type or capacity might be mutated.
    """
    if mutpb_area_capacity + mutpb_area_type > 1:
        raise ValueError(
            "The sum of mutpb_area_type and mutpb_area_capacity "
            "must be smaller or equal to 1."
        )

    mutated = False
    if random.random() < mutpb_area_count:
        mutated = (
            mutuniform_area_count_crop(depot, a_area_count, b_a_area_count) or mutated
        )

    for area in depot.areas.copy():
        mut_choice = random.random()

        if mut_choice < mutpb_area_capacity:
            mutated = (
                mutuniform_area_capacity(area, a_area_capacity, b_area_capacity)
                or mutated
            )

        elif mut_choice < mutpb_area_capacity + mutpb_area_type:
            mutated = mut_area_to_other_type(area, depot) or mutated

    if mutated:
        depot.reset_results()

    return mutated


def mutuniform_depot_or(
    depot,
    a_area_count,
    b_a_area_count,
    mutpb_area_count,
    mutpb_area_type,
    a_area_capacity,
    b_area_capacity,
    mutpb_area_capacity,
):
    """Wrapper for mutations of a depot individual where area count and either
    type or capacity might be mutated.
    """
    if mutpb_area_count + mutpb_area_capacity + mutpb_area_type > 1:
        raise ValueError(
            "The sum of mutsigma_area_count, mutpb_area_type and "
            "mutpb_area_capacity must be smaller or equal to 1."
        )

    mutated = False
    mut_choice = random.random()

    if mut_choice < mutpb_area_count:
        mutated = (
            mutuniform_area_count_rnd(depot, a_area_count, b_a_area_count) or mutated
        )

    elif mut_choice < mutpb_area_count + mutpb_area_capacity:
        area = random.choice(depot.areas)
        mutated = (
            mutuniform_area_capacity(area, a_area_capacity, b_area_capacity) or mutated
        )

    elif mut_choice < mutpb_area_count + mutpb_area_capacity + mutpb_area_type:
        area = random.choice(depot.areas)
        mutated = mut_area_to_other_type(area, depot) or mutated

    if mutated:
        depot.reset_results()

    return mutated
