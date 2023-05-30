# -*- coding: utf-8 -*-
"""
Created on Fri Jan  26 14:54:23 2018

@author: P.Boev

all helpers should be placed here
"""
import datetime
import itertools
import json
import operator
import sys
import time
from collections.abc import Mapping
from functools import reduce

from simpy.core import Environment

import eflips
from eflips.misc import ArgumentError


class Stopwatch:
    def __init__(self):
        self._lastTime = time.time()
        self.times = []

    def add(self, description):
        newTime = time.time()
        dt = newTime - self._lastTime
        self._lastTime = newTime
        self.times.append((description, dt))

def createEvalScheme(objType):
    """helper function to combine and remove repeating
    attributes from evaluation sets

    Pavel Boev
    """
    evaluationSets = eflips.globalConstants['evaluationSets']
    evaluationScheme = eflips.globalConstants['evaluationScheme']

    if evaluationSets.get(objType) is None:
        raise ArgumentError('Object type %s not found in the evaluation sets.'
                            % objType)
    else:
        if not evaluationScheme[objType]:
            raise ArgumentError('No evaluation sets selected. '
                                'Please add at least one evaluation set.')
        else:
            if not all(keys in evaluationSets[objType] for
                       keys in evaluationScheme[objType]):
                raise ArgumentError('Evaluation set does not exist.'
                                    ' Check the input.')
            else:
                d = {}
                evalDicts = {key: value for key, value in
                             evaluationSets[objType].items() if
                             key in evaluationScheme[objType]}
                for evalSet in evalDicts.values():
                    for key in evalSet:
                        try:
                            d[key].append(evalSet[key])
                        except KeyError:
                            d[key] = [evalSet[key]]
                for key in d:
                    d[key] = \
                        list(set(list(itertools.chain.from_iterable(
                            d[key]))))
                return d


def compare(value, operator, search_in):
    """Helper function to perform comparison operations, used, e.g., in 
    eflips.charging.generateChargingPoints_OC()
    
    Dominic Jefferies
    """
    out = False
    if operator == '==' and search_in == value:
        out = True
    elif operator == '<' and search_in < value:
        out = True
    elif operator == '<=' and search_in <= value:
        out = True
    elif operator == '>' and search_in > value:
        out = True
    elif operator == '>=' and search_in >= value:
        out = True
    elif operator == 'in':
        # Make sure both value and search_in are not a plain
        # string or numeric, otherwise we would iterate over each character
        # of the string:
        if not isinstance(value, list):
            value = [value]
        if not isinstance(search_in, list):
            search_in = [search_in]
        out = any(x in value for x in search_in)
    return out


def cm2in(*cm):
    """Convert cm to inch (required for matplotlib). If multiple arguments
    are supplied, return a tuple; useful for
    fig = plt.figure(figsize=cm2in(15,9))."""
    oneinch = 2.54
    if len(cm) > 1:
        # return tuple
        return tuple(i/oneinch for i in cm)
    elif len(cm) == 1:
        return cm[0]/oneinch
    else:
        # return zero
        return 0


def add_class_attribute(obj, old_attribute_name, new_attribute_name):
    """Adds '_' in front of an object attribute.
    Internal function to change attribute name when using getter and setter

    Pavel Boev
    """
    if old_attribute_name[0] is not '_' and\
            hasattr(obj, '_' + old_attribute_name) is False:
        setattr(obj.__class__, new_attribute_name,
                getattr(obj, old_attribute_name))
        # del obj.old_attribute_name


def complex_setter(self, logObjClass, attr, new_attr_name, value):
    """creates setter property with log function

    Pavel Boev
    """
    setattr(self, new_attr_name, value)
    obj = logObjClass.instances[0]()
    if hasattr(self, 'logger'):
        if isinstance(self.logger, dict):
            if attr not in self.logger:
                self.logger[attr] = {}
            self.logger[attr].update({obj.env.now: value})
            print('LOG attr: %s, time: %d' % (attr, obj.env.now))
        else:
            self.logger.log(attr)


