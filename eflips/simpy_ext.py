# -*- coding: utf-8 -*-
"""
Created on Mon Mar  5 16:39:29 2018

@author: P.Mundt

Components that extend SimPy functionalities.
"""
from collections import deque
from simpy.resources.store import Store, StorePut, StoreGet, FilterStore, \
    FilterStoreGet
from simpy.resources.base import BaseResource
from simpy.core import BoundClass
from simpy.events import PENDING
from abc import ABC


class FilterStoreExtGet(FilterStoreGet):
    """Request to get an *item* from the *store* matching the *filter*. The
    request is triggered once there is such an item available in the store.

    *filter* is a function receiving one item. It should return ``True`` for
    items matching the filter criterion. The default function returns ``True``
    for all items, in which case the request behaves exactly like
    :class:`StoreGet`.
    
    Addition to FilterStoreGet: accepts additional input through **kwargs

    """
    def __init__(self, store, filter=lambda item: True, **kwargs):
        super(FilterStoreExtGet, self).__init__(store, filter)


class FilterStoreExt(FilterStore):
    """Extensions to SimPy's FilterStore."""
    def __init__(self, env, capacity=float('inf'), *args, **kwargs):
        super(FilterStoreExt, self).__init__(env, capacity)

    get = BoundClass(FilterStoreExtGet)
    """Request a to get an *item*, for which *filter* returns ``True``, out of
    the store."""

    @property
    def count(self):
        """Number of items currently in the store."""
        return len(self.items)

    @property
    def vacant(self):
        """Current unused capacity."""
        return self.capacity - self.count
    
    def find(self, out, filter=lambda item: True):
        """Find objects that return True for filter(item) in self.items.
        
        out: [str] Defines the output of the function. Possibilities:
            'list_bool': Return a list of booleans indicating True where 
                *filter* returns True for an item in self.items. Without a 
                match, return an empty list.
            'any': Return True if *filter* returns True for an item in 
                self.items. Otherwise return False.
            'obj_first': Return the first item for which *filter* returns True 
                in self.items. Without a match, return None.
            'ID_first': Return the attribute *ID* of the first item for 
                which *filter* returns True in self.items. Without a match, 
                return None.
            'index_first': Return the index of the first item in self.items for 
                which *filter* returns True. Without a match, return False.
            'index_all': Return a list of indices for all items in self.items
                for which filter(item) returns True. Without a match, return an
                empty list.
        """
        if out == 'list_bool':
            matches = [True if filter(item) else False for item in self.items]
            return matches
        
        elif out == 'any':
            match = next((True for item in self.items if filter(item)), False)
            return match
        
        elif out == 'obj_first':
            item = next((item for item in self.items if filter(item)), None)
            return item
        
        elif out == 'ID_first':
            ID = next((item.ID for item in self.items if filter(item)), None)
            return ID
        
        elif out == 'index_first':
            for idx, item in enumerate(self.items):
                if filter(item):
                    return idx
            return False
        
        elif out == 'index_all':
            indices = [
                idx for idx, item in enumerate(self.items) if filter(item)
            ]
            return indices
        
        else:
            raise ValueError('out = "%s" unknown' % out)

    def trigger_get(self, put_event):
        """Trigger get events. Must be called manually if the criteria that a
        get request filters by may become True only after the put event
        success.
        """
        self._trigger_get(put_event)


class StorePutExt(StorePut):
    """Request to put *item* into the *store*. The request is triggered once
    there is space for the item in the store.

    *store* must implement 'update_max_count' and 'tus_put'.

    """
    def __init__(self, store, item):
        super(StorePutExt, self).__init__(store, item)

        self.callbacks.append(store.update_max_count)

        self.t_issued = store._env.now

        def log_t_elapsed(request):
            """Determine and log the elapsed time since *request* was issued.

            request: [StorePutExt] event
            """
            request.resource.tus_put.append(
                request.resource._env.now - request.t_issued
            )
        self.callbacks.append(log_t_elapsed)


