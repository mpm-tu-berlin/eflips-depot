# -*- coding: utf-8 -*-
"""
Resources for the depot simulation.

"""
import itertools
import simpy
from simpy.core import BoundClass
from simpy.resources.resource import PriorityRequest, Release
from eflips.settings import globalConstants
from eflips.evaluation import DataLogger
from eflips.helperFunctions import flexprint
from eflips.depot.processes import BaseDepotProcess


class DepotResourceRequest(PriorityRequest):
    """Request event for DepotResource. Subclass of simpy.resources.resource.
    PriorityRequest, for providing an interface for customization.

    caller: object that issued the request. Must have attribute 'ID'
        (e.g. 'serve').
    preempt: [bool] switch for preemption. Same behaviour as in class
        PriorityRequest. Only set to False if it is certain that this request
        is not used with simpy.AllOf alongside other requests to avoid a
        potential locked resource case (currently done so in
        DepotControl.run_processes). See depot_simulation_tests_depotprocess.py
        for explanation and demo.

    """

    def __init__(self, resource, caller, priority=0, preempt=True):
        super(DepotResourceRequest, self).__init__(resource, priority, preempt)

        self.caller = caller

        if (
            hasattr(resource, "logger")
            and globalConstants["general"]["LOG_ATTRIBUTES"]
            and globalConstants["general"]["LOG_SPECIFIC_STEPS"]
        ):
            self.callbacks.append(resource.logger.steplog)


class DepotResourceRelease(Release):
    """Release event for DepotResource. Subclass of simpy.resources.resource.
    Release, for providing an interface for customization.

    """

    def __init__(self, resource, request):
        super(DepotResourceRelease, self).__init__(resource, request)

        if (
            hasattr(resource, "logger")
            and globalConstants["general"]["LOG_ATTRIBUTES"]
            and globalConstants["general"]["LOG_SPECIFIC_STEPS"]
        ):
            self.callbacks.append(resource.logger.steplog)


class DepotResource(simpy.PreemptiveResource):
    """Extension of simpy PreemptiveResource. For representing resources such
    as workers and charging interfaces that are required for a DepotProcess.
    Can be switched on/off using simpyExt.ResourceSwitch.

    Parameters:
    depot: [Depot] instance

    """

    def __init__(self, env, ID, depot, capacity=1):
        super(DepotResource, self).__init__(env, capacity)

        self.env = env
        self.ID = ID
        self.depot = depot

        if globalConstants["general"]["LOG_ATTRIBUTES"]:
            self.logger = DataLogger(env, self, "DEPOTRESOURCE")

    request = BoundClass(DepotResourceRequest)
    release = BoundClass(DepotResourceRelease)

    @property
    def user_count(self):
        """Return a dict that contains the number of current users."""
        user_count = {}
        for user in self.users:
            ID = user.caller.ID
            if ID not in user_count:
                user_count[ID] = 1
            else:
                user_count[ID] += 1
        return user_count


class DepotChargingInterface(DepotResource):
    """
    Parameters:
    max_power: [int or float] in kW

    """

    def __init__(self, env, ID, depot, max_power):
        super(DepotChargingInterface, self).__init__(env, ID, depot, capacity=1)
        self.max_power = max_power
        self._current_power = 0
        self.power_logs_ci = {0: 0}  # Container for power logs at ci

    @property
    def current_power(self):
        return self._current_power

    @current_power.setter
    def current_power(self, value):
        # update depot.total_power
        diff = value - self._current_power
        self.depot.update_power(diff)

        self._current_power = value

        self.power_logs_ci[self.env.now] = self._current_power


class ResourceBreak(BaseDepotProcess):
    """Process used by ResourceSwitch to occupy resources."""

    def __init__(
        self,
        env,
        ID,
        dur,
        required_resources=None,
        resume=False,
        priority=-3,
        recall_priority=-3,
        preempt=True,
        dur_predefined=True,
    ):
        super(ResourceBreak, self).__init__(
            env,
            ID,
            dur,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            dur_predefined,
        )

    def _action(self):
        try:
            flexprint("Break process started.", env=self.env, switch="res_break")
            yield self.env.timeout(self.dur)
            flexprint("Break process finished.", env=self.env, switch="res_break")

        except simpy.Interrupt:
            # Early reactivation of the resource
            flexprint("Break process interrupted", env=self.env, switch="res_break")
            pass

    @staticmethod
    def estimate_duration(dur, *args, **kwargs):
        return dur


