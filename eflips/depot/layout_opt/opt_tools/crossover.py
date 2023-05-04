"""Crossover operators for DepotPrototype."""
from deap import tools


def cxOnePoint_depot(ind1, ind2):
    """Apply tools.cxOnePoint to the areas of the depot individuals."""
    if min(len(ind1.areas), len(ind2.areas)) <= 1:
        # cannot do cxTwoPoint on individuals with less than two areas
        return ind1, ind2

    tools.cxOnePoint(ind1.areas, ind2.areas)
    ind1.reset_results()
    ind2.reset_results()
    return ind1, ind2


def cxTwoPoint_depot(ind1, ind2):
    """Apply tools.cxTwoPoint to the areas of the depot individuals."""
    if min(len(ind1.areas), len(ind2.areas)) <= 1:
        # cannot do cxTwoPoint on individuals with less than two areas
        return ind1, ind2

    tools.cxTwoPoint(ind1.areas, ind2.areas)
    ind1.reset_results()
    ind2.reset_results()
    return ind1, ind2


def cxMessyOnePoint_depot(ind1, ind2):
    """Apply tools.cxMessyOnePoint to the areas of the depot individuals."""
    tools.cxMessyOnePoint(ind1.areas, ind2.areas)
    ind1.reset_results()
    ind2.reset_results()
    return ind1, ind2