class PositionalStore(Store):
    """Store with *capacity* slots for storing objects that keep their
    position. self.items has a fixed length. Slots without an item contain
    None.

    capacity: [int] Must be an integer > 0 and cannot be infinite.
    tus_put: [list] of times until success of finished put requests

    """
    def __init__(self, env, capacity=10, *args, **kwargs):
        super(PositionalStore, self).__init__(env, capacity)

        if not isinstance(capacity, int) or capacity <= 0:
            raise ValueError('capacity in PositionalStore must be an integer '
                             '> 0.')
        self.items = [None] * capacity
        self._max_count = 0
        self.tus_put = []

    put = BoundClass(StorePutExt)
    """Request to put *item* into the store."""

    @property
    def count(self):
        """Return the amount of occupied slots."""
        return self.capacity - self.items.count(None)

    @property
    def max_count(self):
        """Return the maximum number of items that were in the store at the
        same time.
        """
        return self._max_count

    def update_max_count(self, *args, **kwargs):
        """Update self.max_count."""
        if self.count > self._max_count:
            self._max_count = self.count

    @property
    def vacant(self):
        """Return the amount of vacant slots."""
        return self.items.count(None)

    def _do_put(self, event):
        if None in self.items:
            idx = self.items.index(None)
            self.items[idx] = event.item
            event.succeed()

    def _do_get(self, event):
        item = next((i for i in self.items if i is not None), None)
        if item:
            idx = self.items.index(item)
            self.items[idx] = None
            event.succeed(item)


class PositionalFilterStore(PositionalStore, FilterStoreExt):
    """Combines PositionalStore and FilterStoreExt: self.items has a fixed
    length and items keep their position. Get requests consider a filter.

    """
    def __init__(self, env, capacity=10, *args, **kwargs):
        super(PositionalFilterStore, self).__init__(env, capacity, *args,
                                                    **kwargs)

    get = BoundClass(FilterStoreExtGet)
    """Request a to get an *item*, for which *filter* returns ``True``, out of
    the store."""

    def _do_get(self, event):
        item = next(
            (i for i in self.items if i is not None and event.filter(i)),
            None)
        if item:
            idx = self.items.index(item)
            self.items[idx] = None
            event.succeed(item)
        return True


class LineStorePut(StorePutExt):
    """Request to put *item* onto deepest accessible slot from *side* in
    *store*. The request is triggered once there is accessible space for the
    item in the store.

    side: [str] Specifier from which side the item is put into the store.
        Must be 'default', 'back' or 'front'. If 'default', side_put_default
        of *store* is applied.
    """
    def __init__(self, store, item, side='default'):

        if side not in ['default', 'back', 'front']:
            raise ValueError("Invalid side '%s'. Must be 'default', 'back' "
                             "or 'front'." % side)
        if side == 'default':
            self.side = store.side_put_default
        else:
            self.side = side

        super(LineStorePut, self).__init__(store, item)
        self.callbacks.append(store._trigger_put)


class LineStoreGet(StoreGet):
    """Request to get the first accessible *item* from *side* in *store*. The
    request is triggered once there is an item available in the store.

    side: [str] Specifier from which side the item is retrieved from the store.
        Must be 'default', 'back', 'front'. If 'default', side_get_default
        of *store* is applied.
    
    """
    def __init__(self, store, side='default'):

        if side not in ['default', 'back', 'front']:
            raise ValueError("Invalid side '%s'. Must be 'default', 'back' "
                             "or 'front'." % side)
        if side == 'default':
            self.side = store.side_get_default
        else:
            self.side = side
        
        super(LineStoreGet, self).__init__(store)
        self.callbacks.append(store._trigger_get)


