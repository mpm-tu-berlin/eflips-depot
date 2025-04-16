# -*- coding: utf-8 -*-
"""Components for processes in a depot."""
import math
import warnings
from abc import ABC, abstractmethod
from enum import auto, Enum
from warnings import warn

import simpy
from eflips.helperFunctions import flexprint
from eflips.settings import globalConstants

import eflips
from eflips.depot.evaluation import (
    BatteryLog,
    ChargeStart,
    FullyCharged,
    ProcessFinished,
)
from eflips.depot.filters import VehicleFilter


class ProcessStatus(Enum):
    """Status codes for depot processes."""

    NOT_STARTED = auto()  # process hasn't started yet
    WAITING = auto()  # process is waiting for required resources
    IN_PROGRESS = auto()  # process is being executed
    COMPLETED = auto()  # process successfully finished
    CANCELLED = auto()  # process was interrupted with resume=False


class EstimateValue(Enum):
    """Values for process completion time estimates in addition to an int.

    value.
    """

    UNKNOWN = auto()  # process not completed but an estimate is not possible
    COMPLETED = auto()  # process completed or no process scheduled


class BaseDepotProcess(ABC):
    """Base class for processes in a depot.

    Core functionalities are:
     - wrapping the execution of a simpy process and handling of required
        resources,
     - providing the option to resume the process after interruption.

    Implements __call__ to make an instance directly callable through
    env.process(BaseDepotProcessSubclass(...)).

    An instance is not reusable, meaning a new object should be instantiated
    for every execution (except resumption).

    A new subclass must be added to configuration.map_process_cls_refs.

    Parameters:
    ID: [str] unique name of the process, such as "serve_default". Subclasses
        instances may use the same ID.
    dur: [int or None] Duration of the execution in seconds. Can be left None
        upon instantiation (e.g. if the duration is calculated directly before
        execution).
    required_resources: [list] of DepotResource or subclass objects. May be
        empty. Before the actual process begins, the execution waits for
        requests to all of those resources to succeed.
    resume: [bool] If True, an interrupted process is resumed for the remaining
        duration.
    priority: [int] priority* for requests to resources in required_resources.
    recall_priority: [int] priority* for re-requests to resources after
        interruption.
    *Note that custom values for priority and recall_priority should be higher
        than simpyExt.ResourceSwitch.priority for Resourceswitch to keep
        its priority (lower value means higher priority).
    preempt: [bool] Argument for requests to resources in required_resources.
        See class DepotResourceRequest and SimPy-doc for details.
    dur_predefined: [bool] True if parameter *dur* is defined before (re)start
        of this process. Set to False if the duration is calculated after
        restarting the process (such as for Charge).

    Attributes:
    starts: [list] of points of time [int] when the process has (re-)started.
    proc: [simpy.Process] processing self._pem.
    requests: [list] of DepotResourceRequest objects for the current process
        execution.
    status: [ProcessStatus] member.
    finished: [simpy.Event] succeeds when the process is completed or cancelled
        and won't be resumed, resulting in self.__call__ to proceed.
    etc: [int or EstimateValue] estimated simulation time of completion. See
        method self._estimate_time_of_completion for details.
    """

    @abstractmethod
    def __init__(
        self,
        env,
        ID,
        dur=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        dur_predefined=True,
    ):
        self.env = env
        self.ID = ID
        self.dur = dur
        self.dur_orig = dur

        self.required_resources = (
            required_resources if required_resources is not None else []
        )
        self.resume = resume
        self.priority = priority
        self.recall_priority = recall_priority
        self.preempt = preempt

        self.dur_predefined = dur_predefined

        self.recall_count = 0
        self.starts = []
        self.ends = []
        self.proc = None
        self.requests = []
        self._status = ProcessStatus.NOT_STARTED
        self.finished = env.event()
        self.etc = EstimateValue.UNKNOWN

        if (
            required_resources is not None
            and not preempt
            and len(required_resources) > 1
        ):
            warn(
                'BaseDepotProcess parameter "preempt" should not be False '
                'while there is more than 1 resource in "required_resources" '
                "unless a resource-locking case can be precluded. See"
                " DepotResourceRequest.preempt for more info."
            )

    @property
    def resIDs(self):
        """Return a list of IDs of self.required_resources."""
        return [res.ID for res in self.required_resources]

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        self._status = value
        self._estimate_time_of_completion()

    def _estimate_time_of_completion(self):
        """Calculate self.etc based on the current status."""
        if (
            self.status is ProcessStatus.NOT_STARTED
            or self.status is ProcessStatus.WAITING
        ):
            self.etc = EstimateValue.UNKNOWN

        elif self.status is ProcessStatus.IN_PROGRESS:
            if self.dur is None:
                raise ValueError(
                    "Process %s attribute 'dur' must be set "
                    "before process execution." % self.ID
                )
            else:
                self.etc = self.env.now + self.dur

        elif (
            self.status is ProcessStatus.COMPLETED
            or self.status is ProcessStatus.CANCELLED
        ):
            self.etc = EstimateValue.COMPLETED

    @abstractmethod
    def _action(self, *args, **kwargs):
        """Generator function that characterizes the process.

        Subclasses must
        implement this method. It must catch the exception simpy.Interrupt and
        let it pass or implement custom follow-ups. If this method yields a
        timeout, the duration must be set as self.dur before the yield
        statement due to the determination of self.etc.
        """

    @staticmethod
    @abstractmethod
    def estimate_duration(*args, **kwargs):
        """Return an estimate of the process duration in seconds based on given.

        parameters as as if it could start immediately. Must be int, not float.
        Possible waiting time for resources are not considered. Therefore the
        estimate is reliable only if the process is started immediately. Static
        to be callable before instantiation.
        """

    def _pre(self, *args, **kwargs):
        """Space for actions before calling self._pem that may not be possible.

        upon init.
        """
        pass

    def _post(self, *args, **kwargs):
        """Space for actions after finishing self._pem."""
        pass

    def __call__(self, *args, **kwargs):
        """Generator that starts the execution of this process.

        Note that all parameters required by self._action() have to be passed
        as keyword arguments.
        """
        self._pre(*args, **kwargs)

        try:
            self.proc = self.env.process(self._pem(*args, **kwargs))
            yield self.finished

        except simpy.Interrupt:
            raise RuntimeError(
                "BaseDepotProcess must be interrupted through "
                "BaseDepotProcess.proc.interrupt() instead of "
                "interrupting __call__."
            )

        self._post(*args, **kwargs)

    def _pem(self, recall=False, *args, **kwargs):
        """Process execution method (pem) that wraps waiting for required.

        resources (if any) and self._action in a single generator. Is called
        again for the remaining duration after interruption if self.resume is
        True.

        recall: [bool] False for the first call to this method, which uses
            self.priority and self.dur. For a recall, self.recall_priority and
            overwritten self.dur are used.
        """
        prio = self.recall_priority if recall else self.priority
        self.requests = []
        action_proc = None

        try:
            # Wait for required resources
            self._request_resources(prio)
            self.status = ProcessStatus.WAITING
            yield self.env.all_of(self.requests)

            if hasattr(self, "vehicle") and self.vehicle is not None:
                flexprint(
                    'Process "%s" for vehicle %s got resources.'
                    % (self.ID, self.vehicle.ID),
                    env=self.env,
                    switch="processes",
                )

            start_time = self.env.now
            self.proc.action_start = start_time
            self.starts.append(start_time)
            # Call actual process method
            action_proc = self.env.process(self._action(*args, **kwargs))
            # Step back in event schedule to let action_proc execute until the
            # first yield statement, i.e. calculate self.dur if unknown
            # beforehand
            yield self.env.timeout(0)
            self.status = ProcessStatus.IN_PROGRESS
            yield action_proc

            if hasattr(self, "vehicle") and self.vehicle is not None:
                flexprint(
                    'Process "%s" for vehicle %s End of pem reached.'
                    % (self.ID, self.vehicle.ID),
                    env=self.env,
                    switch="processes",
                )

            self._cleanup()
            self.status = ProcessStatus.COMPLETED
            if hasattr(self, "vehicle") and self.vehicle is not None:
                self.vehicle.dwd.active_processes.remove(self)
            self.finished.succeed()

        except simpy.Interrupt:
            if hasattr(self, "vehicle") and self.vehicle is not None:
                flexprint(
                    'Process "%s" for vehicle %s interrupted.'
                    % (self.ID, self.vehicle.ID),
                    env=self.env,
                    switch="processes",
                )

            if not self.resume:
                if hasattr(
                    self.vehicle.dwd.current_area, "items"
                ):  # Needed because of Precondition, special cancel time
                    items = self.vehicle.dwd.current_area.items
                else:
                    items = None
                flexprint(
                    'Process "%s" for vehicle %s interrupted. area. %s, area items: %s. resume: %s'
                    % (
                        self.ID,
                        self.vehicle.ID,
                        self.vehicle.dwd.current_area,
                        items,
                        self.resume,
                    ),
                    env=self.env,
                    switch="departure_before_fully_charged_3",
                )

            # Interrupt process action_proc
            if action_proc is not None and not action_proc.triggered:
                action_proc.interrupt()

            if self.resume:  # Process may be resumed
                self._cleanup()

                if self.dur_predefined:
                    # Process requires pre-definition of the (remaining)
                    # duration
                    usage_time = (
                        self.env.now - self.proc.start
                        if hasattr(self.proc, "start")
                        else 0
                    )
                    remaining_time = self.dur - usage_time

                    if remaining_time > 0:
                        self.recall_count += 1

                        # Update dur for the recall
                        self.dur = remaining_time

                        # Call process again
                        if hasattr(self, "vehicle") and self.vehicle is not None:
                            flexprint(
                                'Process "%s" for vehicle %s will be resumed with dur=%s.'
                                % (self.ID, self.vehicle.ID, remaining_time),
                                env=self.env,
                                switch="processes",
                            )

                        self.proc = self.env.process(
                            self._pem(recall=True, *args, **kwargs)
                        )

                else:
                    # Continue without dur (for processes such as Charge).
                    # Call process again
                    if hasattr(self, "vehicle") and self.vehicle is not None:
                        flexprint(
                            'Process "%s" for vehicle %s will be resumed.'
                            % (self.ID, self.vehicle.ID),
                            env=self.env,
                            switch="processes",
                        )
                    self.proc = self.env.process(
                        self._pem(recall=True, *args, **kwargs)
                    )

            else:
                if hasattr(self, "vehicle") and self.vehicle is not None:
                    flexprint(
                        "Process '%s' for vehicle %s won't be resumed."
                        % (self.ID, self.vehicle.ID),
                        env=self.env,
                        switch="processes",
                    )
                self._cleanup_after_cancel()

    def _request_resources(self, prio):
        """Issue requests to required resources, if any."""
        if self.required_resources:
            resIDs = self.resIDs

            if hasattr(self, "vehicle") and self.vehicle is not None:
                flexprint(
                    'Process "%s" for vehicle %s requesting resources %s with prio=%s; dur=%s.'
                    % (self.ID, self.vehicle.ID, resIDs, prio, self.dur),
                    env=self.env,
                    switch="processes",
                )

            self.requests = [
                res.request(caller=self, priority=prio, preempt=self.preempt)
                for res in self.required_resources
            ]

    def _release_resources(self):
        """Release occupied resource slots and open requests."""
        for req in self.requests:
            if req.triggered:
                req.resource.release(req)
            else:
                req.cancel()

    def _cleanup(self):
        """Actions after process execution regardless of its success."""
        if hasattr(self, "vehicle") and self.vehicle is not None:
            flexprint(
                'Process "%s" for vehicle %s cleanup called'
                % (self.ID, self.vehicle.ID),
                env=self.env,
                switch="processes",
            )
        # Release occupied resource slots and open requests
        self._release_resources()

        # For stats
        if hasattr(self.proc, "action_start"):
            self.ends.append(self.env.now)

        if hasattr(self, "vehicle") and self.vehicle is not None:
            flexprint(
                'Process "%s" for vehicle %s cleaned up' % (self.ID, self.vehicle.ID),
                env=self.env,
                switch="processes",
            )

    def cancel(self):
        """Immediately stop the current process execution.

        No restart is
        scheduled, even if self.resume was set to True before.
        """
        if hasattr(
            self.vehicle.dwd.current_area, "items"
        ):  # Needed because of Precondition, special cancel time
            items = self.vehicle.dwd.current_area.items
        else:
            items = None
        flexprint(
            "Cancelling %s for vehicle %s. soc=%s, area: %s, area items: %s"
            % (
                self,
                self.vehicle.ID,
                self.vehicle.battery.soc,
                self.vehicle.dwd.current_area,
                items,
            ),
            env=self.env,
            switch="departure_before_fully_charged_3",
        )

        self.resume = False
        self.proc.interrupt()

    def _cleanup_after_cancel(self):
        """Do general and cancel-specific cleanup."""
        if hasattr(self, "vehicle") and self.vehicle is not None:
            flexprint(
                "Process '%s' for vehicle %s cancelled." % (self.ID, self.vehicle.ID),
                env=self.env,
                switch="processes",
            )
        self._cleanup()
        self.status = ProcessStatus.CANCELLED
        if hasattr(self, "vehicle") and self.vehicle is not None:
            self.vehicle.dwd.active_processes.remove(self)
        self.finished.succeed()

    def interrupt(self):
        """Immediately stop the current process execution.

        If self.resume is
        True, a restart is scheduled and may be successful immediately if
        required resources are available. If self.resume is False, same effect
        as self.cancel.
        """
        self.proc.interrupt()


