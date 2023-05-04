# -*- coding: utf-8 -*-
"""Load and check eflips settings.

Documentation of parameters in globalConstants. A default version is saved in
*filename_default*. All new parameters need to be added to the default version
with a default value.

general:
    SIMULATION_TIME: Simulation duration in seconds
    AMBIENT_TEMPERATURE: °C
    AMBIENT_HUMIDITY: not yet used
    LOG_ATTRIBUTES: To log or not to log
    LOG_CONTINUOUSLY: Log every time step or log when a value changes
    LOG_SPECIFIC_STEPS: Log with steplog()
    LOG_COPIES: [bool] if True, the attribute to log is deepcopied. If False
        (default), a reference is logged (take care with mutable objects then).
    DEFAULT_PLOT_SIZE: Default size for plots. [int] for a single value. If
          multiple values in json: [list] of values before (!) conversion.
          [tuple] after loading.
    FLEXPRINT_SWITCHES: To print or not to print with eflips.helperFunctions.
        flexprint. [dict] of switch-bool-pairs

network:
    PASSENGER_WEIGHT: kg
    DELAY_MODE: [str] Consider delay data during simulation ('none',
          'random', 'delaydata')
    RANDOM_DELAY_MIN: Bounds for random delay data generation (only effective
          when 'DELAY_MODE' = 'random')
    RANDOM_DELAY_MAX: see RANDOM_DELAY_MIN
    WAIT_FOR_CHARGING: Always wait for charging to finish, possibly incurring
          delays
    SKIP_CHARGING_WHEN_OCCUPIED: [bool] Skip charging if all slots are taken
          (instead of advancing to a free slot once it becomes available)
    CHARGING_SLOT_TIMEOUT: Default timeout when advancing to free charging
          slot
    DEBUG_MSGS: [bool] Show debug messages

depot:
    vehicle_types: [dict] Properties of vehicles types. Vehicle type names must
        match the labels in trip data. See eflips.depot.simple_vehicle for
        documentation of properties.
        example:
            vehicle_types = {
                'vehicle_type_1': {'battery_capacity': 230, 'CR': 1.15},
                'vehicle_type_2': {'battery_capacity': 300, 'CR': 1.5},
                'vehicle_type_3': {'battery_capacity': 300, 'CR': 1.5},
            }
    substitutable_types: [list] of lists specifying which vehicle types can
        substitute each other on a trip. substititions must be mutual,
        one-sided is not supported. Empty if no substitution.
        substitutable_types is used for parking vehicles. For matching vehicles
        and trips in dispatch, the input values in trip.vehicle_types are used
        instead.
    vehicle_count: [dict] number of vehicles per home depot and vehicle type to
        instantiate. Depot IDs must match IDs in trip data.
        example:
            vehicle_count = {
                'depot_ID_1': {
                    'vehicle_type_1': 10,
                    'vehicle_type_2': 15
                }
            }
    consumption_calc_mode: [str] option how to calculate the energy consumption
    by trips in a depot simulation. Modes:
        'CR_distance_based': calculate with consumption rate in kWh/km
        'CR_time_based': calculate with consumption rate in kWh/hour
        'soc_given': trip.end_soc is given
    prioritize_init_store: [bool] If True, the depot's vehicleStoreInit is
          being emptied before taking vehicles from parking areas. See
          DispatchControl.process_request() for more information. Important:
          If True, the handed over to Timetable must be sufficient to cover
          all requests to vehicleStoreInit without repetitions during the
          simulation.
    allow_negative_soc: [bool] If True, a SimpleBattery's SoC may be negative.
        If False, the SoC is reset to *reset_negative_soc_to* if it falls
        below zero. Simple imitation of opportunity charging on long trips.
    reset_negative_soc_to: [float or int] SoC (0 <= value <= 1) that a battery
        is reset to if the energy level would become negative and
        *allow_negative_soc* is False. Note that the energy level can still
        reach values between 0 and reset_negative_soc_to. Not that SoC is
        calculated with regard to real battery capacity, not nominal.
    energy_reserve: [int or float] percentage of additional energy reserve for
        departure before fully charged.
    log_sl: [bool] switch for logging the sl figure (see
        DepotEvaluation.current_sl)
    sl_period: [int] in seconds for the calculation of stress level
    lead_time_match: [int] standard time prior to sta, when a trip is matched with an vehicle
    path_results: [str] path for exports in evaluation phase
    log_cm_data: [bool] switch for logging data required for exporting the
        charging management input. Automatically set to True if the GUI is on.
    dispatch_retrigger_interval: [None or int] interval in seconds for
        BaseDispatchStrategy.trigger_until_found (tries to find vehicles
        undergoing cancellable processes for delayed trips). If None, no
        trigger is scheduled.
    gui:
        line_break_GUI: [int] sets the linebreak of the parking areas for the GUI
        show_occupancy_rate: [boolean] determines if the occ. rate should be shown after simulation
        offset_line_break: [list] of [boolean] and [int], if [boolean] is true the offset will be for the 2nd and following rows be the [int]
        first_parking_area: [str] the name of the first parking area, if offset_line_brak is false, the 2nd row will start under the first parking area, an area has to be given, if "blocks" wanna be used
        slot_length: the slot length for each vehicle type. vehicle types has to be the same like in vehicle_types
            default: [int] 50 px
        blocks: [list] of [ints]. after each number of areas after the "first_parking_area", the gap is expanded, between the current and the next area. [-1] for disappeling the feature
        distances_between_areas: the gap between two areas
        special_position_areas: OPTIONAL [dict] Assigns x- and y-position to areas (same name like in the template). Overrides the placinge algorythem
            AREA_NAME :
                x:
                y:
        special_orientation_areas: OPTIONAL assigns slot oriantaion to an area (same name like in the template). Overrides the placinge algorythem
            AREA_NAME: ANGLE_LEFT or VERTICAL or HORIZONTAL or ANGLE_RIGHT
    smart_charging:
        start_date: [list] of 3 [ints] Start date for the prices, [YYYY,M,D]
        power_limit_grid: [int or dict] if int, it is the max. power limit for the depot
                        if dict; keys are the times from which a powerlimit is in place, value is the power limit. The last vlaue has to be the same like the first one.
                        if times are not long engouh its starts over again from the begin.
        accuracy: [int] used for some calculations, in percent


rcParams: modifications of matplotlib.rcParams. [dict] that contains
      key-value-pairs such as: 'figure.dpi': 150

evaluationSets: [dict] evaluationSets collects all attributes to log. The
      user can modify/add/remove the evaluation sets and the attributes in
      them. The evaluationSets are used to create an evaluation scheme. Every
      evaluation set consists of two types of attributes:
          "attsToLog_const" are the attributes that do not change, so they
              are logged once
          "attsToLog_time" are logged during the whole simulation duration
      Multiple evaluation sets could be used in a scheme.

evaluationScheme: {dict} The evaluation scheme lets you choose the evaluation
      sets to log.

EXEC_TIME: [str] top-level value appended during loading. Example:
      '2018-11-13_1800'

FILENAME_SETTINGS: [str] top-level value appended during loading. filename
      argument that was passed to load_settings()
"""
import datetime
from eflips.helperFunctions import cm2in, load_json, deep_merge
from matplotlib import rcParams
import os.path


