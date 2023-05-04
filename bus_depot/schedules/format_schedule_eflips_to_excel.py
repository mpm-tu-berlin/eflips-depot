import eflips
import pandas as pd
import tkinter as tk

"""Import script for schedules from eFLIPS"""

root = tk.Tk()
root.withdraw()

# path = filedialog.askopenfilename()
# safe_path = filedialog.asksaveasfilename()

path = (r"d:\Uni\Dissertation\Umlaufpläne DJ\scenario4_EN_DC_GN_DC_simdata_Schedule_Simulation_with_buffer_07_delayed.dat")
safe_path =(r"d:\Uni\Dissertation\Umlaufpläne DJ\scenario4_EN_DC_GN_DC_simdata_Schedule_Simulation_with_buffer_07_delayed.xlsx")

shell = eflips.io.import_pickle(path)

vehicles = shell['logger_data']["vehicles"]
columns_typ = ['capacity_max','soc_reserve','soc_min','soc_max','discharge_rate','charge_rate'] #all data comes from batery dict
vehicle_types = pd.DataFrame(columns=columns_typ)
columns_schedule = ['line_name','origin','destination','vehicle_types','std [s]','sta [s]', 'distance [km]','average_consumption','start_soc','end_soc','charge_on_track']
schedule = pd.DataFrame(columns=columns_schedule)


for vehicle_number, vehicle_value in vehicles.items(): #each vehicle
    ID = str(vehicle_number)
    vehicle_type = vehicle_value['params']['vehicle_type'].name

    if vehicle_type not in list(vehicle_types.index.values): # check if vehicle Type is already imported
        for data in columns_typ:
            vehicle_types.loc[vehicle_type,data] = vehicle_value['params']['vehicle_type'].params['battery'][data] #writes the data from battery into the pd frame

    lines = str()
    for line in vehicle_value["mission_list"][0].root_node.lines:
        lines += line + " /"
    schedule.loc[ID, 'line_name'] = lines[0:len(lines)-2]

    schedule.loc[ID, 'origin'] = vehicle_value["mission_list"][0].root_node.origin.short_name
    schedule.loc[ID, 'destination'] = vehicle_value["mission_list"][0].root_node.destination.short_name

    item = vehicle_value['log_data']['soc_primary']['values'].popitem(last=False)
    schedule.loc[ID,'std [s]'] = int(item[0]) #start time
    start_soc = item[1]
    schedule.loc[ID, 'start_soc'] = start_soc

    item = vehicle_value['log_data']['soc_primary']['values'].popitem(last=True)
    schedule.loc[ID, 'sta [s]'] = int(item[0]) #end time
    end_soc = item[1]
    schedule.loc[ID, 'end_soc'] = end_soc


    if "pantograph" in vehicle_value["params"]["vehicle_type"].params["charging_interfaces"]:
        schedule.loc[ID,'charge_on_track'] = True
    else:
        schedule.loc[ID, 'charge_on_track'] = False

    schedule.loc[ID,'vehicle_types'] = vehicle_type

    item = vehicle_value['log_data']['odo']['values'].popitem(last=True)
    schedule.loc[ID, 'distance [km]'] = item[1]

    item = vehicle_value['log_data']['specific_consumption_primary']['values'].popitem(last=True)
    schedule.loc[ID, 'average_consumption'] = item[1]

with pd.ExcelWriter(safe_path) as writer:  # doctest: +SKIP
    schedule.to_excel(writer, sheet_name='Tripdata',index_label='ID')
    vehicle_types.to_excel(writer, sheet_name='vehicle_types')