class LineStore(PositionalStore):
    """Store with *capacity* slots for storing arbitrary objects in a line
    where objects can block each other and may only be accessible from one 
    side.
    Blocked objects don't move up the line after the blocking object is
    retrieved. That means that a full LineStore has to be emptied before any
    further put request can be successful.
    A LineStore has fixed sides 'back' and 'front' that can be differentiated
    in put and get requests. 'back' is at the low index side of the items list
    and 'front' and the high index side. For example, with a capacity of 4:
        back -> [0, 1, 2, 3] <- front
    Issuing a put request to the back side will result in trying to put the
    item as far to the front as possible, starting at the back. Issuing a get
    request to the front side will result in trying to retrieve the item
    closest to the front.
    Different side values for put and get result in fifo (first-in-first-out)
    while the same value for both results in lifo (last-in-first-out).
    
    capacity: [int] Must be an integer > 0 and cannot be infinite.
    side_put_default, side_get_default: [str] default side from which an item
        is put into/ retrieved from the store. Must be 'back' or 'front'.

    """
    def __init__(self, env, capacity=10, side_put_default='back',
                 side_get_default='front', *args, **kwargs):
        super(LineStore, self).__init__(env, capacity, *args, **kwargs)

        valid_sides = ('back', 'front')
        if side_put_default not in valid_sides:
            raise ValueError("Invalid side_put_default '%s'. Must be 'back' "
                             "or 'front'." % side_put_default)
        if side_get_default not in valid_sides:
            raise ValueError("Invalid side_get_default '%s'. Must be 'back' "
                             "or 'front'." % side_get_default)

        self.side_put_default = side_put_default
        self.side_get_default = side_get_default
    
    put = BoundClass(LineStorePut)
    """Request to put *item* into the store."""

    get = BoundClass(LineStoreGet)
    """Request to get an *item* out of the store."""
    
    def _do_put(self, event):
        idx = self.index_put(event.side)
        if idx is not False:
            self.items[idx] = event.item
            event.succeed()
    
    def _do_get(self, event):
        idx = self.index_get(event.side)
        if idx is not False:
            item = self.items[idx]
            self.items[idx] = None
            event.succeed(item)

    def index_put(self, side='default'):
        """Return the index of the deepest accessible slot in self.items
        starting from *side*. Return False if there is no accessible slot.
        """
        if side == 'default':
            side = self.side_put_default

        rg = self.range_from_side(side)
        idx = False
        prev_idx = rg[0]
        for idx in rg:
            if self.items[idx] is not None:
                if idx == rg[0]:    # store full or access blocked
                    return False
                else:               # accessible slot found
                    return prev_idx
            prev_idx = idx
        return idx

    def index_get(self, side):
        """Return the index of the first item in self.items that is accessible
        from *side*. Return False if there is no item in self.items.
        """
        if side == 'default':
            side = self.side_get_default

        rg = self.range_from_side(side)
        for idx in rg:
            if self.items[idx] is not None:
                return idx
        return False

    def range_from_side(self, side):
        """Return a range of indices for self.items starting from *side*."""
        if side == 'back':
            return range(self.capacity)
        elif side == 'front':
            return range(self.capacity - 1, -1, -1)
        else:
            raise ValueError("Invalid side '%s'. Must be 'back' or 'front'."
                             % side)

    @property
    def vacant(self):
        """Return the amount of vacant slots, regardless of side and
        accessibility.
        """
        return super().vacant

    def vacant_side(self, side):
        """Return the amount of unoccupied slots accessible from *side*."""
        idx = self.index_put(side)
        if side == 'back':
            if idx is False:
                return 0
            else:
                return idx + 1
        elif side == 'front':
            if idx is False:
                return 0
            else:
                return self.capacity - idx
        raise ValueError("Invalid side '%s'. Must be 'back' or 'front'."
                         % side)

    @property
    def vacant_entrance(self):
        """Return the amount of vacant slots accessible from the default
        putting side."""
        return self.vacant_side(self.side_put_default)

    @property
    def vacant_exit(self):
        """Return the amount of vacant slots accessible from the default
        getting side."""
        return self.vacant_side(self.side_get_default)

    @property
    def vacant_blocked(self):
        """Return the amount of vacant slots that are unaccessible from the
        default putting side.
        """
        n = self.vacant_exit
        if n == self.capacity:
            # vacant same as capacity means no blocked slots
            n = 0
        return n

    def isblocked(self, item, side='default'):
        """Return True if *item* is prevented from being retrieved from *side*
        by other items.
        """
        if item not in self.items:
            raise ValueError("Item %s is not in the store.")

        if side == 'default':
            side = self.side_get_default

        idx = self.index_get(side)
        return self.items[idx] is not item

    def isunblocked(self, item, side='default'):
        """Return True if *item* can be retrieved from *side*."""
        if item not in self.items:
            raise ValueError("Item %s is not in the store.")

        if side == 'default':
            side = self.side_get_default

        idx = self.index_get(side)
        return self.items[idx] is item

    def index_neighbour(self, index, side):
        """Return the index of the slot one step towards *side* starting from
        *index* if there is one. Otherwise return None. *side* must be 'front'
        or 'back'.
        """
        if index >= self.capacity or index < 0:
            raise IndexError('index %s out of range for self.items.' % index)

        if side == 'front':
            if index == self.capacity - 1:  # no neighbour in this direction
                return None
            else:
                return index + 1
        elif side == 'back':
            if index == 0:  # no neighbour in this direction
                return None
            else:
                return index - 1
        raise ValueError("Invalid side '%s' for index_neighbour. Must be "
                         "'back' or 'front'." % side)

        