class ResourceSwitch:
    """User that occupies a DepotResource object on a predefined regular
    basis, claiming priority over other users and optionally interrupting them.
    For representing cycles that reduce a resource's capacity such as break
    times for workers.
    Must be activated with 'env.process(run_break_cycle())' before simulation
    start.

    Attributes:
    resource: [DepotResource] or subclass object. Can be None before simulation
        start.
    breaks: [list] of tuples defining phases during which *resource* is
        unavailable. The tuples must contain two values: first is the starting
        time of the unavailability-phase and second is the end time. The times
        are defined as total seconds since 0:00 on the starting day. *breaks*
        is repeated until the simulation end.
        The usual case is defining the rhythm of one day. Example:
            breaks = [(12*3600, 13*3600), (18*3600, 20*3600)]
            -> The resource is unavailable from 12:00 until 13:00 and from
            18:00 until 20:00.
        The definition can include rythms of more than one day. The base-
        timevalue stays at 0:00 on the first day:
            breaks = [(12*3600, 13*3600), (36*3600, 38*3600)]
            -> The resource is unavailable from 12:00 until 13:00 on the first
            day and from 12:00 until 14:00 on the next day.
        An unavailability-phase during the night:
            breaks = [(16.5*3600), (32*3600)]
            -> The resource is only available from 8:00 until 16:30.
        Important: Phases of unavailability must not overlap.
    preempt: [bool] Set to True if a current user's process should be strictly
        interrupted when a break begins. After the end of a break preempted
        requests are not resumed by default. If needed, the request executing
        instance has to implement resuming. Set to False if the process should
        be permitted to finish before the break starts. The end time of the
        break is not postponed, meaning that the break duration might be cut.
    strength: [int or str] of resource.capacity that is unavailable during
        breaks of this user. Set to resource.capacity or 'full' if resource
        should be fully unavailable. Set to between 0 and resource.capacity to
        only partly switch it off.
    priority [int or float]: Priority of break-requests to *resource*. Lower
        number means higher priority. Fixed value of -3. Requests by regular
        users should have a priority > -3 (the default priority for regular
        users is 0). User priority of <= -3 is possible, but can result in
        delayed or never even happening breaks.
    resume: [bool] If True, an interrupted break is resumed for the remaining
        duration.

    """

    def __init__(
        self,
        env,
        ID,
        resource,
        breaks,
        preempt=True,
        strength="full",
        resume=False,
        priority=-3,
    ):
        # TODO temporarily add priority argument
        self.env = env
        self.ID = ID
        self.strength_input = strength
        self._strength = None  # to be set with setter
        self._resource = None  # to be set with setter
        self._breaks = None  # to be set with setter
        self.strength = strength
        self.resource = resource

        self.breaks = breaks
        self.preempt = preempt

        self.priority = priority
        self.resume = resume

    @property
    def breaks(self):
        return self._breaks

    @breaks.setter
    def breaks(self, value):
        if self.check_breaks(value):
            self._breaks = value

    @property
    def resource(self):
        return self._resource

    @resource.setter
    def resource(self, value):
        """Set self._resource to *value* and update self.strength if value is
        not None. Value must be a [DepotResource] or subclass object
        or None.
        """
        if value is not None:
            if not isinstance(value, DepotResource):
                raise ValueError(
                    "Argument resource to ResourceSwitch must be "
                    "of class DepotResource or subclass."
                )

            self.strength = self.strength_input

        self._resource = value

    @property
    def strength(self):
        """Return None or the current resource.capacity or the custom value
        self._strength.
        """
        if self.resource is None:
            return None
        elif self.strength_input == "full":
            return self.resource.capacity

        return self._strength

    @strength.setter
    def strength(self, value):
        # Check if strength is int or 'full'
        if not isinstance(value, int) and (
            not isinstance(value, str) or value != "full"
        ):
            raise ValueError(
                "'%s' is an invalid input for ResourceSwitch %s " % (value, self.ID)
                + "strength. Must be 'full' "
                "(str) or of type int."
            )

        # Check if 1 < value < self.resource.capacity if int
        if self.resource is not None:
            if isinstance(value, int) and (value > self.resource.capacity or value < 1):
                raise ValueError(
                    'ResourceSwitch "%s" strength must be '
                    "between 1 and the capacity of the related "
                    "resource (=%d)." % (self.ID, self.resource.capacity)
                )

        self.strength_input = value
        self._strength = value

    @staticmethod
    def check_breaks(breaks):
        """Do essential validity checks on ResourceSwitch parameter *break*."""
        if not breaks:
            raise ValueError("ResourceSwitch breaks cannot be empty.")

        for break_no, break_i in enumerate(breaks):
            if (
                not isinstance(break_i, tuple)
                and not isinstance(break_i, list)
                or len(break_i) != 2
            ):
                raise ValueError(
                    "ResourceSwitch break must contain tuples of " "length = 2"
                )
            if break_i[0] < 0 or break_i[1] < 0:
                raise ValueError("ResourceSwitch break times must be nonnegative.")
            if break_i[0] >= break_i[1]:
                raise ValueError(
                    "ResourceSwitch break start time must be earlier "
                    "than break end time"
                )
            if break_no > 0:
                if break_i[0] <= breaks[break_no - 1][1]:
                    raise ValueError(
                        "ResourceSwitch break phases must be listed in "
                        "chronologically ascending order and start times must "
                        "be later than end times of previous phases."
                    )
        return True

    def run_break_cycle(self):
        """Infinitely loop through self.breaks and occupy self.resource
        accordingly.
        """
        # Update strength in case the self.resource.capacity has changed since
        # setting self.resource
        self.strength = self.strength_input

        for cycle_no in itertools.count():
            for break_i in self.breaks:
                # Determine the earliest possible break_start in the future
                day_no = self.env.now // 86400
                if day_no * 86400 + break_i[0] < self.env.now:
                    day_no += 1
                break_start = day_no * 86400 + break_i[0]
                until_break = break_start - self.env.now

                if until_break > 0:
                    # Wait until break
                    yield self.env.timeout(until_break)

                break_duration = break_i[1] - break_i[0]

                for m in range(self.strength):
                    # self.env.process(self.take_break(break_duration))
                    self.take_break(break_duration)

                # Create own timeout event instead of waiting for take_break()
                # because the loop has to continue even if a break is delayed
                yield self.env.timeout(break_duration)

    def take_break(self, duration):
        """Request one slot at self.resource and hold it for *duration*."""
        proc = ResourceBreak(
            self.env,
            "break",
            duration,
            required_resources=[self.resource],
            resume=self.resume,
            priority=self.priority,
            recall_priority=self.priority,
            preempt=self.preempt,
        )

        self.env.process(proc())