class VehicleProcess(BaseDepotProcess, ABC):
    """Base class for processes specific for vehicles at areas.

    Parameters:
    ismandatory: [bool] True if the process is mandatory for vehicles. False if
        the process is optional. If optional, set vehicle_filter for further
        decision.
    vehicle_filter: [VehicleFilter] object for determining if the process
        should be executed for a vehicle on the area. If left None, all
        vehicles are permitted.
    cancellable_for_dispatch: [bool]: if True, the process may be cancelled for
        dispatching the vehicle.

    Attributes:
    request_immediately: [bool] if True, a vehicle will request this process to
        start immediately after entering the area this process is available at.
        There might still be waiting time due to resource requirements.
    """

    request_immediately = True

    def __init__(
        self,
        env,
        ID,
        dur,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
        dur_predefined=True,
    ):
        super(VehicleProcess, self).__init__(
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

        self.vehicle = None
        self.ismandatory = ismandatory
        self.vehicle_filter = (
            vehicle_filter if vehicle_filter is not None else VehicleFilter()
        )

        self.cancellable_for_dispatch = cancellable_for_dispatch
        if cancellable_for_dispatch:
            self.finished.callbacks.append(self.notify_assignment)

    def _pre(self, *args, **kwargs):
        self.vehicle = kwargs["vehicle"]
        # flexprint('in _pre of %s for vehicle %s. slot: %s'
        #         % (self.ID, self.vehicle.ID, self.vehicle.dwd.current_slot),
        #         env=self.env, switch='objID', objID=self.vehicle.ID)
        self.vehicle.dwd.active_processes.append(self)

        if (
            hasattr(self.vehicle, "logger")
            and globalConstants["general"]["LOG_ATTRIBUTES"]
            and globalConstants["general"]["LOG_SPECIFIC_STEPS"]
        ):
            self.vehicle.logger.steplog()

    def _post(self, *args, **kwargs):
        if globalConstants["depot"]["log_cm_data"]:
            # Use home_depot in case the vehicle left the depot before
            # finishing the process (not robust for simulations with multiple
            # depots)
            self.vehicle.dwd.home_depot.evaluation.cm_report.log(
                ProcessFinished(self.env, self.vehicle)
            )
        # flexprint(
        #     'in _post of %s for vehicle %s. slot: %s'
        #     % (self.ID, self.vehicle.ID, self.vehicle.dwd.current_slot),
        #     env=self.env, switch='objID', objID=self.vehicle.ID)

    def notify_assignment(self, *args):
        """Trigger dispatch process if there is a relevant update."""
        if (
            self.vehicle is not None
            and self.vehicle.dwd.current_area.issink
            and self.vehicle.dwd.active_processes
            and all(
                [
                    proc.cancellable_for_dispatch
                    for proc in self.vehicle.dwd.active_processes
                ]
            )
        ):
            # flexprint(
            #     "NOTIFYING ASSIGNMENT AFTER %s END for vehicle %s"
            #     % (self.ID, self.vehicle.ID),
            #     env=self.env,
            # )

            self.vehicle.dwd.current_depot.depot_control.trigger_dispatch()


class Serve(VehicleProcess):
    """Process of serving a vehicle such as cleaning."""

    def __init__(
        self,
        env,
        ID,
        dur,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
    ):
        super(Serve, self).__init__(
            env,
            ID,
            dur,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
        )

    def _action(self, vehicle):
        try:
            flexprint(
                "\t%s started service." % (vehicle.ID),
                env=self.env,
                switch="operations",
            )
            flexprint(
                "\t%s started service." % (vehicle.ID), env=self.env, switch="processes"
            )
            yield self.env.timeout(self.dur)
            vehicle.dwd.service_need = False
            vehicle.dwd.t_lastServed = self.env.now
            flexprint(
                "\t%s completed service." % (vehicle.ID),
                env=self.env,
                switch="processes",
            )

        except simpy.Interrupt:
            flexprint("action interrupted", env=self.env, switch="processes")
            pass

    @staticmethod
    def estimate_duration(dur, *args, **kwargs):
        return dur


class ChargeAbstract(VehicleProcess, ABC):
    """Base class for charging of vehicles at a depot area.

    Parameters:
    dur: is None because the duration is set during charging.
    required_resources: doesn't need to contain the charging interface because
        it's detected automatically.
    efficiency: [int or float] efficiency of charging process. Value higher
        than 0, max 1.

    Attributes:
    last_update: [int] time of last call to self.update_battery.
    """

    @abstractmethod
    def __init__(
        self,
        env,
        ID,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
        efficiency=1,
        *args,
        **kwargs
    ):
        if required_resources is not None:
            raise ValueError(
                "Parameter 'required_resources' is deactivated for process "
                "ChargeAbstract. The charging interface is detected "
                "automatically. Remove this statement when adding other "
                "requirements."
            )

        super(ChargeAbstract, self).__init__(
            env,
            ID,
            None,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
            dur_predefined=False,
        )

        self.efficiency = efficiency
        self.last_update = 0
        self.energy = 0  # total Energy of charging process
        self.charging_interface = None  # is set upon call

    def _pre(self, *args, **kwargs):
        super()._pre(*args, **kwargs)
        self.charging_interface = self.vehicle.dwd.current_charging_interface

    def _request_resources(self, prio):
        """Request the charging interface at the vehicle's current slot."""

        flexprint(
            'Process "%s" for vehicle %s requesting resource %s'
            " with prio=%s; dur=%s. Index: %d, slot_no: %d"
            % (
                self.ID,
                self.vehicle.ID,
                self.charging_interface.ID,
                prio,
                self.dur,
                self.vehicle.dwd.current_area.items.index(self.vehicle),
                self.vehicle.dwd.current_slot,
            ),
            env=self.env,
            switch="processes",
        )

        self.requests = [
            self.charging_interface.request(
                caller=self, priority=prio, preempt=self.preempt
            )
        ]

    @classmethod
    def get_chargedata(cls, vehicle):
        """Get data for charging process of class *cls*.

        Not robust if there
        are multiple data sets using *cls* with different parameters and
        vehicle filters that can return True for the same vehicle.
        """
        return next(
            (
                procdata
                for procdata in vehicle.dwd.current_depot.processes.values()
                if procdata["type"] is cls
                and procdata["kwargs"]["vehicle_filter"](vehicle)
            ),
            None,
        )

    def update_battery(self, event_name, amount=None):
        """Update the energy level of self.vehicle.battery.

        Can be called
        during the execution of the process to provide an interim update.

        Parameters:
        event_name: [str] for BatteryLog
        amount: [None or float] amount of energy to add to the battery. Must be
            passed if amount might be so small that t_elapsed would be 0 (i.e.
            if called by Charge(sub-)-classes). Leave None for update requests
            from outside the process.
        """
        if amount is None and (
            self.last_update == self.env.now or self.starts[-1] == self.env.now
        ):
            # Skip if the call is not for putting and either the process just
            # started (possibly waiting for resources) or just updated
            return

        if amount is None:
            t_base = max(self.last_update, self.starts[-1])
            t_elapsed = self.env.now - t_base
            amount = t_elapsed / 3600 * self.power * self.efficiency
            rest = round(
                self.vehicle.battery.energy_real * self.soc_target
                - self.vehicle.battery.energy,
                14,
            )
            if rest < amount:
                # Process was about to finish in this second. Reduce amount to
                # rest to avoid soc > soc_target and provide an early update
                amount = rest
                event_name = "charge_end"

        self.vehicle.battery.put(amount)
        self.energy += amount
        self.vehicle.battery_logs.append(
            BatteryLog(self.env.now, self.vehicle, event_name)
        )
        self.last_update = self.env.now


class Charge(ChargeAbstract):
    """Process of charging a vehicle's battery.

    Charging is constant during the
    whole charging process with maximum power provided by the charging
    interface.

    Parameters:
    soc_target: [int or float or str] the charging process will stop at this
        soc. Must be 0 < soc_target <= 1 or 'soc_max'. For soc_max,
        vehicle.battery.soc_max will be used as soc_target.
    """

    def __init__(
        self,
        env,
        ID,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
        efficiency=1,
        soc_target=1,
    ):
        super(Charge, self).__init__(
            env,
            ID,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
            efficiency,
        )

        self.check_soc_target(soc_target, vehicle_filter)
        self.soc_target = soc_target

    @property
    def power(self):
        return self.charging_interface.max_power

    def _action(self, *args, **kwargs):
        self.vehicle.battery_logs.append(
            BatteryLog(self.env.now, self.vehicle, "charge_start")
        )
        if globalConstants["depot"]["log_cm_data"] and len(self.starts) == 1:
            # Log the first start only (ignore interruptions)
            self.vehicle.dwd.current_depot.evaluation.cm_report.log(
                ChargeStart(self.env, self.vehicle)
            )

        self.vehicle.battery.active_processes.append(self)

        if self.soc_target == "soc_max":
            self.soc_target = self.vehicle.battery.soc_max
        amount = (
            self.vehicle.battery.energy_real * self.soc_target
            - self.vehicle.battery.energy
        )
        assert amount > 0, (
            self.recall_count,
            self.vehicle.ID,
            self.vehicle.battery.energy,
            self.soc_target,
        )

        effective_power = self.power * self.efficiency
        self.dur = int(amount / effective_power * 3600)
        # flexprint('\t%s (energy=%d, energy_real=%d) needs to charge %f kWh. Will take %d s at %d kW'
        #           % (self.vehicle.ID, self.vehicle.battery.energy, self.vehicle.battery.energy_real, amount, self.dur, power),
        #           env=self.env, switch='operations')

        self.charging_interface.current_power = self.power
        self.vehicle.power_logs[self.env.now] = effective_power

        try:
            yield self.env.timeout(self.dur)

            # Update with exact amount to prevent rounding errors
            rest = round(
                self.vehicle.battery.energy_real * self.soc_target
                - self.vehicle.battery.energy,
                14,
            )
            assert rest >= 0
            if rest > 0:
                # rest is 0 if update_battery was called first in this same
                # second. Skip adding rest in this case because update_battery
                # already did
                self.update_battery("charge_end", amount=rest)

            # charge full event
            if globalConstants["depot"]["log_cm_data"]:
                self.vehicle.dwd.current_depot.evaluation.cm_report.log(
                    FullyCharged(self.env, self.vehicle)
                )

            flexprint(
                "\t%s completed charging." % (self.vehicle.ID),
                env=self.env,
                switch="operations",
            )

        except simpy.Interrupt:
            flexprint("charge interrupted", env=self.env, switch="processes")
            self.update_battery("charge_interrupt")

        self.charging_interface.current_power = 0
        self.vehicle.power_logs[self.env.now] = 0
        self.vehicle.battery.active_processes.remove(self)
        self.vehicle.battery.n_charges += 1

    @staticmethod
    def estimate_duration(vehicle, charging_interface, *args, **kwargs):
        """Return a duration estimate [int] assuming constant charging at.

        maximum power until soc_target is reached.
        """
        chargedata = Charge.get_chargedata(vehicle)
        if chargedata is None:
            # vehicle doesn't need charging (anymore) since vehicle filter
            # returned False
            return 0

        efficiency = 1  # chargedata["kwargs"]["efficiency"] TODO: Re-enable this
        soc_target = 1  # chargedata["kwargs"]["soc_target"]
        if soc_target == "soc_max":
            soc_target = vehicle.battery.soc_max

        amount = vehicle.battery.energy_real * soc_target - vehicle.battery.energy
        dur = int(amount / charging_interface.max_power * 3600 * efficiency)
        return dur

    @staticmethod
    def check_soc_target(soc_target, vehicle_filter):
        if soc_target != "soc_max" and not 0 < soc_target <= 1:
            raise ValueError("soc_target must be > 0 and <= 1 or 'soc_max'")
        if (
            "soc_lower_than" in vehicle_filter.filter_names
            and vehicle_filter.soc is not None
            and vehicle_filter.soc > soc_target
        ):
            raise ValueError(
                "soc_target for charging must be higher than "
                "the soc of filter_soc_lower_than"
            )


class ChargeSteps(ChargeAbstract):
    """Process of charging a vehicle's battery in linear power steps.

    Parameters:
    steps: [list] of lists or tuples that contain pairs of SoC and power.
        Example: [(0.8, 150), (1, 20)]
        The pairs define the power for charging a battery until the
        corresponding SoC value is reached. The above example states: "Charge
        at 150 kW until SoC=0.8, then reduce to 20 kW until the SoC=1".
        The process starts at the correct SoC value in steps, meaning that if
        in the example a vehicle that already has an SoC of 0.81 starts
        charging will start to charge with 20 kW.
        steps must contain at least one pair and must be sorted by SoC values
        in ascending order. Charging stops when the highest SoC value is
        reached, meaning that for steps=[(0.8, 50), (0.9, 150)] charging will
        stop at SoC=0.9. This functionality replaces the parameter 'soc_target'
        from the class ChargeAbstract/Charge.
        Note that the power values must not be higher than the maximum power of
        the charging interface.
    """

    def __init__(
        self,
        env,
        ID,
        steps,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
        efficiency=1,
    ):
        super(ChargeSteps, self).__init__(
            env,
            ID,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
            efficiency,
        )

        self.steps = steps
        self._power = 0

    @property
    def power(self):
        return self._power

    @power.setter
    def power(self, value):
        self._power = value

    def _action(self, *args, **kwargs):
        self.vehicle.battery_logs.append(
            BatteryLog(self.env.now, self.vehicle, "charge_start")
        )
        if globalConstants["depot"]["log_cm_data"] and len(self.starts) == 1:
            # Log the first start only (ignore interruptions)
            self.vehicle.dwd.current_depot.evaluation.cm_report.log(
                ChargeStart(self.env, self.vehicle)
            )

        self.vehicle.battery.active_processes.append(self)

        # Find the entry point in steps
        steps_cropped = []
        for entry in self.steps:
            if entry[0] > self.vehicle.battery.soc:
                steps_cropped.append(entry)
        assert steps_cropped, (
            "soc is already higher than this process "
            "can achieve. Case should be avoided with "
            "a vehicle filter."
        )

        try:
            # Charge according to steps_cropped until steps_cropped is empty
            while steps_cropped:
                soc_step, self.power = steps_cropped.pop(0)
                amount = (
                    self.vehicle.battery.energy_real * soc_step
                    - self.vehicle.battery.energy
                )
                assert amount > 0, (
                    self.recall_count,
                    self.vehicle.ID,
                    self.vehicle.battery.energy,
                    soc_step,
                )
                effective_power = self.power * self.efficiency
                self.dur = int(amount / effective_power * 3600)

                # flexprint('\t%s (energy=%d, energy_real=%d) needs to charge %f kWh. Will take %d s at %d kW'
                #           % (self.vehicle.ID, self.vehicle.battery.energy, self.vehicle.battery.energy_real, amount, self.dur, power),
                #           env=self.env, switch='operations')

                self.charging_interface.current_power = self.power
                self.vehicle.power_logs[self.env.now] = effective_power
                yield self.env.timeout(self.dur)

                if steps_cropped:
                    self.update_battery("charge_step")
                    flexprint(
                        "Vehicle %s after update: soc_step=%s, soc: %s, energy=%s, energy_real=%s, amount=%s, power=%s, effective_power=%s, dur=%s, steps_cropped=%s, slot=%s"
                        % (
                            self.vehicle.ID,
                            soc_step,
                            self.vehicle.battery.soc,
                            self.vehicle.battery.energy,
                            self.vehicle.battery.energy_real,
                            amount,
                            self.power,
                            effective_power,
                            self.dur,
                            steps_cropped,
                            self.vehicle.dwd.current_slot,
                        ),
                        env=self.env,
                        switch="objID",
                        objID=self.vehicle.ID,
                    )

            rest = round(
                self.vehicle.battery.energy_real * soc_step
                - self.vehicle.battery.energy,
                14,
            )
            assert rest >= 0
            if rest > 0:
                # rest is 0 if update_battery was called first in this same
                # second. Skip adding rest in this case because update_battery
                # already did
                self.update_battery("charge_end", amount=rest)

            flexprint(
                "Vehicle %s after update: soc_step=%s, soc: %s, energy=%s, energy_real=%s, amount=%s, power=%s, effective_power=%s, dur=%s, steps_cropped=%s, slot=%s"
                % (
                    self.vehicle.ID,
                    soc_step,
                    self.vehicle.battery.soc,
                    self.vehicle.battery.energy,
                    self.vehicle.battery.energy_real,
                    amount,
                    self.power,
                    effective_power,
                    self.dur,
                    steps_cropped,
                    self.vehicle.dwd.current_slot,
                ),
                env=self.env,
                switch="objID",
                objID=self.vehicle.ID,
            )

            # charge full event
            if globalConstants["depot"]["log_cm_data"]:
                self.vehicle.dwd.current_depot.evaluation.cm_report.log(
                    FullyCharged(self.env, self.vehicle)
                )

            flexprint(
                "\t%s completed charging." % (self.vehicle.ID),
                env=self.env,
                switch="operations",
            )

        except simpy.Interrupt:
            flexprint("charge interrupted", env=self.env, switch="processes")
            self.update_battery("charge_interrupt")

        self.charging_interface.current_power = 0
        self.vehicle.power_logs[self.env.now] = 0
        self.vehicle.battery.active_processes.remove(self)
        self.vehicle.battery.n_charges += 1

    @staticmethod
    def estimate_duration(vehicle, *args, **kwargs):
        """Return a duration estimate [int] respecting steps."""
        chargedata = ChargeSteps.get_chargedata(vehicle)
        if chargedata is None:
            # vehicle doesn't need charging (anymore) since vehicle filter
            # returned False
            return 0

        efficiency = chargedata["kwargs"]["efficiency"]
        steps = chargedata["kwargs"]["steps"]

        steps_cropped = []
        for entry in steps:
            if entry[0] > vehicle.battery.soc:
                steps_cropped.append(entry)

        if not steps_cropped:
            return 0

        dur = 0
        while steps_cropped:
            soc_step, power = steps_cropped.pop(0)
            amount = vehicle.battery.energy_real * soc_step - vehicle.battery.energy
            effective_power = power * efficiency
            dur += int(amount / effective_power * 3600)

        return dur

    @staticmethod
    def check_steps(steps):
        """Check *steps* for validity."""
        try:
            iterable = iter(steps)
        except TypeError:
            raise TypeError("steps must be iterable.")
        else:
            if not steps:
                raise ValueError("steps cannot be empty.")
            previous = -1
            for soc, power in iterable:
                if previous >= soc:
                    raise ValueError(
                        "Entries in steps must be sorted by SoC "
                        "values in ascending order and cannot "
                        "contain equal SoC values."
                    )
                previous = soc


class ChargeEquationSteps(ChargeAbstract):
    """Process of charging a vehicle's battery based on a linearized function.

    for power.

    Parameters:
    peq_name: [str] name of equation that returns power based on the current
        situation. Must be in module eflips.depot.processes.
        Example: 'exponential_power'
    peq_params: {dict} of parameters that peq requires in addition to what is
        accessible via arguments.
    precision: [float] SoC invervals to call peq at while charging. Must
        be > 0 and < 1. Lower value means higher precision. High precision can
        significantly increase simulation runtime. Precision is capped to
        intervals resulting in step durations >= 1 second.
    soc_target: same as for class Charge
    """

    def __init__(
        self,
        env,
        ID,
        peq_name,
        peq_params,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
        efficiency=1,
        precision=0.01,
        soc_target=1,
    ):
        super(ChargeEquationSteps, self).__init__(
            env,
            ID,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
            efficiency,
        )

        self.peq = getattr(eflips.depot.processes, peq_name)
        self.peq_params = peq_params
        if not 0 < precision < 1:
            raise ValueError("precision must be > 0 and < 1.")
        self.precision = precision
        Charge.check_soc_target(soc_target, vehicle_filter)
        self.soc_target = soc_target

    @property
    def power(self):
        return self.peq(self.vehicle, self.charging_interface, self.peq_params)

    def _action(self, *args, **kwargs):
        self.vehicle.battery_logs.append(
            BatteryLog(self.env.now, self.vehicle, "charge_start")
        )
        if globalConstants["depot"]["log_cm_data"] and len(self.starts) == 1:
            # Log the first start only (ignore interruptions)
            self.vehicle.dwd.current_depot.evaluation.cm_report.log(
                ChargeStart(self.env, self.vehicle)
            )

        self.vehicle.battery.active_processes.append(self)

        if self.soc_target == "soc_max":
            self.soc_target = self.vehicle.battery.soc_max
        assert (
            self.vehicle.battery.soc < self.soc_target
        ), "soc is already higher than this process can achieve. Case should be avoided with a vehicle filter."

        soc_target_step = 0
        try:
            while soc_target_step < self.soc_target:
                soc_interval = min(
                    self.precision, self.soc_target - self.vehicle.battery.soc
                )
                soc_target_step = self.vehicle.battery.soc + soc_interval
                amount = self.vehicle.battery.energy_real * soc_interval
                effective_power = self.power * self.efficiency
                self.dur = int(amount / effective_power * 3600)

                if self.dur == 0:
                    # amount is so small that dur is <1s. Reduce the precision
                    # to what 1 second would yield. Prevents yielding 0-
                    # timeouts with minimal amounts until the battery is full.
                    # flexprint('%s, soc: %s, soc_target: %s, soc_target_step: %s, soc_interval: %s, power: %s, dur: %s, amount: %s'
                    #       % (self.vehicle, self.vehicle.battery.soc, self.soc_target, soc_target_step, soc_interval, effective_power, self.dur, amount), env=self.env)
                    self.dur = 1
                    amount_1s = 1 / 3600 * effective_power
                    soc_interval_1s = amount_1s / self.vehicle.battery.energy_real
                    soc_target_1s = self.vehicle.battery.soc + soc_interval_1s
                    soc_target_step = min(soc_target_1s, self.soc_target)

                # flexprint('\t%s (energy=%d, energy_real=%d) needs to charge %f kWh. Will take %d s at %d kW'
                #           % (self.vehicle.ID, self.vehicle.battery.energy, self.vehicle.battery.energy_real, amount, self.dur, power),
                #           env=self.env, switch='operations')

                self.charging_interface.current_power = self.power
                self.vehicle.power_logs[self.env.now] = effective_power
                yield self.env.timeout(self.dur)

                if soc_target_step < self.soc_target:
                    # recalculate amount because update_battery may have been
                    # called in the meantime
                    amount_step = (
                        self.vehicle.battery.energy_real * soc_target_step
                        - self.vehicle.battery.energy
                    )
                    self.update_battery("charge_step", amount=amount_step)
                    flexprint(
                        "Vehicle %s after update: until_soc=%s, soc: %s, energy=%s, energy_real=%s, amount=%s, power=%s, effective_power=%s, dur=%s, slot=%s"
                        % (
                            self.vehicle.ID,
                            soc_interval,
                            self.vehicle.battery.soc,
                            self.vehicle.battery.energy,
                            self.vehicle.battery.energy_real,
                            amount,
                            self.power,
                            effective_power,
                            self.dur,
                            self.vehicle.dwd.current_slot,
                        ),
                        env=self.env,
                        switch="objID",
                        objID=self.vehicle.ID,
                    )
                else:
                    # Skip to adding rest
                    break

            rest = round(
                self.vehicle.battery.energy_real * self.soc_target
                - self.vehicle.battery.energy,
                14,
            )
            # assert rest >= 0, '%s, soc: %s, soc_target: %s, rest: %s, soc_target_step: %s, now: %s, last_update: %s, last log t: %s, last log name: %s' \
            #                   % (self.vehicle, self.vehicle.battery.soc, self.soc_target, rest, soc_target_step, self.env.now, self.last_update, self.vehicle.battery_logs[-1].t, self.vehicle.battery_logs[-1].event_name)
            if rest > 0:
                # rest is 0 if update_battery was called first in this same
                # second. Skip adding rest in this case because update_battery
                # already did
                self.update_battery("charge_end", amount=rest)

            # flexprint(
            #     'Vehicle %s after update: until_soc=%s, soc: %s, energy=%s, energy_real=%s, amount=%s, power=%s, effective_power=%s, dur=%s, slot=%s'
            #     % (self.vehicle.ID, soc_interval, self.vehicle.battery.soc, self.vehicle.battery.energy, self.vehicle.battery.energy_real, amount, self.power,
            #        effective_power, self.dur, self.vehicle.dwd.current_slot),
            #     env=self.env, switch='objID', objID=self.vehicle.ID)

            # charge full event
            if globalConstants["depot"]["log_cm_data"]:
                self.vehicle.dwd.current_depot.evaluation.cm_report.log(
                    FullyCharged(self.env, self.vehicle)
                )

            flexprint(
                "\t%s completed charging." % (self.vehicle.ID),
                env=self.env,
                switch="operations",
            )

        except simpy.Interrupt:
            flexprint("charge interrupted", env=self.env, switch="processes")
            self.update_battery("charge_interrupt")
            warnings.warn(
                "It is unclear whether interrupting a charging process of this type returns the desired "
                "result. Double-check the battery state after the simulation."
            )

        self.charging_interface.current_power = 0
        self.vehicle.power_logs[self.env.now] = 0
        self.vehicle.battery.active_processes.remove(self)
        self.vehicle.battery.n_charges += 1

    @staticmethod
    def estimate_duration(vehicle, charging_interface, *args, **kwargs):
        """Return a duration estimate [int] respecting steps."""
        chargedata = ChargeEquationSteps.get_chargedata(vehicle)
        if chargedata is None:
            # vehicle doesn't need charging (anymore) since vehicle filter
            # returned False
            return 0

        efficiency = chargedata["kwargs"]["efficiency"]
        soc_target = chargedata["kwargs"]["soc_target"]
        precision = chargedata["kwargs"]["precision"]
        peq = getattr(eflips.depot.processes, chargedata["kwargs"]["peq_name"])
        peq_params = chargedata["kwargs"]["peq_params"]
        if soc_target == "soc_max":
            soc_target = vehicle.battery.soc_max

        soc = vehicle.battery.soc
        dur = 0

        while soc < soc_target:
            soc_interval = min(precision, soc_target - soc)
            soc += soc_interval

            amount = vehicle.battery.energy * soc_interval

            power = peq(vehicle, charging_interface, peq_params)
            effective_power = power * efficiency
            dur += amount / effective_power * 3600

        return int(dur)


def exponential_power(vehicle, charging_interface, peq_params, *args, **kwargs):
    """Return power in kW for the use with ChargeEquationSteps.

    Parameters:
    vehicle: [SimpleVehicle]
    charging_interface: [DepotChargingInterface]
    peq_params: [dict] with parameters for this function in addition to what is
        accessible via vehicle and charging_interface
    """
    SoC = vehicle.battery.soc
    P_max = charging_interface.max_power
    SoC_threshold = peq_params["x"]
    En = vehicle.battery.energy_nominal

    if SoC < SoC_threshold:
        return P_max
    else:
        # return P_max ** (5 - SoC / 0.2) * 10 ** (4 - SoC / 0.2) * En ** (SoC / 0.2 - 4)
        return (
            P_max
            / 10
            * (1 / En - 10)
            / (math.exp(1) - math.exp(SoC_threshold))
            * (math.exp(SoC) - math.exp(SoC_threshold))
            + P_max
        )


class Standby(VehicleProcess):
    """Process of mandatory waiting such as standby times."""

    def __init__(
        self,
        env,
        ID,
        dur,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
    ):
        super(Standby, self).__init__(
            env,
            ID,
            dur,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
        )

    def _action(self, vehicle):
        try:
            flexprint(
                "\t%s started standby." % (vehicle.ID),
                env=self.env,
                switch="operations",
            )
            yield self.env.timeout(self.dur)
            flexprint(
                "\t%s completed standby." % (vehicle.ID),
                env=self.env,
                switch="operations",
            )

        except simpy.Interrupt:
            flexprint("action interrupted", env=self.env, switch="processes")
            pass

    @staticmethod
    def estimate_duration(dur, *args, **kwargs):
        return dur


class Repair(VehicleProcess):
    """Process of repairing a vehicle."""

    def __init__(
        self,
        env,
        ID,
        dur,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
    ):
        super(Repair, self).__init__(
            env,
            ID,
            dur,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
        )

    def _action(self, vehicle):
        try:
            flexprint(
                "\t%s started repair." % (vehicle.ID), env=self.env, switch="operations"
            )
            yield self.env.timeout(self.dur)
            vehicle.dwd.repair_need = False
            flexprint(
                "\t%s completed repair." % (vehicle.ID),
                env=self.env,
                switch="operations",
            )

        except simpy.Interrupt:
            flexprint("action interrupted", env=self.env, switch="processes")
            pass

    @staticmethod
    def estimate_duration(dur, *args, **kwargs):
        return dur


class Maintain(VehicleProcess):
    """Process of maintaining a vehicle."""

    def __init__(
        self,
        env,
        ID,
        dur,
        ismandatory=True,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
    ):
        super(Maintain, self).__init__(
            env,
            ID,
            dur,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
        )

    def _action(self, vehicle):
        try:
            flexprint(
                "\t%s started maintenance." % (vehicle.ID),
                env=self.env,
                switch="operations",
            )
            yield self.env.timeout(self.dur)
            vehicle.dwd.maintenance_need = False
            flexprint(
                "\t%s completed maintenance." % (vehicle.ID),
                env=self.env,
                switch="operations",
            )

        except simpy.Interrupt:
            flexprint("action interrupted", env=self.env, switch="processes")
            pass

    @staticmethod
    def estimate_duration(dur, *args, **kwargs):
        return dur


class Precondition(VehicleProcess):
    """Preconditioning for a vehicle.

    Parameters:
    required_resources: doesn't need to contain the charging interface because
        it's detected automatically.
    power: in kW.

    Attributes:
    request_immediately: is False for this class because preconditioning is
        scheduled depending on departure time instead of entry time at an area.
    """

    request_immediately = False

    def __init__(
        self,
        env,
        ID,
        dur,
        ismandatory=False,
        vehicle_filter=None,
        required_resources=None,
        resume=True,
        priority=0,
        recall_priority=-1,
        preempt=True,
        cancellable_for_dispatch=False,
        efficiency=1,
        power=20,
    ):
        super(Precondition, self).__init__(
            env,
            ID,
            dur,
            ismandatory,
            vehicle_filter,
            required_resources,
            resume,
            priority,
            recall_priority,
            preempt,
            cancellable_for_dispatch,
        )

        self.efficiency = efficiency
        self.power = power

    def _action(self, vehicle):
        try:
            flexprint(
                "\t%s started preconditioning." % (vehicle.ID),
                env=self.env,
                switch="operations",
            )
            effective_power = self.power * self.efficiency
            self.charging_interface.current_power = self.power
            self.vehicle.power_logs[self.env.now] = effective_power
            yield self.env.timeout(self.dur)
            self.charging_interface.current_power = 0
            self.vehicle.power_logs[self.env.now] = 0

            flexprint(
                "\t%s completed preconditioning." % (vehicle.ID),
                env=self.env,
                switch="operations",
            )

        except simpy.Interrupt:
            flexprint("action interrupted", env=self.env, switch="processes")
            self.charging_interface.current_power = 0
            self.vehicle.power_logs[self.env.now] = 0

    def _pre(self, *args, **kwargs):
        super()._pre(*args, **kwargs)
        self.charging_interface = self.vehicle.dwd.current_charging_interface

    @staticmethod
    def estimate_duration(dur, *args, **kwargs):
        return dur