filename_default = 'settings_files\\default'

current_path = os.path.dirname(__file__)
default_settings_file = os.path.join(current_path, os.path.join("settings_files", "default"))

# Get default settings
globalConstants = load_json(default_settings_file)


def load_settings(filename):
    """Load custom eflips settings from a json file.
    The imported data is deeply merged into globalConstants, so it is possible
    to only specify differing values in the custom file.

    filename: [str] excluding file extension
    """
    custom = load_json(filename)
    deep_merge(globalConstants, custom)

    # Append execution time
    globalConstants['EXEC_TIME'] = str(datetime.datetime.now().strftime(
        "%Y-%m-%d_%H%M"))

    # Append filename
    globalConstants['FILENAME_SETTINGS'] = filename

    # Finalize custom values
    if 'DEFAULT_PLOT_SIZE' in custom['general']:
        globalConstants['general']['DEFAULT_PLOT_SIZE'] = cm2in(
            *custom['general']['DEFAULT_PLOT_SIZE'])

    if 'rcParams' in custom:
        rcParams.update(custom['rcParams'])


def reset_settings():
    """Reset the existing globalConstants to contain default values only.

    Note that existing references to objects in globalConstants are not updated
    since values are replaced.
    """
    globalConstants.clear()
    new = load_json(filename_default)
    deep_merge(globalConstants, new)