class LineFilterStoreGet(LineStoreGet):
    """Request to get the first accessible *item* from *side* in *store*
    matching *filter*(item). The request is triggered once there is an
    accessible item available in the store.
    Doesn't inherit from FilterStoreGet, but provides the same filter
    functionality.

    side: [str] Same as in LineStoreGet.

    filter: Same as in simpy.FilterStoreExtGet. Function receiving one item. It
        must return True for items matching the filter criterion. The default
        function returns True for all items.
    
    """
    def __init__(self, store, filter=lambda item: True, side='default'):
        self.filter = filter
        super(LineFilterStoreGet, self).__init__(store=store, side=side)


class LineFilterStore(LineStore, FilterStoreExt):
    """Combines the LineStore and FilterStoreExt.
    Requests have to fulfil both the access criterion of LineStore and the
    filter criterion of FilterStoreExt.
    
    The method find() of FilterStoreExt is overridden to only return unblocked 
    items. The original find() is still accessible through find_ib.
    
    """
    def __init__(self, env, capacity=10, side_put_default='back',
                 side_get_default='front'):
        super(LineFilterStore, self).__init__(
            env=env, capacity=capacity, side_put_default=side_put_default,
            side_get_default=side_get_default)
    
    get = BoundClass(LineFilterStoreGet)
    """Request to get an *item* out of the store."""
    
    def _do_get(self, event):
        idx = self.index_get(event.side)
        if idx is not False and event.filter(self.items[idx]):
            item = self.items[idx]
            self.items[idx] = None
            event.succeed(item)
        return True
    
    def find(self, out, filter=lambda item: True, exclude_blocked=True,
             side='default'):
        """Find objects that return True for filter(item) in self.items.
        
        out: [str] Defines the output of the function. Possibilities:
            'list_bool': Return a list of booleans indicating True where 
                *filter* returns True for an item in self.items. Without a 
                match, return an empty list.
            'any': Return True if *filter* returns True for an item in 
                self.items. Otherwise return False.
            'obj_first': Return the first item for which *filter* returns True 
                in self.items. Without a match, return None.
            'ID_first': Return the attribute *ID* of the first item for 
                which *filter* returns True in self.items. Without a match, 
                return None.
            'index_first': Return the index of the first item in self.items for 
                which *filter* returns True. Without a match, return False.
            'index_all': Return a list of indices for all items in self.items
                for which filter(item) returns True. Without a match, return an
                empty list.
        exclude_blocked: [bool] True if items in self.items that are blocked
            (and cannot be retrieved by get()) should be excluded. If set to 
            False, this method behaves exactly like in class FilterStoreExt.
        side: See docstring for class LineStore. Is not used when
            ignoreBlocked is False.
        """
        if exclude_blocked:
            return self._find_exclude_blocked(out, side, filter)
        else: 
            return self._find_all(out, filter)
    
    def _find_all(self, out, filter):
        """Find objects that return True for filter(item) in self.items without 
        considering if it is currently blocked.
        
        Warning: side is not considered either, so that this method returns the
        results equivalent to find() in FilterStoreExt. Therefore this method
        is not suited for use in the simulation but for evaluation.

        out: see LineFilterStore.find
        """
        if out == 'list_bool':
            matches = [True if item is not None and filter(item) else False
                       for item in self.items]
            return matches
        
        elif out == 'any':
            match = next((True for item in self.items if item is not None
                          and filter(item)), False)
            return match
        
        elif out == 'obj_first':
            item = next((item for item in self.items if item is not None
                         and filter(item)), None)
            return item
        
        elif out == 'ID_first':
            ID = next((item.ID for item in self.items if item is not None
                       and filter(item)), None)
            return ID
        
        elif out == 'index_first':
            for idx, item in enumerate(self.items):
                if item is not None and filter(item):
                    return idx
            return False
        
        elif out == 'index_all':
            indices = [idx for idx, item in enumerate(self.items)
                       if item is not None and filter(item)]
            return indices
        
        else:
            raise ValueError("out = '%s' unknown" % out)
            
    def _find_exclude_blocked(self, out, side, filter):
        """Find objects that return True for filter(item) in self.items and are
        not blocked.

        out: see LineFilterStore.find
        """
        idx = self.index_get(side)
        if idx is not False and not filter(self.items[idx]):
            idx = False
        
        if out == 'list_bool':
            matches = [False] * self.capacity
            if idx is not False:
                matches[idx] = True
            return matches
        
        elif out == 'any':
            if idx is not False:
                return True
            else:
                return False
        
        elif out == 'obj_first':
            if idx is not False:
                return self.items[idx]
            else:
                return None
        
        elif out == 'ID_first':
            if idx is not False:
                return self.items[idx].ID
            else:
                return None
        
        elif out == 'index_first':
            return idx
        
        elif out == 'index_all':
            raise ValueError("out='index_all' not available for find() "\
                             + "with exclude_blocked=True.'")
        
        else:
            raise ValueError("out = '%s' unknown" % out)