def change_getter(self, logObjClass, attr, fun_str):
    """extends existing getter property with log function

    Pavel Boev
    """
    obj = logObjClass.instances[0]()
    value = eval(fun_str)
    if hasattr(self, 'logger'):
        if isinstance(self.logger, dict):
            if attr not in self.logger:
                self.logger[attr] = {}
            self.logger[attr].update({obj.env.now: value})
        else:
            self.logger.log(attr)
    return value


def flexprint(*objects, sep=' ', end='\n', file=sys.stdout, flush=False,
              env=None, switch=None, objID=None):
    """Extension of the Python print() function.
    Only prints if the value for *switch* in dict
    globalConstants['general']['FLEXPRINT_SWITCHES'] is True or *switch* is
    None.

    Accepts optional parameters in addition to print():
    env: [SimPy Environment object] If passed, the current simulation time
        is printed in the same line as *objects*, e.g.:
            't = 5: Vehicle arrived.'
    switch: [str] key in globalConstants['general']['FLEXPRINT_SWITCHES'].
    objID: [str] If *switch* is 'objID' and objID is passed, only print if
        objID matches the value of key 'objID' in
        globalConstants['general']['FLEXPRINT_SWITCHES'].

    Allows quick search-and-replace of usual print().

    PM
    """
    # Imported here to avoid circular import dependency
    from eflips.settings import globalConstants
    proceed = True

    if switch is not None:
        if switch not in globalConstants['general']['FLEXPRINT_SWITCHES']:
            raise ValueError('switch %s not in FLEXPRINT_SWITCHES.' % switch)

        # Check switch
        if not globalConstants['general']['FLEXPRINT_SWITCHES'][switch]:
            proceed = False
        elif switch == 'objID':
            if objID is None:
                raise ValueError("objID cannot be None when switch is"
                                 + " 'objID'.")
            if globalConstants['general']['FLEXPRINT_SWITCHES'][
                switch] != objID:
                proceed = False

    # Print only if proceed is (still) True
    if proceed:
        if env is not None:
            if not isinstance(env, Environment):
                raise ValueError('env must be a SimPy Environment object.')
            else:
                # Extend output by time value
                slice_time = 't = ' + str(int(env.now))
                slice_time_clock = '(' + seconds2date(int(env.now)) + '):'
                objects = [slice_time, slice_time_clock, *objects]

        print(*objects, sep=sep, end=end, file=file, flush=flush)

        # gui logging
        from eflips.depot.gui.depot_view import main_view
        if main_view is not None:
            s = []
            for x in objects:
                if isinstance(x, str):
                    if x != "":
                        s.append(x)
                else:
                    try:
                        arr = []
                        arr.append("[")
                        for t in x:
                            subArr = []
                            subArr.append(t)
                        arr.append(", ".join(subArr))
                        arr.append("]")
                        s.append("".join(arr))
                    except:
                        pass
            main_view.log("DEBUG", ' '.join(s))


