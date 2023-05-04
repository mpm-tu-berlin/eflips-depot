# -*- coding: utf-8 -*-
"""
Created on Wed Sep  6 13:44:16 2017

@author: d.Jefferies

Simple test framework for transport simulation based on SimPy.

"""

from functools import total_ordering
import itertools as it
import math
import pickle
import gzip
import copy

# -----------------------------------------------------------------------------
# Class definitions
# -----------------------------------------------------------------------------

class Ambient:
    """Object with ambient conditions, i.e. weather."""
    def __init__(self, temperature, relHumidity, insolation):
        self.temperature = temperature  # °C
        self.relHumidity = relHumidity  # 0...1
        self.insolation = insolation  # W/m²


class Error(Exception):
    """Base class for error in this module"""


class InputError(Error):
    def __init__(self, message):
        self.message = message


class ModelError(Error):
    def __init__(self, message):
        self.message = message


class ArgumentError(Error):
    def __init__(self, message):
        # self.expression = expression
        self.message = message


class DataError(Error):
    def __init__(self, message):
        # self.expression = expression
        self.message = message


class IDError(Error):
    def __init__(self, message):
        self.message = message


def weekday(weekday):
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    res = 1
    for day in days:
        if day == weekday:
            return res
        res += 1
    return -1

def getNextDay(weekday):
    days = it.cycle(['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'])
    while not next(days) == weekday:
        pass
    return next(days)

@total_ordering
class TimeInfo:
    weekday = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2,
               'Thursday': 3, 'Friday': 4, 'Saturday': 5,
               'Sunday': 6}

    @classmethod
    def weekdayInv(cls):
        return dict(reversed(item) for item in cls.weekday.items())

    @classmethod
    def adjust(cls, newFirstDay):
        shift = 7 - cls.weekday[newFirstDay]
        for day in cls.weekday:
            index = (cls.weekday[day] + shift) % 7
            cls.weekday.update({day: index})

    @classmethod
    def reset(cls):
        cls.weekday = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2,
                       'Thursday': 3, 'Friday': 4, 'Saturday': 5,
                       'Sunday': 6}

    @classmethod
    def firstDay(cls):
        return cls.weekdayInv()[0]

    def __init__(self, day, time):
        # add sanity checking here: day has to be a string conforming to weekday -> done
        if day not in self.weekday:
            raise ValueError("day '%s' is not a valid key in " % day +
                             "TimeInfo.weekday")
        self.day = day
        self.time = time

    def __eq__(self, other):
        return (self.day, self.time) == (other.day, other.time)

    def __nq__(self, other):
        return not self == other

    def __lt__(self, other):
        if self.day == other.day:
            return self.time < other.time
        else:
            return TimeInfo.weekday[self.day] < TimeInfo.weekday[other.day]

    def getSeconds(self):
        secOfDay = 86400
        return (self.weekday[self.day])*secOfDay + self.time

    def __add__(self, other):
        if type(other) == int or type(other) == float:
            res = copy.deepcopy(self)
            res.addSeconds(other)
            return res
        else:
            return NotImplemented

    def __sub__(self, other):
        secOfWeek= 86400*7
        if self < other:
            return ((secOfWeek + self.getSeconds()) - other.getSeconds())
        else:
            return (self.getSeconds() - other.getSeconds())

    def addSeconds(self, seconds):
        # if self.time + seconds < 86400:
        #     self.time = self.time + seconds
        # else:
        offsetDays, newTime = divmod(self.time + seconds, 86400)
        self.time = newTime
        newDay = (TimeInfo.weekday[self.day] + offsetDays) % 7
        self.day = TimeInfo.weekdayInv()[newDay]

    def subSeconds(self, seconds):
        secOfWeek = 86400
        seconds = self.getSeconds() - seconds
        while seconds < 0:
            seconds += secOfWeek
        day, time = divmod(seconds, 86400)
        self.day = TimeInfo.weekdayInv()[day]
        self.time = time


    def delay(self, other):
        if other < self:
            return -1
        else:
            if self.day == other.day:
                return other.time - self.time
            else:
                diff = (TimeInfo.weekday[other.day] - TimeInfo.weekday[
                    self.day] - 1) * 86400  # 86400=24*3600
                diff = diff + (86400 - self.time) + other.time
                return diff

    @property
    def totalSeconds(self):
        """Return total seconds since the beginning of the first day."""
        return self.time + TimeInfo.weekday[self.day]*86400

    def __abs__(self):
        return self.totalSeconds

    def hms(self):
        """Return hours, minutes and seconds"""
        hour = math.floor(self.time / 3600)
        minute = math.floor((self.time-hour*3600)/60)
        second = self.time-hour*3600-minute*60
        return hour, minute, second

    def toString(self):
        """Return a string of the form 'Tuesday 16:03:41'"""
        (hour, minute, second) = self.hms()
        day = self.day
        return day + ' ' + '{:02.0f}'.format(hour) + ':'\
               + '{:02.0f}'.format(minute) + ':' \
               + '{:02.0f}'.format(second)

    def toString_short(self):
        """Return a string of the form 'Tue 16:03'"""
        (hour, minute, second) = self.hms()
        day_short = {'Monday': 'Mon',
                     'Tuesday': 'Tue',
                     'Wednesday': 'Wed',
                     'Thursday': 'Thu',
                     'Friday': 'Fri',
                     'Saturday': 'Sat',
                     'Sunday': 'Sun'}
        day = day_short[self.day]
        return day + ' ' + '{:02.0f}'.format(hour) + ':'\
               + '{:02.0f}'.format(minute)

def translate(word, dictionary):
    try:
        return dictionary[word]
    except (KeyError, TypeError):
        return word

def storeVehicleParams(vehicleParams, path, file):
    filepath = path + '\\' + file
    with gzip.open(filepath, mode='wb') as file:
        file.write(pickle.dumps(vehicleParams))

def loadVehicleParams(path, file):
    filepath = path + '\\' + file
    with gzip.open(filepath, mode='rb') as file:
        vehicleParams = pickle.loads(file.read())
    return vehicleParams

def convertLineNumber(number):
    convertDict = {'X':'5', 'M':'8', 'N':'9'}
    if number[0] in convertDict:
        number = convertDict[number[0]] + number[1:]
    return number

def translateDay(day):
    d = {'Monday':'Montag', 'Tuesday':'Dienstag', 'Wednesday':'Mittwoch',
         'Thursday':'Donnerstag', 'Friday':'Freitag', 'Saturday':'Samstag',
         'Sunday':'Sonntag'}
    return translate(day,d)