class ExclusiveRequest(ABC):
    """Cancels requests in *other_requests* to other resources upon success.
    Makes it possible to issue multiple requests while making sure that only
    one (not any) of them succeeds (XOR-logic).

    Must be subclassed via multiple inheritance with priority over a simpy Put,
    Get, Request or Release (sub-)class.

    Parameters:
    other_requests: [list or None] of requests to be cancelled upon success of
    this one. May contain self. All others must not be requests to the same
    store as self.

    Attributes:
    immediate_callbacks: [list] list of functions that are called immediately
        and only upon success of this request. Any immediate callbacks must be
        added before calling super().__init__ because the request may already
        have succeeded afterwards.

    """
    def __init__(self, other_requests=None, *args, **kwargs):
        self.other_requests = other_requests if other_requests is not None \
            else []
        self.immediate_callbacks = []
        self.immediate_callbacks.append(self._cancel_others)
        super(ExclusiveRequest, self).__init__(*args, **kwargs)

    def succeed(self, value=None):
        """Set the event's value, mark it as successful, call its immediate
        callbacks and schedule it for processing by the environment. Returns
        the event instance.

        Raises :exc:`RuntimeError` if this event has already been triggerd.

        """
        if self._value is not PENDING:
            raise RuntimeError('%s has already been triggered' % self)

        self._ok = True
        self._value = value

        immediate_callbacks = self.immediate_callbacks
        self.immediate_callbacks = None
        for immediate_callback in immediate_callbacks:
            immediate_callback(self)

        self.env.schedule(self)
        return self

    def _cancel_others(self, event):
        for other in self.other_requests:
            if other is not self:
                if other.resource is self.resource:
                    raise ValueError('Exclusive requests cannot be issued to '
                                     'the same resource.')
                assert not other.triggered
                other.cancel()


class ExclusivePut(ExclusiveRequest, StorePut):
    """Demo class for the usage of ExclusiveRequest."""
    def __init__(self, store, item, other_requests=None):
        super(ExclusivePut, self).__init__(store=store, item=item,
                                           other_requests=other_requests)


class ExclusiveGet(ExclusiveRequest, StoreGet):
    """Demo class for the usage of ExclusiveRequest."""
    def __init__(self, store, other_requests=None):
        super(ExclusiveGet, self).__init__(resource=store,
                                           other_requests=other_requests)


class StoreWithExclusiveRequests(Store):
    """Demo class for the usage of ExclusiveRequest."""
    def __init__(self, env, capacity, ID):
        self.ID = ID
        super(StoreWithExclusiveRequests, self).__init__(env, capacity)

    put = BoundClass(ExclusivePut)
    get = BoundClass(ExclusiveGet)


