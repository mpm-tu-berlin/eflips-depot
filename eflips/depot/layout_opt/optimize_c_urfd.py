"""Script for executing the depot layout optimization with two objectives:
- max capacity (c)
- max mean of unblocked ready for departure vehicles (urfd).

Uses the DEAP evolutionary computation framework.
"""
import random
import numpy as np
from multiprocessing import Pool
import os
from deap import base, creator, tools

# Load opt settings before importing other parts of layout_opt
import eflips.depot.layout_opt.settings

filename_opt_settings = "..\\..\\..\\bus_depot\\opt_settings\\diss_kls"

eflips.depot.layout_opt.settings.load_settings(filename_opt_settings)
from eflips.depot.layout_opt.settings import OPT_CONSTANTS as OC

from eflips.depot.layout_opt import opt_tools, util, evaluation
from eflips.depot.layout_opt.opt_tools.fitness_util import memorize, lookup
import eflips.depot.layout_opt.opt_tools.fitness_c_urfd
from eflips.helperFunctions import Tictoc


# GA-parameters

# Load optimization settings
POP_SIZE = OC["algorithm"]["POP_SIZE"]

CXPB = OC["algorithm"]["CXPB"]

MUTPB = OC["algorithm"]["MUTPB"]
MUTPB_AREA_COUNT = OC["algorithm"]["MUTPB_AREA_COUNT"]
MUTSIGMA_AREA_COUNT = OC["algorithm"]["MUTSIGMA_AREA_COUNT"]
MUTPB_AREA_TYPE = OC["algorithm"]["MUTPB_AREA_TYPE"]
MUTPB_AREA_CAPACITY = OC["algorithm"]["MUTPB_AREA_CAPACITY"]
MUTSIGMA_AREA_CAPACITY = OC["algorithm"]["MUTSIGMA_AREA_CAPACITY"]

# Set max. generations
NGEN = 1000

# Set up optimization tools

creator.create("FitnessMulti", base.Fitness, weights=(1.0, 1.0))
creator.create("Individual", opt_tools.DepotPrototype, fitness=creator.FitnessMulti)

toolbox = base.Toolbox()
toolbox.register("individual", opt_tools.init_random_depot, creator.Individual)
toolbox.register("population", tools.initRepeat, list, toolbox.individual)
toolbox.register("clone", opt_tools.clone_depot)

toolbox.register("mate", opt_tools.cxOnePoint_depot)
toolbox.register(
    "mutate",
    opt_tools.mutgaussian_depot_or,
    mutsigma_area_count=MUTSIGMA_AREA_COUNT,
    mutpb_area_count=MUTPB_AREA_COUNT,
    mutpb_area_type=MUTPB_AREA_TYPE,
    mutsigma_area_capacity=MUTSIGMA_AREA_CAPACITY,
    mutpb_area_capacity=MUTPB_AREA_CAPACITY,
    splitpb=OC["algorithm"]["SPLITPB"],
)
# toolbox.register('select', tools.selTournamentDCD)
toolbox.register("select", tools.selNSGA2)
toolbox.register("evaluate", opt_tools.fitness_c_urfd.evaluate)


