"""
@author: B.Bober

Popup Window for adding a new depot area to the depot.

+++ OUTDATED +++

For more explanation about the .NET code see DepotView class.
"""
from os.path import isfile, join

import eflips.depot.gui.depot_view
from eflips.depot.gui.depot_view import *


class NewAreaWindow(Window):
    def __init__(self, parent):
        self.parent = parent

        stream = StreamReader(parent.path_root + "eflips\\depot\\gui\\newarea_view.xaml")
        self.Window = XamlReader.Load(stream.BaseStream)
        self.Owner = parent.Window
        
        # general
        self.TxtAreaName = self.Window.FindName("TxtAreaName")
        self.TxtShortName = self.Window.FindName("TxtShortName")

        self.ChkVehicleType = self.Window.FindName("ChkVehicleType")
        self.ChkVehicleType_DL = self.Window.FindName("ChkVehicleType_DL")
        self.ChkVehicleType_EN = self.Window.FindName("ChkVehicleType_EN")
        self.ChkVehicleType_GN = self.Window.FindName("ChkVehicleType_GN")

        # charging
        self.ChkChargingModule = self.Window.FindName("ChkChargingModule")
        self.ChkCharging_Mandatory = self.Window.FindName("ChkCharging_Mandatory")
        self.TxtNoChargingSlots_Plug = self.Window.FindName("TxtNoChargingSlots_Plug")
        self.TxtNoChargingSlots_Induction = self.Window.FindName("TxtNoChargingSlots_Induction")
        self.TxtNoChargingSlots_Overhead = self.Window.FindName("TxtNoChargingSlots_Overhead")        

        # maintenance
        self.ChkMaintenanceModule = self.Window.FindName("ChkMaintenanceModule")
        self.ChkMaintenance_Mandatory = self.Window.FindName("ChkMaintenance_Mandatory")

        # repair
        self.ChkRepairModule = self.Window.FindName("ChkRepairModule")
        self.ChkRepair_Mandatory = self.Window.FindName("ChkRepair_Mandatory")

        # service
        self.ChkServiceModule = self.Window.FindName("ChkServiceModule")
        self.ChkService_Mandatory = self.Window.FindName("ChkService_Mandatory")

        # standby
        self.ChkStandbyArrival = self.Window.FindName("ChkStandbyArrival")
        self.ChkStandbyDeparture = self.Window.FindName("ChkStandbyDeparture")

        # vehicle slots
        self.ChkIsSink = self.Window.FindName("ChkIsSink")
        self.TxtNoVehicleSlots = self.Window.FindName("TxtNoVehicleSlots")

        self.CboParkingOrientation = self.Window.FindName("CboParkingOrientation")
        availableOrientations = eflips.depot.gui.depot_view.getAvailableParkingOrientations()
        itemList = ArrayList()
        for key in availableOrientations:
            item = ArrayList()
            item.Add(key)
            item.Add(availableOrientations[key])
            self.CboParkingOrientation.Items.Add(item)
        self.CboParkingOrientation.SelectedIndex = 0

        # outdated
        # self.CboAccessMode = self.Window.FindName("CboAccessMode")
        # availableAreaStrategies = eflips.depot.BaseDepotArea.getAvailableParkingStrategies()
        # itemList = ArrayList()
        # for key in availableAreaStrategies:
        #     item = ArrayList()
        #     item.Add(key)
        #     item.Add(availableAreaStrategies[key])
        #     self.CboAccessMode.Items.Add(item)
        # self.CboAccessMode.SelectedIndex = 0

        self.CboChargingType = self.Window.FindName("CboChargingType")
        availableChargingInterfaces = eflips.depot.gui.depot_view.getAvailableChargingInterfaces()
        itemList = ArrayList()
        for key in availableChargingInterfaces:
            item = ArrayList()
            item.Add(key)
            item.Add(availableChargingInterfaces[key])
            self.CboChargingType.Items.Add(item)
        self.CboChargingType.SelectedIndex = 0

        # functional
        self.LbTemplates = self.Window.FindName("LbTemplates")
        self.LbTemplates.SelectionChanged += self.LbTemplates_SelectionChanged
        self.LbTemplates.MouseDoubleClick += self.LbTemplates_MouseDoubleClick

        self.BtnSave = self.Window.FindName("BtnSave")
        self.BtnSave.Click += self.BtnSave_Click

        self.BtnRemove = self.Window.FindName("BtnRemove")
        self.BtnRemove.Click += self.BtnRemove_Click

        self.BtnOk = self.Window.FindName("BtnOk")
        self.BtnOk.Click += self.BtnOk_Click

        self.listTemplates()

    #region control eventhandlers
    def BtnSave_Click(self, sender, eventArgs):
        from eflips.depot.gui.inputbox import InputBox
        inputBox = InputBox(self, "Save Template", "Please enter a name for your template.", self.TxtAreaName.Text)
        if inputBox.show():
            self.saveTemplate(inputBox.returnValue + ".json")
        inputBox = None        

    def BtnRemove_Click(self, sender, eventArgs):
        if self.LbTemplates.SelectedItem != None:
            self.removeTemplate(self.LbTemplates.SelectedItem)

    def BtnOk_Click(self, sender, eventArgs):
        self.addDepotArea()

    def LbTemplates_SelectionChanged(self, sender, eventArgs):
        if self.LbTemplates.SelectedItem != None:
            self.loadTemplate(self.LbTemplates.SelectedItem)

    def LbTemplates_MouseDoubleClick(self, sender, eventArgs):
        if self.LbTemplates.SelectedItem != None:
            self.loadTemplate(self.LbTemplates.SelectedItem)
            self.addDepotArea()
    #endregion

    def show(self):
        return self.Window.ShowDialog()

    def addDepotArea(self):
        """
        This is the actual dialog return method for this window. Called by BtnOk_Click().
        Will only be successful if there is no existing area with the same name already.
        """
        if not self.TxtAreaName.Text in self.parent.areas:
            self.returnValue = self.createDepotArea()
            self.Window.DialogResult = System.Nullable[System.Boolean](True)
        else:
            MessageBox.Show("An area with the same name already exists.", "Depot GUI", MessageBoxButton.OK, MessageBoxImage.Exclamation)

    def createDepotArea(self):
        """
        Returns an instance of LineArea or DirectArea according to the current settings.
        """
        #generic properties
        areaNo = None
        ID = self.TxtAreaName.Text
        capacity = int(self.TxtNoVehicleSlots.Text) if eflips.depot.gui.depot_view.isNumeric(self.TxtNoVehicleSlots.Text) else 1 # 1 is min!

        #entryconditions
        desiredVehicleTypes = None
        if self.ChkVehicleType.IsChecked:
            desiredVehicleTypes = []
            if self.ChkVehicleType_DL.IsChecked:
                desiredVehicleTypes.append("DL")
            if self.ChkVehicleType_EN.IsChecked:
                desiredVehicleTypes.append("EN")
            if self.ChkVehicleType_GN.IsChecked:
                desiredVehicleTypes.append("GN")
        
        entry_filter = eflips.depot.depot.VehicleFilter() if desiredVehicleTypes == None else eflips.depot.depot.VehicleFilter(filter_names=['vehicle_type'], vehicle_types=desiredVehicleTypes)

        #parkinglot / issink
        issink = self.ChkIsSink.IsChecked

        # outdated
        # accessMode = self.CboAccessMode.SelectedItem[0] if self.CboAccessMode.Visibility == Visibility.Visible and self.CboAccessMode.SelectedItem != None else 'fifo'

        #available procs
        procSet = self.parent.depot.build.select_procSet(-1, self.parent.env)

        available_processes = {}
        if self.ChkChargingModule.IsChecked:
            if self.ChkCharging_Mandatory.IsChecked:
                available_processes[procSet['charge']] = True
            else:
                available_processes[procSet['charge']] = False

        if self.ChkMaintenanceModule.IsChecked:
            if self.ChkMaintenance_Mandatory.IsChecked:
                available_processes[procSet['maintain']] = True
            else:
                available_processes[procSet['maintain']] = False

        if self.ChkRepairModule.IsChecked:
            if self.ChkRepair_Mandatory.IsChecked:
                available_processes[procSet['repair']] = True
            else:
                available_processes[procSet['repair']] = False

        if self.ChkServiceModule.IsChecked:
            if self.ChkService_Mandatory.IsChecked:
                available_processes[procSet['serve']] = True
            else:
                available_processes[procSet['serve']] = False

        if self.ChkStandbyArrival.IsChecked:
            available_processes[procSet['standbyArr']] = True

        if self.ChkStandbyDeparture.IsChecked:
            available_processes[procSet['standbyDep']] = True

        for process in available_processes:
            process.ismandatory = available_processes[process]

        #charging interface
        noSlots_Plug = int(self.TxtNoChargingSlots_Plug.Text) if self.ChkChargingModule.IsChecked and eflips.depot.gui.depot_view.isNumeric(self.TxtNoChargingSlots_Plug.Text) else 0
        noSlots_Induction = int(self.TxtNoChargingSlots_Induction.Text) if self.ChkChargingModule.IsChecked and eflips.depot.gui.depot_view.isNumeric(self.TxtNoChargingSlots_Induction.Text) else 0
        noSlots_Overhead = int(self.TxtNoChargingSlots_Overhead.Text) if self.ChkChargingModule.IsChecked and eflips.depot.gui.depot_view.isNumeric(self.TxtNoChargingSlots_Overhead.Text) else 0

        if self.ChkIsSink.IsChecked and self.CboParkingOrientation.SelectedItem != None and self.CboParkingOrientation.SelectedItem[0] == "VERTICAL":
            # parking lot with blocking vehicles
            newArea = eflips.depot.depot.LineArea(env=self.parent.env, areaNo = areaNo, ID = ID,
                                                  capacity = capacity, chIntConfig = [noSlots_Plug, noSlots_Induction, noSlots_Overhead],
                                                  available_processes = list(available_processes.keys()),
                                                  issink = issink, entry_filter = entry_filter, accessModeStd=accessMode)
        else:
            # conventional depotarea or parking lot with non-blocking vehicles
            newArea = eflips.depot.depot.DirectArea(env=self.parent.env, areaNo = areaNo, ID = ID,
                                                    capacity = capacity, chIntConfig = [noSlots_Plug, noSlots_Induction, noSlots_Overhead],
                                                    available_processes = list(available_processes.keys()),
                                                    issink = issink, entry_filter = entry_filter)

        newArea.shortName = self.TxtShortName.Text
        newArea.slot_orientation = self.CboParkingOrientation.SelectedItem[0] if self.ChkIsSink.IsChecked and self.CboParkingOrientation.SelectedItem != None else "HORIZONTAL"
        newArea.assignmentGroup = None
        return newArea

    #region template methods
    def toJson(self):
        return DepotAreaView(self.createDepotArea(), self.parent).toJson() #workaround to save some code

    def listTemplates(self):
        """
        Lists all json templates from [path] (see below) in LbTemplates.
        """
        self.LbTemplates.Items.Clear()
        path = self.parent.path_templates + "depotareas\\"
        if not os.path.exists(path):
            os.makedirs(path)

        for filename in os.listdir(path):
            if isfile(join(path, filename)) and filename.endswith(".json"):
                self.LbTemplates.Items.Add(filename)

    def loadTemplate(self, filename):
        """
        Loads a json template from the [filename] source and sets the controls on this window accordingly.
        """
        try:
            depotArea = eflips.depot.gui.depot_view.readJsonFile(self.parent.path_templates + "depotareas\\" + filename)
            if depotArea and depotArea[0] != None:
                depotArea = depotArea[0]

                if "ID" in depotArea:
                    self.TxtAreaName.Text = depotArea["ID"]

                if "shortName" in depotArea:
                    self.TxtShortName.Text = depotArea["shortName"]

                if "capacity" in depotArea:
                    self.TxtNoVehicleSlots.Text = str(depotArea["capacity"])

                self.ChkVehicleType.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool("entry_filter" in depotArea and depotArea["entry_filter"] and len(depotArea["entry_filter"]) > 0)
                self.ChkVehicleType_DL.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(self.ChkVehicleType.IsChecked and "DL" in depotArea["entry_filter"])
                self.ChkVehicleType_EN.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(self.ChkVehicleType.IsChecked and "EN" in depotArea["entry_filter"])
                self.ChkVehicleType_GN.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(self.ChkVehicleType.IsChecked and "GN" in depotArea["entry_filter"])

                self.ChkIsSink.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool("issink" in depotArea and depotArea["issink"])

                orientationItem = eflips.depot.gui.depot_view.findComboboxItem(self.CboParkingOrientation, depotArea["slot_orientation"] if "slot_orientation" in depotArea else None)
                self.CboParkingOrientation.SelectedItem = orientationItem if orientationItem != None else self.CboParkingOrientation.Items[0]

                # outdated
                # accessModeItem = eflips.depot.gui.depotView.findComboboxItem(self.CboAccessMode, depotArea["accessModeStd"] if "accessModeStd" in depotArea else None)
                # self.CboAccessMode.SelectedItem = accessModeItem if accessModeItem != None else self.CboAccessMode.Items[0]

                self.TxtNoChargingSlots_Plug.Text = str(depotArea["chIntConfig"][0]) if "chIntConfig" in depotArea and self.ChkChargingModule.IsChecked else "0"
                self.TxtNoChargingSlots_Induction.Text = str(depotArea["chIntConfig"][1]) if "chIntConfig" in depotArea and self.ChkChargingModule.IsChecked else "0"
                self.TxtNoChargingSlots_Overhead.Text = str(depotArea["chIntConfig"][2]) if "chIntConfig" in depotArea and self.ChkChargingModule.IsChecked else "0"

                self.ChkChargingModule.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool("processConfig" in depotArea and "charge" in depotArea["processConfig"])
                self.ChkCharging_Mandatory.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(self.ChkChargingModule.IsChecked and depotArea["processConfig"]["charge"] == 2)

                self.ChkMaintenanceModule.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool("processConfig" in depotArea and "maintain" in depotArea["processConfig"])
                self.ChkMaintenance_Mandatory.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(self.ChkMaintenanceModule.IsChecked and depotArea["processConfig"]["maintain"] == 2)

                self.ChkRepairModule.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool("processConfig" in depotArea and "repair" in depotArea["processConfig"])
                self.ChkRepair_Mandatory.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(self.ChkRepairModule.IsChecked and depotArea["processConfig"]["repair"] == 2)

                self.ChkServiceModule.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool("processConfig" in depotArea and "serve" in depotArea["processConfig"])
                self.ChkService_Mandatory.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(self.ChkServiceModule.IsChecked and depotArea["processConfig"]["serve"] == 2)

                self.ChkStandbyArrival.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool("processConfig" in depotArea and "standbyArr" in depotArea["processConfig"])
                self.ChkStandbyDeparture.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool("processConfig" in depotArea and "standbyDep" in depotArea["processConfig"])

                return True
        except:
            traceback.print_exc()
            MessageBox.Show("Error while loading template!", "Depot GUI - Save Template", MessageBoxButton.OK, MessageBoxImage.Error)
        return False

    def saveTemplate(self, filename):
        """
        Saves a json template to the [filename] target location according to the settings made on this window.
        """
        if filename != None:
            result = eflips.depot.gui.depot_view.writeJsonFile(self.parent.path_templates + "depotareas\\" + filename, self.toJson())
            if result == 1:
                MessageBox.Show("Template saved successfully.", "Depot GUI - Save Template", MessageBoxButton.OK, MessageBoxImage.Information)
                self.listTemplates()
            elif result == -1:
                MessageBox.Show("Error while saving template!", "Depot GUI - Save Template", MessageBoxButton.OK, MessageBoxImage.Error)

    def removeTemplate(self, filename):
        """
        Removes the currently selected template in the list.
        Asks for confirmation.
        """
        filename = self.parent.path_templates + "depotareas\\" + filename
        if os.path.exists(filename) and isfile(filename) and MessageBox.Show("Do you really want to delete this template?", "Depot GUI - Confirm Delete Template", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes:
            os.remove(filename)
            self.listTemplates()
    #endregion