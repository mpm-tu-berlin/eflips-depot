"""Utility functions."""
import numpy as np


def share_nonzero(arr):
    """Share of elements that are unequal to zero in *arr*."""
    print(type(arr), arr)
    return np.count_nonzero(arr) / len(arr)


class StoppingCriteria:
    """Stopping utilities for optimization loops of an evolutionary algorithm."""

    def __init__(self):
        self.bestfit_history = {}
        self._criteria = []

        self.g = 0
        self.pop = None

    def select(self, *args):
        """Select the stopping criteria. Pass at least one stopping criteria
        method name as str. The same method cannot be selected twice.
        """
        for crit in args:
            try:
                self._criteria.append(getattr(self, crit))
            except AttributeError:
                raise ValueError("StoppingCriteria has no criterion named %s" % crit)

    def check(self, *args, **kwargs):
        """Return True if any of the selected stopping criteria returns True.
        Pass parameters as kwargs based on stopping criteria requirements.
        """
        return any(crit(*args, **kwargs) for crit in self._criteria)

    @staticmethod
    def max_gen_reached(g, ngen, *args, **kwargs):
        """Return True if *g* is higher than or equal to *ngen*."""
        result = g >= ngen
        if result:
            print("Stopped because of reaching max generation.")
        return result

    @staticmethod
    def max_fit_reached(hof, maxfitness, *args, **kwargs):
        """Return True if *maxfitness* is reached.

        hof: [deap.tools.HallOfFame]
        maxfitness: [deap.creator.Fitness...]
        """
        if hof.items:
            result = hof[0].fitness >= maxfitness
            if result:
                print("Stopped because of reaching max fitness.")
            return result
        else:
            return False

    def no_improvement(self, g, hof, improvement_interval, *args, **kwargs):
        """Return True if there has been no fitness improvement for the last
        *improvement_interval* generations.
        """
        self.bestfit_history[g] = hof[0].fitness if hof.items else None

        g_lookup = g - improvement_interval
        if g_lookup > 0:
            if self.bestfit_history[g_lookup] is not None:
                result = self.bestfit_history[g_lookup] >= hof[0].fitness
                if result:
                    print(
                        "Stopped because of no fitness improvement in the last %d generations."
                        % improvement_interval
                    )
                return result
            else:
                return False
        else:
            return False

    @staticmethod
    def feasible_found(pop, feasible, *args, **kwargs):
        """Return True if there is a feasible individual in the current
        population.

        pop: [list] population
        feasible: [function] returning the feasibility of an individual
        """
        for ind in pop:
            if feasible(ind):
                return True
        return False

    @staticmethod
    def higher_than(value, limit, *args, **kwargs):
        """Return True if *value* is higher than *limit*."""
        result = value > limit
        if result:
            print("Stopped because value %s is higher than %s." % (value, limit))
        return result


def attrbased_set(seq, attr):
    """Determine unique and duplicate objects in *seq* based on the comparison
    of attribute *attr*.
    """
    uniques_attr = set()
    uniques = []
    duplicates = []
    for obj in seq:
        if getattr(obj, attr) not in uniques_attr:
            uniques_attr.add(getattr(obj, attr))
            uniques.append(obj)
        else:
            duplicates.append(obj)
    return uniques, duplicates