class StoreConnector:
    """Group Store objects and redirect and process put and get requests to
    those stores.

    SimPy's any_of() can lead to more than one successful and processed
    request if there are multiple matches at the time of issuing, which might
    be a problem for some simulation scenarios.
    This class uses ExclusiveRequest to guarantee that only one request is
    successful.

    Stores grouped by StoreConnector must accept a *filter* argument in get()
    (can be neglected there, e.g. with **kwargs). Also they must implement the
    property 'count' Therefore class FilterStoreExt or subclasses or subclasses
    (such as PositionalFilterStore, LineFilterStore) can be used. Put and Get
    requests of the stores must inherit from ExclusiveRequest.

    Get and put requests to StoreConnector have to be wrapped by env.process,
    for example: item = yield env.process(sc.get()), which is different from
    issuing requests to Stores: item = yield store.get().

    stores: [list] of FilterStore or subclass objects that are grouped by
        the StoreConnector object
    capacity: [int] total capacity of stores
    default_selection_put/get: [list] of booleans used to select stores in
        put() and get() methods.

    """
    def __init__(self, env, stores):
        self.env = env
        self._stores = []   # to be set with self.stores
        self.capacity = 0
        self.default_selection_put = []
        self.default_selection_get = []

        self.stores = stores

    @property
    def stores(self):
        return self._stores

    @stores.setter
    def stores(self, value):
        self._stores = value
        self.update_defaults()

    def add_store(self, store):
        if store not in self.stores:
            self.stores.append(store)
            self.update_defaults()

    def remove_store(self, store):
        if store in self.stores:
            self.stores.remove(store)
            self.update_defaults()

    @property
    def count(self):
        """Return the sum of current items in self.stores."""
        return sum(store.count for store in self.stores)

    def clear(self):
        """Remove all entries from self.stores and update."""
        self.stores.clear()
        self.update_defaults()

    def update_defaults(self):
        self.default_selection_put = [True] * len(self.stores)
        self.default_selection_get = [True] * len(self.stores)
        self.capacity = sum(store.capacity for store in self.stores)

    def put(self, item, selection=None):
        """Summarize put_imm and put_wait.

        In this method strategies can be determined before calling the actual
        put() methods, e.g. by modifying the *selection* argument.
        """
        if selection is None:
            selection = self.default_selection_put

        store, requests = yield self.env.process(self.put_imm(item, selection))

        if store is None:
            store = yield self.env.process(self.put_wait(requests))
        return store

    def put_imm(self, item, selection=None):
        """Check if an item can be put immediately into a store in
        self.stores.
        If successful, return the store the item was put in and the list of
        cancelled requests.
        If unsuccessful, return store=None and the list of pending (!)
        requests.

        Checks consecutively if a put request is immediately successful. If
        True, already issued but unsuccessful requests are cancelled and
        following requests not issued. This is to prevent the possible success
        of more than one request with yield any_of.

        selection: [list] of booleans. If the entry matching the index in
            self.stores is False, the store is ignored.
        """

        if selection is None:
            selection = self.default_selection_put
        store = None
        requests = []
        # Check store candidates consecutively
        for candidate_no, candidate in enumerate(self.stores):
            if selection[candidate_no]:
                req = candidate.put(item, other_requests=requests)
                if req.triggered:
                    # Request is immediately successful
                    yield req
                    store = req.resource
                    return store, requests
                requests.append(req)
        return store, requests

    def put_wait(self, requests):
        """Wait for the success of a put request in requests and return the
        corresponding store. After success, cancel all other open requests.

        Important: Before calling this method, get_imm() must be called because
        otherwise *yield any_of()* could retrieve more than one item if there
        are multiple matches available at the time of request.
        """
        condition_value = yield self.env.any_of(requests)
        store = condition_value.events[0].resource
        self.assert_request_not_triggered(requests)
        return store

    def get(self, filter=lambda item: True, selection=None):
        """Get an item that returns True for filter(item) from one of the
        stores in self.stores. Guarantees that only one item is retrieved
        from the stores in total.

        In this method strategies can be determined before calling the actual
        get() methods, e.g. by modifying the *selection* argument.

        selection: [list] of booleans. If the entry matching the index in
            self.stores is False, the store is ignored.
        """
        if selection is None:
            selection = self.default_selection_get
        item, requests = yield self.env.process(self.get_imm(filter,
                                                             selection))
        if item is None:
            item = yield self.env.process(self.get_wait(requests))
        return item

    def get_imm(self, filter=lambda item: True, selection=None):
        """Check if a get request to a store in self.stores is
        immediately successful.
        If successful, return item and the list of cancelled requests.
        If unsuccessful, return item=None and the list of pending (!)
        requests.

        Checks consecutively if a get request is immediately successful. If
        True, already issued but unsuccessful requests are cancelled and
        following requests not issued. This is to prevent the possible success
        of more than one request with yield any_of.
        """

        if selection is None:
            selection = self.default_selection_get
        item = None
        requests = []
        # Check store candidates consecutively
        for candidate_no, candidate in enumerate(self.stores):
            if selection[candidate_no]:
                req = candidate.get(filter, other_requests=requests)
                if req.triggered:
                    # Request is immediately successful
                    item = yield req
                    return item, requests
                requests.append(req)
        return item, requests

    def get_wait(self, requests):
        """Return item as soon as a request in requests is successful. Then
        cancel all other requests.

        Important: Before calling this method, get_imm() must be called because
        otherwise *yield any_of()* could retrieve more than one item if there
        are multiple matches available at the time of request.
        """
        # Wait until one of the requests is successful
        condition_value = yield self.env.any_of(requests)
        # Get the actual item: any_of() returns a SimPy ConditionValue Event,
        # which offers access to the triggered Get-event and its value. The
        # value is the item that we need
        item = condition_value.events[0].value
        self.assert_request_not_triggered(requests)
        return item

    @staticmethod
    def cancel_requests(requests):
        """Cancel all requests in list *requests* that are not triggered."""
        for req in requests:
            req.cancel()

    @staticmethod
    def assert_request_not_triggered(requests):
        n_triggered = 0
        for req in requests:
            if req.triggered:
                n_triggered += 1
        assert n_triggered == 1


