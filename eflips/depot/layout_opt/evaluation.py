from collections import Counter
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import pickle
from datetime import datetime
from eflips.depot.layout_opt.settings import OPT_CONSTANTS as OC
from eflips.depot.evaluation import baseplot, savefig
from eflips.settings import globalConstants
from eflips.helperFunctions import cm2in


class OptEvaluation:
    """Container and tools for evaluation.

    feasible: [function] that returns the feasiblity of an individual.
    feasible_fr_vec: [function] that returns a feasibility vector of an
        individual from its results.
    """

    def __init__(
        self,
        pop,
        logbook,
        hof,
        crossovers,
        mutations,
        memory,
        feasible,
        feasible_fr_vec,
    ):
        self.pop = pop
        self.logbook = logbook
        self.hof = hof
        self.crossovers = crossovers
        self.mutations = mutations
        self.memory = memory
        self.feasible = feasible
        self.feasible_fr_vec = feasible_fr_vec

        self.OC = OC

        self.fitness_counter = None
        self.feasiblity_counter = None

        self.path_results = globalConstants["depot"]["path_results"]

    def _count_fitnesses(self):
        """Gather fitness and feasibility data."""
        if self.fitness_counter is None:
            self.fitness_counter = Counter()
            self.feasiblity_counter = Counter()

            for ID, value in self.memory.items():
                fit = self.memory[ID]["fitness"]
                count = self.memory[ID]["count"]

                self.fitness_counter[fit] += count
                fea = self.feasible_fr_vec(value["results"])
                self.feasiblity_counter[fea] += count

    def results_operators(self):
        evals = self.logbook.select("evals")
        looked_up = self.logbook.select("looked_up")
        if len(evals) == 1:
            print("No crossovers or mutations.")
            return
        print(
            "Crossovers total: %s; Average per gen: %s"
            % (sum(self.crossovers), np.average(self.crossovers))
        )
        mut_per_action = Counter(self.mutations)
        print(
            "Successful mutations: %s; Total: %s; Average per gen: %s"
            % (
                mut_per_action,
                sum(mut_per_action.values()),
                sum(mut_per_action.values()) / (len(evals) - 1),
            )
        )
        sum_evals = sum(evals)
        sum_looked_up = sum(looked_up)
        print("Total evals (incl. looked-up):", sum_evals + sum_looked_up)

    def results_simtimes(self):
        simtimes = []
        for value in self.memory.values():
            simtimes.append(value["results"]["simtime"])
        print(
            "Total simulations: %d, average computation time per simulation: %f seconds"
            % (len(simtimes), np.average(simtimes))
        )

    def results_feasbility(self):
        self._count_fitnesses()

        total_feasible = sum(
            self.feasiblity_counter[key]
            for key in self.feasiblity_counter
            if False not in key
        )
        total_infeasible = sum(self.feasiblity_counter.values()) - total_feasible
        share_feasible = total_feasible / sum(self.feasiblity_counter.values())
        print(
            "Among evals total feasible: %d, total infeasible: %d, share of feasbile: %s, Counter: %s"
            % (
                total_feasible,
                total_infeasible,
                share_feasible,
                self.feasiblity_counter,
            )
        )

    def progress_so(self, objective_label="Kapazität"):
        """Progress of the objective value for depot layout optimization with a
         single objective.

        objective_label: axis label for the objective
        """
        if len(self.pop[0].fitness.values) != 1:
            print("progress_so is for single objective only.")
            return

        (
            minimum,
            maximum,
            avg,
        ) = self.logbook.chapters[
            "fitness"
        ].select("min", "max", "avg")
        evals = self.logbook.select("evals")

        print(
            "Gen %s: min=%s, max=%s, avg=%s"
            % (len(minimum), minimum[-1], maximum[-1], avg[-1])
        )

        plt.scatter(range(len(evals)), minimum)
        plt.scatter(range(len(evals)), maximum)
        plt.scatter(range(len(evals)), avg)

        plt.grid(True)
        plt.xlabel("Generation")
        # plt.ylabel('Total capacity (absmax={})'.format(maxfitness_estimate))
        plt.ylabel(objective_label)
        plt.legend(["min", "max", "avg"])

    def progress_c_d_j(
        self,
        objectives_labels=("Kapazität", "Verspätung [h]", "Stau [h]"),
        objectives_names=("c", "d", "j"),
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="progress_c_d_j",
    ):
        """Plots for the progress of the objectives values for depot layout
        optimization with objectives c, d and j. Time unit is hours.
        """
        return self.progress_mo(
            objectives_labels, objectives_names, show, save, formats, basefilename
        )

    def progress_c_urfd(
        self,
        objectives_labels=("Kapazität", "Unblocked ready for departure vehicles"),
        objectives_names=("c", "urfd"),
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="progress_c_urfd",
    ):
        """Plots for the progress of the objectives values for depot layout
        optimization with objectives c and urfd.
        """
        return self.progress_mo(
            objectives_labels, objectives_names, show, save, formats, basefilename
        )

    def progress_mo(
        self,
        objectives_labels,
        objectives_names,
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="progress_mo",
    ):
        """Plots for the progress of the objectives values for depot layout
        optimization with multiple objectives.

        objectives_labels: iterable with axis labels for objectives
        objectives_names: iterable with names of objectives for the filename
        """
        n_objectives = len(self.pop[0].fitness.values)
        if n_objectives < 2:
            print("progress_mo is for multiple objective only.")
            return

        (
            minimum,
            maximum,
            avg,
        ) = self.logbook.chapters[
            "fitness"
        ].select("min", "max", "avg")
        evals = self.logbook.select("evals")
        entries = len(evals)

        min_sep = []
        max_sep = []
        avg_sep = []
        for i in range(n_objectives):
            min_sep.append([fit[i] for fit in minimum])
            max_sep.append([fit[i] for fit in maximum])
            avg_sep.append([fit[i] for fit in avg])

        x = list(range(entries))

        for i in range(n_objectives):
            fig, ax = baseplot(show)
            ax.scatter(x, min_sep[i], s=12, marker=".", alpha=0.8)
            ax.scatter(x, max_sep[i], s=12, marker=".", alpha=0.8)
            ax.scatter(x, avg_sep[i], s=12, marker=".", alpha=0.8)

            plt.grid(True)
            plt.xlabel("Generation")
            plt.ylabel(objectives_labels[i])
            plt.legend(["Minimum", "Maximum", "Mittelwert"])

            dpi = fig.get_dpi()
            fig.set_size_inches(1920.0 / float(dpi), 948.0 / float(dpi))

            if show:
                fig.show()
            if save:
                filename = self.path_results + basefilename + "_" + objectives_names[i]
                savefig(fig, filename, formats)
            if not show:
                plt.close(fig)

        return min_sep, max_sep, avg_sep

    def objective_space_c_d_j(
        self,
        include_infeasible=True,
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="objective_space_c_d_j",
    ):
        """Plot the objective space based on found fitnesses of depot layout
        optimization with objectives c, d, j. Fitnesses found multiple times
        are plotted only once.

        Time unit is hours.
        """
        any_value = next(iter(self.memory.values()))
        if len(any_value["fitness"]) != 3:
            print("objective_space_c_d_j requires three objectives in fitness")
            return

        x_f, y_f, z_f, x_i, y_i, z_i = ([] for _ in range(6))

        for value in self.memory.values():
            if all(self.feasible_fr_vec(value["results"])):
                x_f.append(value["fitness"][0])
                y_f.append(value["fitness"][1])
                z_f.append(value["fitness"][2])
            elif include_infeasible:
                x_i.append(value["fitness"][0])
                y_i.append(value["fitness"][1])
                z_i.append(value["fitness"][2])

        fig = plt.figure()
        ax = Axes3D(fig)

        sc_fea = ax.scatter(x_f, y_f, z_f)

        if include_infeasible:
            sc_infea = ax.scatter(x_i, y_i, z_i, c="r")
            plt.legend((sc_fea, sc_infea), ("Zulässig", "Unzulässig"))
        else:
            plt.legend((sc_fea,), ("Zulässig",))

        ax.set_xlabel("c")
        ax.set_ylabel("d [h]")
        ax.set_zlabel("j [h]")
        # plt.title('Objective Space')

        plt.grid()

        dpi = fig.get_dpi()
        fig.set_size_inches(1920.0 / float(dpi), 948.0 / float(dpi))

        if show:
            fig.show()
        if save:
            filename = self.path_results + basefilename
            savefig(fig, filename, formats)
        if not show:
            plt.close(fig)

    def pareto_front_c_d_j(
        self,
        include_infeasible=True,
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="pareto_front_c_d_j",
    ):
        """Same as objective_space_c_d_j but only showing the pareto front.
        Time unit is hours.
        """
        if len(self.hof.items[0].fitness.values) != 3:
            print("pareto_front_c_d_j requires three objectives in fitness")
            return

        x_f, y_f, z_f, x_i, y_i, z_i = ([] for _ in range(6))

        for ind in self.hof.items:
            if self.feasible(ind):
                x_f.append(ind.fitness.values[0])
                y_f.append(ind.fitness.values[1])
                z_f.append(ind.fitness.values[2])
            elif include_infeasible:
                x_i.append(ind.fitness.values[0])
                y_i.append(ind.fitness.values[1])
                z_i.append(ind.fitness.values[2])

        fig = plt.figure()
        ax = Axes3D(fig)

        sc_fea = ax.scatter(x_f, y_f, z_f)

        if include_infeasible:
            sc_infea = ax.scatter(x_i, y_i, z_i, c="r")
            plt.legend((sc_fea, sc_infea), ("Zulässig", "Unzulässig"))
        else:
            plt.legend((sc_fea,), ("Zulässig",))

        ax.set_xlabel("Kapazität")
        ax.set_ylabel("Verspätung [h]")
        ax.set_zlabel("Stau [h]")
        # plt.title('Pareto Front')

        plt.grid()

        dpi = fig.get_dpi()
        fig.set_size_inches(1920.0 / float(dpi), 948.0 / float(dpi))

        if show:
            fig.show()
        if save:
            filename = self.path_results + basefilename
            savefig(fig, filename, formats)
        if not show:
            plt.close(fig)

    def objective_space_c_urfd(
        self,
        include_infeasible=True,
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="objective_space_c_urfd",
        xlim=("min", "max"),
    ):
        """Plot the objective space based on found fitnesses of depot layout
        optimization with objectives c and urfd. Fitnesses found multiple times
        are plotted only once.
        """
        any_value = next(iter(self.memory.values()))
        if len(any_value["fitness"]) != 2:
            print("objective_space_c_urfd requires two objectives in fitness")
            return

        # Points due to feasibility counter capacity, delay and congestion
        (
            x_f,
            y_f,
            x_f_hof,
            y_f_hof,
            x_i_000,
            y_i_000,
            x_i_001,
            y_i_001,
            x_i_010,
            y_i_010,
            x_i_011,
            y_i_011,
            x_i_100,
            y_i_100,
            x_i_101,
            y_i_101,
            x_i_110,
            y_i_110,
        ) = ([] for _ in range(18))

        # Counter for verification
        count_f = (
            count_hof
        ) = (
            count_000
        ) = count_001 = count_010 = count_011 = count_100 = count_101 = count_110 = 0

        for value in self.memory.values():
            if all(self.feasible_fr_vec(value["results"])):
                x_f.append(value["fitness"][0])
                y_f.append(value["fitness"][1])
                count_f += 1
            elif include_infeasible:
                if not value["results"]["feasible_capacity"]:
                    if not value["results"]["feasible_delay"]:
                        if not value["results"]["feasible_congestion"]:
                            x_i_000.append(value["fitness"][0])
                            y_i_000.append(value["fitness"][1])
                            count_000 += 1
                        else:
                            x_i_001.append(value["fitness"][0])
                            y_i_001.append(value["fitness"][1])
                            count_001 += 1
                    else:
                        if not value["results"]["feasible_congestion"]:
                            x_i_010.append(value["fitness"][0])
                            y_i_010.append(value["fitness"][1])
                            count_010 += 1
                        else:
                            x_i_011.append(value["fitness"][0])
                            y_i_011.append(value["fitness"][1])
                            count_011 += 1
                else:
                    if not value["results"]["feasible_delay"]:
                        if not value["results"]["feasible_congestion"]:
                            x_i_100.append(value["fitness"][0])
                            y_i_100.append(value["fitness"][1])
                            count_100 += 1
                        else:
                            x_i_101.append(value["fitness"][0])
                            y_i_101.append(value["fitness"][1])
                            count_101 += 1
                    else:
                        if not value["results"]["feasible_congestion"]:
                            x_i_110.append(value["fitness"][0])
                            y_i_110.append(value["fitness"][1])
                            count_110 += 1
                        else:
                            print("All feasible option already plotted")

        for ind in self.hof.items:
            if self.feasible(ind):
                x_f_hof.append(ind.fitness.values[0])
                y_f_hof.append(ind.fitness.values[1])
                count_hof += 1

        print(
            count_f,
            count_hof,
            count_000,
            count_001,
            count_010,
            count_011,
            count_100,
            count_101,
            count_110,
        )

        fig, ax = baseplot(show)

        if include_infeasible:
            sc_infea_000 = ax.scatter(
                x_i_000, y_i_000, marker="x", c="#969696", alpha=0.3
            )
            sc_infea_001 = ax.scatter(
                x_i_001, y_i_001, marker="x", c="#000096", alpha=0.3
            )
            sc_infea_010 = ax.scatter(
                x_i_010, y_i_010, marker="x", c="#009600", alpha=0.3
            )
            sc_infea_011 = ax.scatter(
                x_i_011, y_i_011, marker="x", c="#009696", alpha=0.3
            )
            sc_infea_100 = ax.scatter(
                x_i_100, y_i_100, marker="x", c="#960000", alpha=0.3
            )
            sc_infea_101 = ax.scatter(
                x_i_101, y_i_101, marker="x", c="#960096", alpha=0.3
            )
            sc_infea_110 = ax.scatter(
                x_i_110, y_i_110, marker="x", c="#969600", alpha=0.3
            )
            sc_fea = ax.scatter(x_f, y_f, marker=".", c="k", alpha=0.3)
            sc_fea_hof = ax.scatter(x_f_hof, y_f_hof, marker=".", c="orange")
            plt.legend(
                (
                    sc_fea_hof,
                    sc_fea,
                    sc_infea_000,
                    sc_infea_001,
                    sc_infea_010,
                    sc_infea_011,
                    sc_infea_100,
                    sc_infea_101,
                    sc_infea_110,
                ),
                (
                    "Pareto-Menge",
                    "Zulässig",
                    "Unzulässig 000",
                    "Unzulässig 001",
                    "Unzulässig 010",
                    "Unzulässig 011",
                    "Unzulässig 100",
                    "Unzulässig 101",
                    "Unzulässig 110",
                ),
                loc="upper left",
            )
        else:
            sc_fea = ax.scatter(x_f, y_f, marker=".", c="k", alpha=0.3)
            sc_fea_hof = ax.scatter(x_f_hof, y_f_hof, marker=".", c="orange")
            plt.legend(
                (sc_fea_hof, sc_fea), ("Pareto-Menge", "Zulässig"), loc="upper left"
            )

        ax.set_xlabel("c")
        ax.set_ylabel(r"$\bar{n}_{v,urfd}$")
        # plt.title('Objective Space')

        plt.grid()
        left, right = plt.xlim()
        xlim = list(xlim)
        if xlim[0] == "min":
            xlim[0] = left
        if xlim[1] == "max":
            xlim[1] = right
        plt.xlim(*xlim)

        dpi = fig.get_dpi()
        # fig.set_size_inches(1920.0 / float(dpi), 948.0 / float(dpi))
        fig.set_size_inches(19 / 2.54, 10 / 2.54)

        if show:
            fig.show()
        if save:
            filename = self.path_results + basefilename
            savefig(fig, filename, formats)
        if not show:
            plt.close(fig)

    def pareto_front_c_urfd(
        self,
        include_infeasible=True,
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="pareto_front_c_urfd",
    ):
        """Same as objective_space_c_urfd but only showing the pareto front."""
        if len(self.hof.items[0].fitness.values) != 2:
            print("pareto_front_c_urfd requires two objectives in fitness")
            return

        x_f, y_f, x_i, y_i = ([] for _ in range(4))

        for ind in self.hof.items:
            if self.feasible(ind):
                x_f.append(ind.fitness.values[0])
                y_f.append(ind.fitness.values[1])
            elif include_infeasible:
                x_i.append(ind.fitness.values[0])
                y_i.append(ind.fitness.values[1])

        fig, ax = baseplot(show)
        sc_fea = ax.scatter(x_f, y_f)

        if include_infeasible:
            sc_infea = ax.scatter(x_i, y_i, c="r")
            plt.legend((sc_fea, sc_infea), ("Zulässig", "Unzulässig"))
        else:
            plt.legend((sc_fea,), ("Zulässig",))

        ax.set_xlabel("c")
        ax.set_ylabel("urfd")
        # plt.title('Pareto Front')

        plt.grid()

        dpi = fig.get_dpi()
        # fig.set_size_inches(1920.0 / float(dpi), 948.0 / float(dpi))
        fig.set_size_inches(13 / 2.54, 5 / 2.54)

        if show:
            fig.show()
        if save:
            filename = self.path_results + basefilename
            savefig(fig, filename, formats)
        if not show:
            plt.close(fig)

    def comptime(
        self,
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="comptime",
        xlim=(0, "max"),
    ):
        """Computation time and number of lookups per generation. When
        multiprocessing was used, values still represent wall clock time, not
        computation time sum of worker processes.
        """
        ct, lu, sk = self.logbook.select("comptime", "looked_up", "skipped")
        entries = len(ct)
        ngen = entries - 1
        x = range(entries)

        xlim = list(xlim)
        if xlim[1] == "max":
            xlim[1] = ngen

        N = max(int(xlim[1] * 0.08), 1)  # precision of running mean
        ct_trend = running_mean(ct, N)
        lu_trend = running_mean(lu, N)
        sk_trend = running_mean(sk, N)

        fig, ax1 = baseplot(show)
        plt.xlabel("Generation")

        # c_ct = '#1f77b4'  # blue
        c_ct = "#d62728"  # red
        ax1.plot(ct_trend, c=c_ct)
        ct_plot = ax1.scatter(x, ct, s=12, marker=".", alpha=0.5, c=c_ct)
        ax1.set_ylabel("Sekunden", c=c_ct)

        ax2 = ax1.twinx()

        # ax2.plot(lu_trend, c='#ff7f0e')
        # lu_plot = ax2.scatter(x, lu, s=12, marker='.', alpha=0.5, c='#ff7f0e')
        # # ax2.set_ylabel('Looked up', c='#ff7f0e')
        ax2.set_ylabel("Anzahl", c="#2ca02c")

        ax2.plot(sk_trend, c="#2ca02c")
        sk_plot = ax2.scatter(x, sk, s=12, marker=".", alpha=0.5, c="#2ca02c")
        # ax2.set_ylabel('Skipped', c='#2ca02c')

        ax2.set_ylim(bottom=0, top=len(self.pop))

        # plt.legend((ct_plot, lu_plot, sk_plot), ('Rechenzeit [s]', 'Bekannt', 'Keine Simulation wg. vermuteter Unzulässigkeit'))
        plt.legend((ct_plot, sk_plot), ("Rechenzeit", "Vermutl. unzulässig"))

        plt.xlim(*xlim)
        ax1.grid()

        # dpi = fig.get_dpi()
        # fig.set_size_inches(1920.0 / float(dpi), 948.0 / float(dpi))
        fig.set_size_inches(cm2in(16), cm2in(8))

        if show:
            fig.show()
        if save:
            filename = self.path_results + basefilename
            savefig(fig, filename, formats)
        if not show:
            plt.close(fig)

    def ideas(
        self,
        show=True,
        save=False,
        formats=("pdf",),
        basefilename="ideas",
        xlim=(0, "max"),
    ):
        """Number of individuals with new and looked up fitness by generation."""
        evals, lu = self.logbook.select("evals", "looked_up")
        entries = len(evals)
        ngen = entries - 1
        x = range(entries)

        xlim = list(xlim)
        if xlim[1] == "max":
            xlim[1] = ngen

        N = max(int(xlim[1] * 0.08), 1)  # precision of running mean
        evals_trend = running_mean(evals, N)
        lu_trend = running_mean(lu, N)

        fig, ax = baseplot(show)
        plt.xlabel("Generation")

        ax.plot(evals_trend)
        evals_plot = ax.scatter(x, evals, s=12, marker=".", alpha=0.5)

        ax.plot(lu_trend)
        lu_plot = ax.scatter(x, lu, s=12, marker=".", alpha=0.5)

        ax.set_ylabel("Anzahl")

        plt.legend((evals_plot, lu_plot), ("Neu", "Bekannt"), loc="upper right")

        plt.grid()
        xlim = list(xlim)
        if xlim[1] == "max":
            xlim[1] = ngen
        plt.xlim(*xlim)

        # dpi = fig.get_dpi()
        # fig.set_size_inches(1920.0 / float(dpi), 948.0 / float(dpi))
        fig.set_size_inches(cm2in(16), cm2in(8))

        plt.ylim(0, len(self.pop))

        if show:
            fig.show()
        if save:
            filename = self.path_results + basefilename
            savefig(fig, filename, formats)
        if not show:
            plt.close(fig)

    def save(self, filename=None):
        if filename is None:
            filename = self.path_results + "results_" + now_repr()

        filename += ".p"
        with open(filename, "wb") as file:
            pickle.dump(self, file)
        print("Saved as %s" % filename)

    def draw_hof(self, save=False, formats=("pdf",), basefilename="hof"):
        for ind in self.hof:
            if self.feasible(ind):
                print(ind.ID, ind.fitness.values)
                ind.visu.repack()
                fig, ax = ind.visu.draw()
                if save:
                    filename = self.path_results + basefilename + "_" + ind.ID
                    savefig(fig, filename, formats)


def now_repr():
    """Return the current system date and time as formatted string."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def load(filename):
    """Load from pickle file.
    When loading OptEvaluation, the setup in the optimizing execution file must
    be run first so that classes such as Individual exist.

    filename: [str] including path, excluding extension
    """
    filename += ".p"
    with open(filename, "rb") as file:
        obj = pickle.load(file)
    return obj


def running_mean(x, N):
    """Calculate the running mean of *x*.
    From https://stackoverflow.com/a/27681394
    """
    cumsum = np.cumsum(np.insert(x, 0, 0))
    return (cumsum[N:] - cumsum[:-N]) / float(N)