class Tictoc:
    """Methods for printing elapsed time between code snippets.

    Similar to MATLAB tic and toc. Toc results can be printed immediately and
    on request after code execution.

    Inspired by
        https://stackoverflow.com/questions/5849800/tic-toc-functions-analog-in-python

    print_timestamps: [bool] switch for console outputs
    name: [str] to add at the start of console outputs

    PM
    """

    def __init__(self, print_timestamps=True, name=''):
        self.print_timestamps = print_timestamps
        self.name = name
        self.tlist = None
        self.nTocs = 1

    def tic(self):
        """Start the time measurement."""
        self.tstart = time.time()
        self.tlist = [0]
        if self.print_timestamps:
            print(self.name + 'Starting time measurement. Now:',
                  time.strftime('%H:%M:%S', time.localtime()))

    def toc(self, mode='imm'):
        """Calculate the time from tic.
        Call at all locations where time should be stopped.
        mode: [str] define where the toc result should be printed.
            'imm': (default) print immediately after measurement
            'list': don't print during execution; usage of print_toclist()
                instead
            all recorded stops can be printed later using print_toclist(),
                regardless of the mode used
        """
        if self.tlist is None:
            print(self.name + 'Must use tic before toc')

        else:
            t = time.time() - self.tstart
            self.tlist.append(t)

            if self.print_timestamps:
                if mode == 'imm':
                    print(self.name + 'Elapsed time at toc no %d: %f seconds.'
                          % (self.nTocs, t))
                elif mode == 'list':
                    pass
                else:
                    print(self.name + 'Invalid toc mode.')

            self.nTocs += 1

    @property
    def last_interval(self):
        """Return the time between the last recorded toc and the previous in
        seconds.
        """
        if len(self.tlist) == 1:
            return self.tlist[0]
        else:
            return self.tlist[-1] - self.tlist[-2]

    def print_toclist(self, mode='interval'):
        """Print list of all toc measurements.
        mode: [str] define what toc time should be printed
            'cumulative': same as simple toc output: difference from toc-start
            'interval': (default) time calculated from previous tic or toc
            statement
        """
        if len(self.tlist) > 1:
            print()

            if mode == 'cumulative':
                for tocNo in range(len(self.tlist) - 1):
                    print(self.name + 'Cumulative elapsed time at toc no '
                                      '%d: %f seconds.'
                          % (tocNo + 1, self.tlist[tocNo + 1]))

            elif mode == 'interval':
                intervals = [j - i for i, j in
                             zip(self.tlist[:-1], self.tlist[1:])]
                for tocNo in range(len(intervals)):
                    print(self.name + 'Elapsed time from previous mark to '
                                      'toc no %d: %f seconds.'
                          % (tocNo + 1, intervals[tocNo]))

            else:
                print(self.name + 'Invalid print_toclist mode: %s.'
                      % (mode))
        else:
            print(self.name + 'No tocs recorded.')


def progressbar(env, SIM_TIME, step=1000, step_unit='s'):
    """Print the simulation time during execution.

    env: [simpy.core.Environment]
    SIM_TIME: [int] total simulation time in seconds
    step: [int] interval between outputs. Can be passed as total seconds or
        percentage of SIM_TIME
    step_unit: [str] may be '%' or 's'

    Example calls:
        env.process(eflips.progressbar(env, SIM_TIME, 2000, 's'))
        env.process(eflips.progressbar(env, SIM_TIME, 1, '%'))

    PM
    """
    if step_unit == '%':
        # Convert so seconds
        step = int(round(step * SIM_TIME / 100))
    elif step_unit != 's':
        raise ArgumentError("step_unit must be 's' or '%'")

    while env.now < SIM_TIME:
        yield env.timeout(step)
        perc = round(env.now / SIM_TIME * 100)
        sys.stdout.write('\rsimulation progress: %d/%d (%d %%)\n'
                         % (env.now, SIM_TIME, perc))

    perc = round(env.now / SIM_TIME * 100)
    sys.stdout.write('\rsimulation progress: %d/%d (%d %%)\n'
                     % (env.now, SIM_TIME, perc))


class InstanceCheck:
    """Helper class for keeping info about instances.
    Usages: Instantiate as class variable of the class to check instances of
    (includes subclasses) or instantiate as attribute to use less strictly.
    May be extended to keep instance references, etc.

    PM
    """
    def __init__(self):
        self.IDs = []

    def unique_ID(self, ID, raise_exc=False):
        """Check the uniqueness of parameter *ID*. Raise an error for a
        duplicate ID if *raiseExc* is True.
        Example usage in __init__: self.ic.unique_ID(ID)
        """
        if ID not in self.IDs:
            self.IDs.append(ID)
            return True
        else:
            if raise_exc:
                raise ValueError('Duplicate ID "%s".' % (ID))
            else:
                return False

    def remove(self, ID):
        """Remove object with *ID*."""
        self.IDs.remove(ID)

    def clear(self):
        """Remove all entries."""
        self.IDs.clear()