def main():
    """Execute the given optimization setup.
    Return the final population and statistics.
    """
    g = 0  # generation counter

    # Start time measurement
    tictoc = Tictoc(print_timestamps=False)
    tictoc.tic()

    # Initialize a population
    pop = toolbox.population(n=POP_SIZE)

    # Evaluate the entire population

    # Split individuals into uniques and duplicates to lower the evaluation
    # effort
    uniques, duplicates = util.attrbased_set(pop, "ID")
    results = list(pool.map(toolbox.evaluate, uniques))
    # results = list(map(toolbox.evaluate, uniques))
    fitnesses = [result[:-1] for result in results]
    ind_results = [result[-1] for result in results]
    for ind, fit, ind_result in zip(uniques, fitnesses, ind_results):
        ind.fitness.values = fit
        ind.results = ind_result
        memorize(ind, memory)
    for ind in duplicates:
        # Look up fitness for duplicates
        lookup(ind, memory)
    tools.emo.assignCrowdingDist(pop)

    # Prepare logging and stats
    logbook = tools.Logbook()
    logbook.header = (
        "gen",
        "evals",
        "duplicates_this_gen",
        "looked_up",
        "skipped",
        "memorized",
        "comptime",
        "fitness",
    )
    logbook.chapters["fitness"].header = "min", "avg", "max", "std", "feasible"
    cx_this_gen = 0
    crossovers = []
    # Log stats
    record = mstats.compile(pop)
    skipped = sum(ind.results["simtime"] == 0 for ind in pop)
    feasible = sum(
        eflips.depot.layout_opt.opt_tools.fitness_c_urfd.feasible(ind) for ind in pop
    ) / len(pop)
    logbook.record(
        gen=g,
        evals=len(pop),
        duplicates_this_gen=len(duplicates),
        looked_up=0,
        skipped=skipped,
        memorized=len(memory),
        comptime=tictoc.last_interval,
        feasible=feasible,
        **record
    )
    print()
    print(logbook.stream)  # values of initial population
    hof.update(pop)
    tictoc.toc()

    while not stopping_criteria.check(
        g=g,
        ngen=NGEN,
        hof=hof,
        maxfitness=maxfitness_estimate,
        improvement_interval=30,
        pop=pop,
        feasible=eflips.depot.layout_opt.opt_tools.fitness_c_urfd.feasible,
    ):
        g += 1

        # Clone the population to prepare the offspring
        offspring = list(pool.map(toolbox.clone, pop))
        # offspring = list(map(toolbox.clone, pop))

        # Apply crossover on the offspring
        for child1, child2 in zip(offspring[::2], offspring[1::2]):
            if random.random() < CXPB:
                toolbox.mate(child1, child2)
                del child1.fitness.values
                del child2.fitness.values
                cx_this_gen += 1

        # Apply mutation on the offspring
        for i in range(2):
            for mutant in offspring:
                if random.random() < MUTPB:
                    mutated = toolbox.mutate(mutant)
                    if mutated:
                        del mutant.fitness.values

        # Evaluate the fitness of the offspring

        modified_inds = [ind for ind in offspring if not ind.fitness.valid]
        # Split modified individuals into uniques and duplicates to lower the
        # evaluation effort
        uniques, duplicates = util.attrbased_set(modified_inds, "ID")
        # Look up fitness for individuals that are memorized
        for ind in uniques:
            lookup(ind, memory)
        # Evaluate the individuals that really are new
        new_inds = [ind for ind in uniques if not ind.fitness.valid]
        results = list(pool.map(toolbox.evaluate, new_inds))
        # results = list(map(toolbox.evaluate, new_inds))
        fitnesses = [result[:-1] for result in results]
        ind_results = [result[-1] for result in results]
        for ind, fit, sim_result in zip(new_inds, fitnesses, ind_results):
            ind.fitness.values = fit
            ind.results = sim_result
            memorize(ind, memory)
        # Look up fitness for individuals that are duplicate in this generation
        # or memorized even earlier
        for ind in duplicates:
            lookup(ind, memory)

        # Select the next generation individuals from pop and offspring
        tools.emo.assignCrowdingDist(pop + offspring)  # required for selTournamentDCD
        pop[:] = toolbox.select(pop + offspring, POP_SIZE)

        # Log stats
        hof.update(pop)
        crossovers.append(cx_this_gen)
        cx_this_gen = 0
        record = mstats.compile(pop)
        skipped = sum(ind.results["simtime"] == 0 for ind in new_inds)
        feasible = sum(
            eflips.depot.layout_opt.opt_tools.fitness_c_urfd.feasible(ind)
            for ind in pop
        ) / len(pop)
        logbook.record(
            gen=g,
            evals=len(new_inds),
            duplicates_this_gen=len(duplicates),
            looked_up=len(modified_inds) - len(new_inds),
            skipped=skipped,
            memorized=len(memory),
            comptime=tictoc.last_interval,
            feasible=feasible,
            **record
        )
        print(logbook.stream)
        tictoc.toc()

    tictoc.print_timestamps = True
    tictoc.toc()
    print("(%f s per toc)" % (tictoc.tlist[-1] / tictoc.nTocs - 1))
    print()
    return pop, logbook, crossovers


if __name__ == "__main__":
    opt_tools.init.print_area_precomps()

    # Set up multiprocessing (must be protected by "if __name__ == '__main__'")
    n_processes = os.cpu_count() - 1  # os.cpu_count() is default
    pool = Pool(n_processes, maxtasksperchild=100)
    # maxtasksperchild: renew processes after a certain amount of tasks (proved
    # to be critical for long optimization runs)
    print("Multiprocessing with %d processes" % n_processes)
    toolbox.register("map", pool.map)

    # Prepare statistics
    fitness_stats = tools.Statistics(key=lambda ind: ind.fitness.values)
    mstats = tools.MultiStatistics(fitness=fitness_stats)
    mstats.register("avg", np.mean, axis=0)
    mstats.register("std", np.std, axis=0)
    mstats.register("min", np.min, axis=0)
    mstats.register("max", np.max, axis=0)

    hof = tools.ParetoFront()

    # Set up memory and other logging
    memory = {}

    # Set stopping criteria
    maxfitness_estimate = creator.FitnessMulti()
    maxfitness_estimate.values = (opt_tools.init.CAPACITY_MAX, 102)
    stopping_criteria = util.StoppingCriteria()
    # stopping_criteria.select('max_gen_reached', 'max_fit_reached', 'no_improvement')
    # stopping_criteria.select('no_improvement')
    stopping_criteria.select("max_gen_reached")
    # stopping_criteria.select('max_gen_reached', 'no_improvement')
    # stopping_criteria.select('max_fit_reached')
    # stopping_criteria.select('max_fit_reached', 'no_improvement')
    # stopping_criteria.select('feasible_found')
    # stopping_criteria.select('feasible_found', 'max_gen_reached')

    # Run optimization
    print("Estimated max fitness: %s" % maxfitness_estimate)
    pop, logbook, crossovers = main()

    # Gather data and do some evaluation
    ev = evaluation.OptEvaluation(
        pop,
        logbook,
        hof,
        crossovers,
        eflips.depot.layout_opt.opt_tools.mutation.mutations,
        memory,
        eflips.depot.layout_opt.opt_tools.fitness_c_urfd.feasible,
        eflips.depot.layout_opt.opt_tools.fitness_c_urfd.feasible_fr_vec,
    )
    ev.results_operators()
    ev.results_feasbility()
    ev.results_simtimes()

    # ev.save()