class SizeBasedStorePut(StorePut):
    """Request to put *item* with *itemsize* into the *store*. The request is
    triggered once there is space for the item in the store.

    """

    def __init__(self, store, item, itemsize):
        if itemsize <= 0:
            raise ValueError('itemsize(=%s) must be > 0.' % itemsize)
        self.itemsize = itemsize
        super(SizeBasedStorePut, self).__init__(store, item)


class SizeBasedStoreGet(StoreGet):
    """Request to get an *item* from the *store*. The request is triggered
    once there is an item available in the store.

    """
    pass


class SizeBasedStore(BaseResource):
    """Store for items of different sizes that count towards the capacity of
    the store.
    Items put into SimPy's store classes occupy one slot per request and the
    capacity is the total number of slots.
    This store type introduces the possibility to pass an item size (or weight)
    with a single put request. This item size directly counts towards the
    store's capacity.

    """

    def __init__(self, env, capacity):
        super(SizeBasedStore, self).__init__(env, capacity)
        self.sizes = []
        self.items = []

    @property
    def count(self):
        """Number of items currently in the store."""
        return len(self.items)

    @property
    def level(self):
        """Currently used capacity."""
        return sum(self.sizes)

    @property
    def vacant(self):
        """Current unused capacity."""
        return self.capacity - self.level

    put = BoundClass(SizeBasedStorePut)

    get = BoundClass(SizeBasedStoreGet)

    def _do_put(self, event):
        if event.itemsize <= self._capacity - self.level:
            self.sizes.append(event.itemsize)
            self.items.append(event.item)
            event.succeed()

    def _do_get(self, event):
        if self.items:
            del self.sizes[0]
            item = self.items.pop(0)
            event.succeed(item)


class SizeBasedFilterStore(SizeBasedStore, FilterStoreExt):
    """Combination of SizeBasedStore and FilterStoreExt."""

    def __init__(self, env, capacity):
        super(SizeBasedFilterStore, self).__init__(env, capacity)

    get = BoundClass(FilterStoreExtGet)

    def _do_get(self, event):
        for idx, item in enumerate(self.items):
            if event.filter(item):
                del self.sizes[idx]
                self.items.remove(item)
                event.succeed(item)
                break
        return True


class SizeBasedLineStorePut(SizeBasedStorePut):
    """Request to put *item* as deeply as possible into *store*, starting from
    *side*. The request is triggered once there is accessible space for the
    item in the store.

    side: [str] same as for LineStorePut.
    """

    def __init__(self, store, item, itemsize, side='default'):

        if side not in ['default', 'back', 'front']:
            raise ValueError("Invalid side '%s'. Must be 'default', 'back' "
                             "or 'front'." % side)
        if side == 'default':
            self.side = store.side_put_default
        else:
            self.side = side

        super(SizeBasedLineStorePut, self).__init__(store, item, itemsize)
        self.callbacks.append(store._trigger_put)