class SortedList(list):
    """List that maintains its order sorted by an item's constant value of
    attribute *key* when appending or extending.

    Efficient if the only methods to add items to the list are append and
    extend.

    Parameters:
    key: [str] name of item attribute to sort by. All items in the list must
        have this attribute.

    PM
    """
    def __init__(self, key, values=()):
        self.key = key

        super(SortedList, self).__init__(values)
        self.sort(key=operator.attrgetter(self.key))

    def append(self, obj):
        """Append *obj* and sort."""
        super().append(obj)
        self.sort(key=operator.attrgetter(self.key))

    def extend(self, iterable):
        """Extend by appending elements from *iterable* and sort."""
        super().extend(iterable)
        self.sort(key=operator.attrgetter(self.key))


base_date = datetime.datetime(2018, 12, 3, 0, 0)    # arbitrary Monday 0:00
dateFmt = '%a %H:%M'      # format to "Mon 00:00"
def seconds2date(si, *args, base_date=base_date, dateFmt=dateFmt):
    """Convert an amount of seconds *si* (int) since *base_date* (equivalent
    to 0) to a date (str) with format *dateFmt*.

    Parameters:
    si: [int] time in seconds
    base_date: [datetime.datetime]
    dateFmt: [str] suitable for strftime()

    PM
    """
    return (base_date + datetime.timedelta(seconds=si)).strftime(dateFmt)


# Methods to save and load data to and from a json file. Note the types of
# objects that are supported: https://docs.python.org/3/library/json.html.

def save_json(obj, filename):
    """Write python object *obj* to a json file. Overwrite file if it already
    exists (without comfirmation prompt).
    filename: [str] excluding file extension
    """
    filename = filename + '.json'
    with open(filename, "w") as file:
        json.dump(obj, file, indent=4)


def load_json(filename):
    """Read json file and return python object.
    filename: [str] including path, excluding file extension
    """
    filename = filename + '.json'
    with open(filename, encoding='utf-8') as file:
        data = (line.strip() for line in file)
        data_json = "{0}".format(''.join(data))
    return json.loads(data_json)


def get_by_path(root, keypath):
    """Access a nested object in *root* by *keypath* sequence.
    From https://stackoverflow.com/a/14692747
    """
    return reduce(operator.getitem, keypath, root)


def set_by_path(root, keypath, value):
    """Set a *value* in a nested object in *root* by *keypath* sequence.
    From https://stackoverflow.com/a/14692747
    """
    get_by_path(root, keypath[:-1])[keypath[-1]] = value


def deep_merge(d, u):
    """Do a deep merge of one dict into another.

    This will update d with values in u, but will not delete keys in d
    not found in u at some arbitrary depth of d. That is, u is deeply
    merged into d.

    Args -
     d, u: dicts

    Note: this is destructive to d, but not u.

    Returns: None

    From: https://stackoverflow.com/a/52099238
    Notable differences to dict.update():
    - Subdicts at arbitrary levels existing in both dicts are merged instead of
        replaced.
    - An object reference to an existing subdict in d is kept as long as it
        exists in both dicts (consequence of above).
    """
    stack = [(d, u)]
    while stack:
        d, u = stack.pop(0)
        for k, v in u.items():
            if not isinstance(v, Mapping):
                # u[k] is not a dict, nothing to merge, so just set it,
                # regardless if d[k] *was* a dict
                d[k] = v
            else:
                # note: u[k] is a dict

                # get d[k], defaulting to a dict, if it doesn't previously
                # exist
                dv = d.setdefault(k, {})

                if not isinstance(dv, Mapping):
                    # d[k] is not a dict, so just set it to u[k],
                    # overriding whatever it was
                    d[k] = v
                else:
                    # both d[k] and u[k] are dicts, push them on the stack
                    # to merge
                    stack.append((dv, v))
