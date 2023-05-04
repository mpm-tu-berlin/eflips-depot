"""Utilities for creating json depot templates."""
import json


def save_json(obj, filename):
    """Write python object *obj* to a json file. Overwrite file if it already
    exists (without comfirmation prompt).
    filename: [str] excluding file extension
    """
    filename = filename + '.json'
    with open(filename, "w") as file:
        json.dump(obj, file, indent=4)


total_capacity = 410
arrival_capacity = 232
power = 150


template = {
    'templatename_display': '',
    'general': {
        'depotID': '',
        'dispatch_strategy_name': ''
    },
    'resources': {},
    'resource_switches': {},
    'processes': {},
    'areas': {},
    'groups': {},
    'plans': {}
}


template['templatename_display'] = 'Depot KLS Sept19 all direct'
template['general']['depotID'] = 'KLS'
template['general']['dispatch_strategy_name'] = 'FIRST'
# template['resources']['workers_service'] = {
#             'typename': 'DepotResource',
#             'capacity': 8
#         }

# charging interfaces
ci_all = {}
for i in range(total_capacity):
    ID = 'ci_' + str(i)
    template['resources'][ID] = {
        'typename': 'DepotChargingInterface',
        'max_power': power}
    ci_all[ID] = template['resources'][ID]

# charging switches
cs_all = {}
for i in range(total_capacity):
    ID = 'charging_switch_ci_' + str(i)
    template['resource_switches'][ID] = {
        'resource': 'ci_' + str(i),
        'breaks': [[64800, 72000]],
        'preempt': True,
        'strength': 'full'}
    cs_all[ID] = template['resource_switches'][ID]


template['resource_switches']['charging_switch'] = {
    'resource': 'workers_service',
    'breaks': [[25200, 61200]],
    'preempt': True,
    'strength': 'full'
}

# service switch
# template['resource_switches']['service_switch'] = {
#     'resource': 'workers_service',
#     'breaks': [[25200, 61200]],
#     'preempt': True,
#     'strength': 'full'
# }

template['processes']['charge'] = {
    'typename': 'Charge',
    'ismandatory': False,
    'vehicle_filter': {
        'filter_names': ['soc_lower_than'],
        'soc': 1
    },
    'cancellable_for_dispatch': False
}

# template['processes']['serve'] = {
#     'typename': 'Serve',
#     'dur': 300,
#     'ismandatory': False,
#     'vehicle_filter': {
#         'filter_names': [
#             'in_period'
#         ],
#         'period': [
#             57600,
#             20700
#         ]
#     },
#     'required_resources': [
#         'workers_service'
#     ],
#     'cancellable_for_dispatch': False
# }

template['processes']['standby_arr'] = {
    'typename': 'Standby',
    'dur': 300,
    'ismandatory': True,
    'vehicle_filter': None,
    'required_resources': [],
    'cancellable_for_dispatch': False
}

template['processes']['standby_dep'] = {
    'typename': 'Standby',
    'dur': 900,
    'ismandatory': True,
    'vehicle_filter': None,
    'required_resources': [],
    'cancellable_for_dispatch': False
}

template['areas']['Stauflaeche'] = {
    'typename': 'DirectArea',
    'capacity': arrival_capacity,
    'available_processes': [
        'standby_arr'
    ],
    'issink': False,
    'entry_filter': None
}

# template['areas']['Waschanlage'] = {
#     'typename': 'DirectArea',
#     'capacity': 4,
#     'available_processes': [
#         'serve'
#     ],
#     'issink': False,
#     'entry_filter': None
# }
#
# template['areas']['Grundreinigung'] = {
#     'typename': 'DirectArea',
#     'capacity': 4,
#     'available_processes': [
#         'serve'
#     ],
#     'issink': False,
#     'entry_filter': None
# }

template['areas']['-01-'] = {
    'typename': 'DirectArea',
    'amount': 1,
    'capacity': total_capacity,
    'charging_interfaces': list(ci_all.keys()),
    'available_processes': [
        'charge',
        'standby_dep'
    ],
    'issink': True,
    'entry_filter': None
}

template['groups']['parking area group'] = {
    'typename': 'ParkingAreaGroup',
    'stores': [key for key, value in template['areas'].items() if value['issink']],
    'parking_strategy_name': 'SMART2'
}

template['plans']['default'] = {
    'typename': 'DefaultActivityPlan',
    'locations': [
        'Stauflaeche',
        # 'Waschanlage',
        # 'Grundreinigung',
        'parking area group'
    ]
}


# save_json(template, 'kls_template_2_all_direct')
# save_json(template, 'templates\\kls\\kls_template_2_all_direct')
save_json(template, 'templates\\kls\\test')