class SizeBasedLineStore(SizeBasedStore):
    """Combination of SizeBasedStore and LineStore. For this, the size-based
    capacity is maintained from the back and the front of the line. This class
    doesn't inherit from LineStore, but implements its core functionality of
    blocking.

    Attributes:
    _vacant_back, _vacant_front: remaining capacity at the back and front
        side, respectively

    """

    def __init__(self, env, capacity, side_put_default='back',
                 side_get_default='front'):
        valid_sides = ['back', 'front']
        if side_put_default not in valid_sides:
            raise ValueError("Invalid side_put_default '%s'. Must be 'back' "
                             "or 'front'." % side_put_default)
        if side_get_default not in valid_sides:
            raise ValueError("Invalid side_get_default '%s'. Must be 'back' "
                             "or 'front'." % side_get_default)

        super(SizeBasedLineStore, self).__init__(env, capacity)

        self.items = deque()
        self.sizes = deque()
        self.side_put_default = side_put_default
        self.side_get_default = side_get_default
        self._vacant_back = capacity
        self._vacant_front = capacity

    put = BoundClass(SizeBasedLineStorePut)

    get = BoundClass(LineStoreGet)

    @property
    def vacant(self):
        """Current unused capacity."""
        return self._vacant_back + self._vacant_front

    @property
    def vacant_back(self):
        return self._vacant_back

    @property
    def vacant_front(self):
        return self._vacant_front

    def vacant_side(self, side):
        if side == 'back':
            return self._vacant_back
        elif side == 'front':
            return self._vacant_front
        else:
            raise ValueError("Unknown side '%s'. Must be 'back' or 'front'."
                             % side)

    @property
    def vacant_entrance(self):
        """Return the amount of vacant capacity accessible from the default
        putting side."""
        return self.vacant_side(self.side_put_default)

    @property
    def vacant_exit(self):
        """Return the amount of vacant capacity accessible from the default
        getting side."""
        return self.vacant_side(self.side_get_default)

    @property
    def vacant_blocked(self):
        """Return the amount of vacant capacity that is unaccessible from the
        default putting side.
        """
        v = self.vacant_exit
        if v == self.capacity:
            # vacant same as capacity means no blocked vacant space
            v = 0
        return v

    def _append_side(self, side, itemsize, item):
        if side == 'back':
            self._append_back(itemsize, item)
        elif side == 'front':
            self._append_front(itemsize, item)
        else:
            raise ValueError("Unknown side '%s'. Must be 'back' or 'front'."
                             % side)

    def _append_back(self, itemsize, item):
        self.sizes.appendleft(itemsize)
        self._vacant_back -= itemsize
        if self._vacant_back + itemsize == self.capacity:
            # Set capacity of the front to 0 because item is pushed all the way
            # up to the front.
            self._vacant_front = 0
        self.items.appendleft(item)

    def _append_front(self, itemsize, item):
        self.sizes.append(itemsize)
        self._vacant_front -= itemsize
        if self._vacant_front + itemsize == self.capacity:
            # Set capacity of the back to 0 because item is pushed all the way
            # up to the back.
            self._vacant_back = 0
        self.items.append(item)

    def _pop_side(self, side):
        if side == 'back':
            return self._pop_back()
        elif side == 'front':
            return self._pop_front()
        else:
            raise ValueError("Unknown side '%s'. Must be 'back' or 'front'."
                             % side)

    def _pop_back(self):
        itemsize = self.sizes.popleft()
        self._vacant_back += itemsize
        if self._vacant_back == self.capacity:
            # Capacity of the back is at max because the last item was
            # retrieved, therefore also reset the front capacity
            self._vacant_front = 0
        return self.items.popleft()

    def _pop_front(self):
        itemsize = self.sizes.pop()
        self._vacant_front += itemsize
        if self._vacant_front == self.capacity:
            # Capacity of the front is at max because the last item was
            # retrieved, therefore also reset the back capacity
            self._vacant_back = 0
        return self.items.pop()

    def _do_put(self, event):
        vacant = self.vacant_side(event.side)
        if event.itemsize <= vacant:
            self._append_side(event.side, event.itemsize, event.item)
            event.succeed()

    def _do_get(self, event):
        if self.items:
            item = self._pop_side(event.side)
            event.succeed(item)
