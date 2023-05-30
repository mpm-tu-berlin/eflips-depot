"""
@author: B.Bober, e.lauth

Main GUI code.

You will find some explanation about the .NET (C#) code here.
This information is valid for any of the other GUI classes and marked with a [.NET EXPLANATION] tag.

General stuff:

-   System.Windows.Forms.Application.DoEvents():    Creates an interrupt in order to suspend running threads until all
                                                    windows messages currently queued have been processed. This causes the
                                                    UI to refresh and is actually merely a workaround for single-threaded applications.


Additional important information:

-   In the current state, the simulation controls (speed and pause) depend on the log() method!
    Until eventually a better way has been found, a .NET Thread.Sleep() is hooked to any incoming log call for the speed
    setting or an infinite while loop for the pause function respectively.
"""

# Python imports
import datetime
import os
import traceback

# .NET framework (C#/WPF suppport)
# prerequisites:
# 1) .NET framework to be installed (requires windows OS)
# 2) python package: pythonnet ("pip install pythonnet")
# 3) python package: sometimes the clr-package needs to be installed and deinstalled after. reasons unknown.
import clr

clr.AddReference("PresentationFramework, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35")

# C#/WPF imports (requires .NET framework, GUI related)
import System
from System import *
from System.Drawing import Point
from System.IO import StreamReader
from System.Collections import *
from System.Collections.ObjectModel import *
from System.ComponentModel import *
from System.Windows import *
from System.Windows.Controls import *
from System.Windows.Data import *
from System.Windows.Input import MouseButtonState
from System.Windows.Markup import XamlReader
from System.Windows.Media import *
from System.Windows.Media.Effects import *
from System.Threading import *

# project imports
from eflips import globalConstants
from eflips.depot.gui.area_view import DepotAreaView, VehicleSlotView
from eflips.depot.gui.newarea_view import NewAreaWindow
from eflips.depot.gui.settings_view import SettingsWindow
from eflips.depot.simulation import SimulationHost, Depotinput
from eflips.helperFunctions import seconds2date

inputs = {'filename_eflips_settings': None,
          'filename_timetable': None}

# global variable which contains DepotView instance (main GUI window)
main_view = None


CHARGINGINTERFACE_INDUCTION = "INDUCTION"
CHARGINGINTERFACE_OVERHEAD = "OVERHEAD"
CHARGINGINTERFACE_PLUG = "PLUG"


def getAvailableChargingInterfaces():
    # backend key : displaydescription
    return {CHARGINGINTERFACE_INDUCTION: "induction",
            CHARGINGINTERFACE_OVERHEAD: "overhead",
            CHARGINGINTERFACE_PLUG: "plug"}


PARKINGORIENTATION_HORIZONTAL = "HORIZONTAL"
PARKINGORIENTATION_VERTICAL = "VERTICAL"
PARKINGORIENTATION_ANGLE_LEFT = "ANGLE_LEFT"
PARKINGORIENTATION_ANGLE_RIGHT = "ANGLE_RIGHT"


def getAvailableParkingOrientations():
    # backend key : displaydescription
    return {PARKINGORIENTATION_HORIZONTAL: "horizontal",
            PARKINGORIENTATION_VERTICAL: "vertical",
            PARKINGORIENTATION_ANGLE_LEFT: "angle left (45 deg)",
            PARKINGORIENTATION_ANGLE_RIGHT: "angle right (45 deg)"}


def convertToNullableBool(value):
    """
    Converts a python boolean value to .NET nullable boolean (3-state boolean), neccessary for frontend interaction.
    """
    return System.Nullable[System.Boolean](True) if (isinstance(value, str) and value.upper() == "TRUE") or (isinstance(value, int) and value == 1) else System.Nullable[System.Boolean](False)


def findComboboxItem(cb, description):
    """
    Returns item from [cb].Items if item.Text == [description].
    """

    if description is not None:
        try:
            if cb.Items is not None:
                for cbItem in cb.Items:
                    if cbItem.ToString() == description:
                        return cbItem
        except:
            traceback.print_exc()
    return None


def isNumeric(stringValue):
    try:
        val = int(stringValue)
        return True
    except ValueError:
        return False


class DepotView(Window):
    """
    Main GUI class, inherits from .NET WPF [Window] class.
    """

    __namespace__ = "eflips"

    def __init__(self):
        print("Success.")

        # relative path workaround (visual studio vs. pycharm)
        self.path_root = ""
        self.path_results = "eflips\\depot\\results\\"
        self.path_templates = "bus_depot\\templates\\"
        if os.path.exists("eflips"):
            print("Application root is 'eFLIPS' (Visual Studio).")
        else:
            self.path_root = "..\\"
            self.path_results = "..\\eflips\\depot\\results\\"
            self.path_templates = "..\\bus_depot\\templates\\"
            print("Application root is 'bus_depot' (PyCharm).")

        if not os.path.exists(self.path_results):
            os.makedirs(self.path_results)
        if not os.path.exists(self.path_templates):
            os.makedirs(self.path_templates)

        global main_view
        main_view = self

        self.propertyFocus = None

        self.isSimulationRunning = False
        self.isSimulationPaused = False
        self.simulationDeceleration = 100 #milliseconds between steps

        self.show_properties = True
        self.show_timetable = False

        self.listLog = ObservableCollection[ArrayList]()
        
        # default group color definitions
        self.groupColors = {}
        self.groupColors[0] = [0, 130, 200]     #blue
        self.groupColors[1] = [245, 222, 179]   #wheat
        self.groupColors[2] = [0, 128, 128]     #teal
        self.groupColors[3] = [170, 110, 40]    #brown
        self.groupColors[4] = [240, 128, 128]   #lightcoral
        self.nextGroupColor = 0

        # Attributes for short access to backend objects. Set in
        # init_simulation
        self.simulation_host = None
        self.env = None
        self.timetable = None
        self.depot = None
        self.areas = None
        self.configurator = None
        self.evaluation = None

        """
        [.NET EXPLANATION]
        GUI initialization (XAML)

        The following code initializes the XAML and creates a link between the GUI objects and the python backend code,
        since the GUI is intentionally separate from the python code after the XAML has been compiled (MVVM).

        Control link default pattern:
        [python attribute name] = self.Window.FindName("[XAML control "Name" property]")

        In most cases we need one or more event handler(s) for control events like mouse clicks; default pattern:
        [python attribute name].[control event] += [event handler method name]
        """
        stream = StreamReader(self.path_root + "eflips\\depot\\gui\\depot_view.xaml")
        self.Window = XamlReader.Load(stream.BaseStream)
        self.Window.Loaded += self.Window_Loaded
        self.Window.Closing += self.Window_Closing

        self.MbnClose = self.Window.FindName("MbnClose")
        self.MbnClose.Click += self.MbnClose_Click

        self.MbnStartSimulation = self.Window.FindName("MbnStartSimulation")
        self.MbnStartSimulation.Click += self.MbnStartSimulation_Click

        self.MbnView = self.Window.FindName("MbnView")
        self.MbnShowProperties = self.Window.FindName("MbnShowProperties")
        self.MbnShowProperties.Click += self.MbnShowProperties_Click

        self.MbnShowTimeTable = self.Window.FindName("MbnShowTimeTable")
        self.MbnShowTimeTable.Click += self.MbnShowTimeTable_Click
      
        self.MbnAddArea = self.Window.FindName("MbnAddArea")
        self.MbnAddArea.Click += self.MbnAddArea_Click

        self.MbnDepot = self.Window.FindName("MbnDepot")

        self.MbnSettings = self.Window.FindName("MbnSettings")
        self.MbnSettings.Click += self.MbnSettings_Click

        self.MbnResetDepot = self.Window.FindName("MbnResetDepot")
        self.MbnResetDepot.Click += self.MbnResetDepot_Click

        self.MbnAnalysis = self.Window.FindName("MbnAnalysis")        
        
        self.MbnExportAnalysis = self.Window.FindName("MbnExportAnalysis")
        self.MbnExportAnalysis.Click += self.MbnExportAnalysis_Click

        #self.MbnTest1 = self.Window.FindName("MbnTest1")
        #self.MbnTest1.Click += self.MbnTest1_Click

        #self.MbnTest2 = self.Window.FindName("MbnTest2")
        #self.MbnTest2.Click += self.MbnTest2_Click

        #self.MbnTest3 = self.Window.FindName("MbnTest3")
        #self.MbnTest3.Click += self.MbnTest3_Click

        self.PnlSimEnd = self.Window.FindName("PnlSimEnd")

        self.BtnStart = self.Window.FindName("BtnStart")
        self.BtnStart.Click += self.BtnStart_Click

        self.BtnReset = self.Window.FindName("BtnReset")
        self.BtnReset.Click += self.BtnReset_Click

        self.SldDeceleration = self.Window.FindName("SldDeceleration")
        self.SldDeceleration.ValueChanged += self.SldDeceleration_ValueChanged

        self.LvLog = self.Window.FindName("LvLog")
        self.LvLog.ItemsSource = self.listLog

        self.GbxDepotProperties = self.Window.FindName("GbxDepotProperties")
        self.LvDepotProperties = self.Window.FindName("LvDepotProperties")

        self.GbxItemProperties = self.Window.FindName("GbxItemProperties")
        self.LvItemProperties = self.Window.FindName("LvItemProperties")

        self.GbxTimeTable = self.Window.FindName("GbxTimeTable")
        self.LvTimeTable = self.Window.FindName("LvTimeTable")

        self.BrdMain = self.Window.FindName("BrdMain")
        self.VisualizationArea = self.Window.FindName("VisualizationArea")
        self.VisualizationArea.MouseMove += self.DepotArea_MouseMove
        self.VisualizationArea.PreviewMouseUp += self.DepotArea_PreviewMouseUp

        self.GbxDeceleration = self.Window.FindName("GbxDeceleration")

        self.GSMain = self.Window.FindName("GSMain")
        self.GSDetailsH = self.Window.FindName("GSDetailsH")
        self.GSDetailsV = self.Window.FindName("GSDetailsV")

        Application().Run(self.Window)
        # ATTENTION: no code will be processed from here on. STA thread is occupied by application window. use Window_Loaded() instead.


    #region control eventhandlers
    def Window_Loaded(self, sender, eventArgs):
        """
        [.NET EXPLANATION]
        control event handler default pattern:
        [method name](self, sender, eventArgs)

        sender:     the sender of the event (in most cases like mouse clicks its the control itself)
        eventArgs:  additional event arguments

        Note: Both arguments are mandatory. You might rename them, but they must exist.
        The method name is arbitrary, but must equal to the name declared while GUI initialization inside of the constructor (above).
        """

        self.init_simulation()
        self.log("INFO", "Init finished.")

    def Window_Closing(self, sender, eventArgs):
        self.exit()

    def MbnStartSimulation_Click(self, sender, eventArgs):
        self.start_simulation()

    def MbnShowProperties_Click(self, sender, eventArgs):
        self.show_properties = not self.show_properties
        self.MbnShowProperties.IsChecked = convertToNullableBool(self.show_properties)
        
        self.resetDetails()

    def MbnShowTimeTable_Click(self, sender, eventArgs):
        self.show_timetable = not self.show_timetable
        self.MbnShowTimeTable.IsChecked = convertToNullableBool(self.show_timetable)

        self.resetDetails()

    def MbnAddArea_Click(self, sender, eventArgs):
        """Unused until settings GUI update"""
        newAreaWindow = NewAreaWindow(self)
        depotArea = None

        if newAreaWindow.show():
            depotArea = newAreaWindow.returnValue
        newAreaWindow = None

        if depotArea is not None:
            self.addAreaToFrontend(depotArea)

    def MbnSettings_Click(self, sender, eventArgs):
        settingsWindow = SettingsWindow(self)
        settingsWindow.show()

    def MbnResetDepot_Click(self, sender, eventArgs):
        if MessageBox.Show("Do you really want to reset this depot?", "Depot GUI - Reset Depot", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes:
            self.resetDepot()

    def MbnExportAnalysis_Click(self, sender, eventArgs):
        from eflips.depot.gui.inputbox import InputBox
        inputBox = InputBox(self, "Export Simulation Analysis", "Please enter a name for this export.", self.evaluation.cm_report.defaultname)
        if inputBox.show():
            rv = inputBox.returnValue
            if rv:
                success = self.evaluation.cm_report.export_logs(rv)

                if success:
                    MessageBox.Show(
                        "Vehicle event and trip data exported to %s."
                        % (self.path_results + rv + '.xls'),
                        "Depot GUI - Depot Analysis", MessageBoxButton.OK,
                        MessageBoxImage.Information)
                else:
                    MessageBox.Show("Error while exporting depot analysis!",
                                    "Depot GUI - Depot Analysis",
                                    MessageBoxButton.OK, MessageBoxImage.Error)
        inputBox = None   

    def MbnTest1_Click(self, sender, eventArgs):
        pass

    def MbnTest2_Click(self, sender, eventArgs):
        pass

    def MbnTest3_Click(self, sender, eventArgs):
        pass

    def MbnClose_Click(self, sender, eventArgs):
        self.exit()

    def MbnRestart_Click(self, sender, eventArgs):
        #experimental restart code
        #path = "\"" + sys.executable + "\" \"" + os.getcwd() + "\bvg_depot\STARTSIM_bsrdepot.py\""
        #print(path)
        #System.Diagnostics.Process.Start(path);
        self.exit()

    def BtnStart_Click(self, sender, eventArgs):
        if self.isSimulationRunning:
            if self.isSimulationPaused:
                self.BtnStart.Content = "Pause"
                self.isSimulationPaused = False
            else:
                self.BtnStart.Content = "Continue"
                self.isSimulationPaused = True
        else:
            self.start_simulation()

    def BtnReset_Click(self, sender, eventArgs):
        self.resetSimulation()

    def SldDeceleration_ValueChanged(self, sender, eventArgs):
        self.simulationDeceleration = int(self.SldDeceleration.Value)
        self.GbxDeceleration.Header = "Simulation Deceleration: %dms / step" % self.simulationDeceleration

    def DepotArea_MouseMove(self, sender, mouseEventArgs):
        """
        Used for moving depot areas visually on the screen.
        """
        if mouseEventArgs.LeftButton == MouseButtonState.Pressed:
            if not hasattr(self, "dragItem") or self.dragItem == None:
                depotArea = None
                if type(mouseEventArgs.Source) == type(Grid()):
                    if mouseEventArgs.Source.Name != "":
                        depotArea = mouseEventArgs.Source
                if depotArea == None:
                    depotArea = DepotView.findAncestor(mouseEventArgs.Source, Grid())

                if depotArea is not None:
                    self.dragItem = self.getAreaByViewName(depotArea.Name).view
                    self.dragStartPosition = mouseEventArgs.GetPosition(self.dragItem.Border)
                    self.dragStartPosition.X += self.dragItem.Border.Margin.Left
                    self.dragStartPosition.Y += self.dragItem.Border.Margin.Top                

            if hasattr(self, "dragItem") and self.dragItem is not None:
                relativeToCanvas = mouseEventArgs.GetPosition(self.VisualizationArea)
                self.dragItem.setPosition(Point(relativeToCanvas.X - self.dragStartPosition.X, relativeToCanvas.Y - self.dragStartPosition.Y))

    def DepotArea_PreviewMouseUp(self, sender, mouseEventArgs):
        self.dragStartPosition = None
        self.dragItem = None
    #endregion


    #region depot "backend" methods
    def getAreaByViewName(self, areaViewName):
        return next(area for area in self.areas.values() if hasattr(area, "view") and area.view.Name == areaViewName)

    def addAreaToFrontend(self, area):
        """Create DepotAreaView instance based on *area* and add it to the GUI.
        area: BaseArea subclass instance
        """
        maxEast = 0 #-globalConstants["depot"]["gui"]["distances_between_areas"]
        maxSouth = 0
        max_height = 0
        count_after_first_parking_area = 0 #needed for blocks
        first_parking_area_reached = False #needed for blocks
        if globalConstants["depot"]["gui"]["offset_line_break"][0]:
            offset_park_area_second_row = globalConstants["depot"]["gui"]["offset_line_break"][1]
        else:
            offset_park_area_second_row = 0
        System.Windows.Forms.Application.DoEvents() #neccessary refresh for GUI offset
        if area.ID in globalConstants["depot"]["gui"]["special_position_areas"]:
            area.view = DepotAreaView(area, self, globalConstants["depot"]["gui"]["special_position_areas"][area.ID]["x"],
                                      globalConstants["depot"]["gui"]["special_position_areas"][area.ID]["y"])

            self.VisualizationArea.Children.Add(area.view.Border)
            self.resizeCanvas()
        else:    
            for a in self.areas.values():
                if hasattr(a, "view") and a.view is not None:
                    east = a.view.position.X + a.view.ActualWidth
                    south = a.view.position.Y
                    max_height = max([max_height, a.view.ActualHeight])

                    if a.ID == globalConstants["depot"]["gui"]["first_parking_area"]:
                        max_height = a.view.ActualHeight
                        first_parking_area_reached = True
                        if not(globalConstants["depot"]["gui"]["offset_line_break"][0]):
                            offset_park_area_second_row = a.view.position.X
                    if first_parking_area_reached:
                        count_after_first_parking_area +=1
                        maxEast = east
                        maxSouth = south

                    if east > globalConstants["depot"]["gui"]["line_break"]:
                        maxEast = offset_park_area_second_row - globalConstants["depot"]["gui"][
                            "distances_between_areas"]
                        maxSouth = max_height + globalConstants["depot"]["gui"]["special_position_areas"][globalConstants["depot"]["gui"]["first_parking_area"]]["y"] + 20


                    # if maxEast < east < globalConstants["depot"]["gui"]["line_break"]:
                    #     maxEast = east
                    # else:
                    #     #maxEast = offset_park_area_second_row - globalConstants["depot"]["gui"]["distances_between_areas"]
                    #     if south == maxSouth:
                    #         maxSouth = max_height + a.view.position.Y + 30
                        # else:
                        #     maxSouth = south
            """The areas are displayed from left to right, until the linebreak. Then the  1st area in the 2nd row will be under the first "isssink" area of the 1st row."""

            #creates visual blocks
            if count_after_first_parking_area in globalConstants["depot"]["gui"]["blocks"]:
                if not(maxEast == offset_park_area_second_row - globalConstants["depot"]["gui"]["distances_between_areas"]):
                    maxEast += 100

            # create and attach wpf view model
            area.view = DepotAreaView(area, self, maxEast + globalConstants["depot"]["gui"]["distances_between_areas"], maxSouth) #..,.., x position, y position
            self.VisualizationArea.Children.Add(area.view.Border)
            self.resizeCanvas()

    def removeAreaFromFrontend(self, area):
        #remove view
        if hasattr(area, "view"):
            self.VisualizationArea.Children.Remove(area.view.Border)

    def resetDepot(self):
        self.configurator.reset()

        self.VisualizationArea.Children.Clear()
        self.Tag = self.configurator.templatename
        self.updateTitle()

        self.log("INFO", "Depot reset successful.")
    #endregion

    
    #region depot "frontend" methods
    @staticmethod
    def findAncestor(uiElement, targetType):
        """
        [.NET EXPLANATION]
        In XAML, just like in XML or HTML, the objects are aligned in a tree structure, hence any object has a parent/ancestor
        and child/descendant (except for the top/root and the bottom objects obviously).

        This method tries to find an objects ancestor of a certain type and returns the first one found.

        uiElement:  child/descendant object from which the search starts
        targetType: type of parent/ancestor you want to find
        """
        if hasattr(uiElement, "Parent") and uiElement.Parent is not None:
            if type(uiElement.Parent) == type(targetType) and hasattr(uiElement.Parent, "Name") and uiElement.Parent.Name != "":
                return uiElement.Parent
            else:
                return DepotView.findAncestor(uiElement.Parent, targetType)
        else:
            return None


    def resizeCanvas(self):
        """
        Experimental. Automatically resizes the depot canvas on which the depot areas are placed to fit all of the areas.
        """
        bottomright = Point(0, 0)

        areas = self.areas
        for areaId in areas:
            depotArea = areas[areaId]
            
            if hasattr(depotArea, "view"):
                areaX = depotArea.view.Border.Margin.Left + depotArea.view.Border.ActualWidth + depotArea.view.Border.Margin.Right # + depotArea.view.Border.RenderTransform.Value.OffsetX
                if areaX > bottomright.X:
                    bottomright.X = areaX
                areaY = depotArea.view.Border.Margin.Top + depotArea.view.Border.ActualHeight + depotArea.view.Border.Margin.Bottom  + depotArea.view.Border.RenderTransform.Value.OffsetY
                if areaY > bottomright.Y:
                    bottomright.Y = areaY

        #self.VisualizationArea.Width = bottomright.X
        self.VisualizationArea.Height = bottomright.Y

    def setPropertyFocus(self, viewObj):
        """
        Sets the current property focus, which is used to determine what information
        is to be displayed in the properties inspector.
        """
        if self.propertyFocus is not None:
            if isinstance(self.propertyFocus, DepotAreaView):
                self.propertyFocus.Border.ClearValue(Border.BackgroundProperty)
            elif isinstance(self.propertyFocus, VehicleSlotView):
                self.propertyFocus.childGrid.ClearValue(Grid.BackgroundProperty)

        self.propertyFocus = viewObj
        if self.propertyFocus is not None:
            if isinstance(self.propertyFocus, DepotAreaView):
                self.propertyFocus.Border.Background = Brushes.DarkOrange
            elif isinstance(self.propertyFocus, VehicleSlotView):
                self.propertyFocus.childGrid.Background = Brushes.DarkOrange

    def update(self):
        """
        Updates this object on the GUI.
        """
        if self.show_timetable and self.env.now > 0:
            list = ObservableCollection[ArrayList]()
            if self.depot.timetable is not None:
                for trip in self.depot.timetable.all_trips:
                    entry = ArrayList()
                    entry.Add(trip.ID)
                    entry.Add(trip.vehicle_types_joinedstr)
                    entry.Add(trip.std)
                    entry.Add(trip.atd if trip.atd is not None else "")
                    entry.Add("YES" if trip.atd is None and trip.std < self.env.now else "")
                    entry.Add(trip.vehicle.ID if trip.vehicle is not None else "")
                    list.Add(entry)
            self.LvTimeTable.ItemsSource = list

        if self.show_properties:
            self.LvDepotProperties.ItemsSource = None
            self.LvDepotProperties.ItemsSource = self.getProperties()

            self.LvItemProperties.ItemsSource = None
            if self.propertyFocus is not None:
                self.LvItemProperties.ItemsSource = self.propertyFocus.getProperties()

    def resetDetails(self):
        """
        (Re)sets the visibility of the Property Inspector and/or Timetable lists.

        [.NET EXPLANATION]
        A .NET Grid is like any other grid in another programming language. It has columns
        and rows and children arranged within these. The SetColumnSpan() method sets the range
        of columns in which the child is displayed, while the column set in SetColumn() is the
        starting column.
        """
        Grid.SetColumnSpan(self.GbxDepotProperties, 1)
        Grid.SetColumnSpan(self.GbxItemProperties, 1)
        Grid.SetColumnSpan(self.GbxTimeTable, 1)
        Grid.SetColumnSpan(self.GSDetailsH, 1)
        Grid.SetColumn(self.GbxTimeTable, 2)
        Grid.SetColumnSpan(self.BrdMain, 1)

        self.GSMain.Visibility = Visibility.Visible
        self.GbxDepotProperties.Visibility = Visibility.Visible
        self.GbxItemProperties.Visibility = Visibility.Visible
        self.GbxTimeTable.Visibility = Visibility.Visible
        self.GSDetailsH.Visibility = Visibility.Visible
        self.GSDetailsV.Visibility = Visibility.Visible

        if not self.show_properties:
            self.GbxDepotProperties.Visibility = Visibility.Collapsed
            self.GbxItemProperties.Visibility = Visibility.Collapsed
            self.GSDetailsH.Visibility = Visibility.Collapsed
            self.GSDetailsV.Visibility = Visibility.Collapsed

            if self.show_timetable:
                Grid.SetColumn(self.GbxTimeTable, 1)
                Grid.SetColumnSpan(self.GbxTimeTable, 3)

            self.LvDepotProperties.ItemsSource = None
            self.LvItemProperties.ItemsSource = None

        if not self.show_timetable:
            self.GbxTimeTable.Visibility = Visibility.Collapsed
            self.GSDetailsV.Visibility = Visibility.Collapsed

            if self.show_properties:
                Grid.SetColumnSpan(self.GbxDepotProperties, 3)
                Grid.SetColumnSpan(self.GbxItemProperties, 3)
                Grid.SetColumnSpan(self.GSDetailsH, 3)
            else:
                self.GSMain.Visibility = Visibility.Collapsed
                Grid.SetColumnSpan(self.BrdMain, 3)

            self.LvTimeTable.ItemsSource = None  
        elif not self.show_properties:
            Grid.SetColumn(self.GbxTimeTable, 0)

        self.update()

    def getProperties(self):
        """
        Collects the data displayed in the GUI properties inspector on the
        right hand side of the screen.

        [.NET EXPLANATION]
        Since python.net does not support complex types via CLR, we need to
        pass a list of lists of strings to display content inside a .NET
        [ListView] class. The outer list represents the rows, while the inner
        list represents the columns inside this row. While a .NET [ArrayList]
        is just the equivalent of a python [list], the
        .NET ObservableCollection additionally implements
        .NET NotifyPropertyChanged.
        """
        listProperties = ObservableCollection[ArrayList]()

        id = ArrayList()
        id.Add("ID")
        id.Add(self.depot.ID)
        listProperties.Add(id)

        templatename = ArrayList()
        templatename.Add('Template name')
        templatename.Add(self.configurator.templatename)
        listProperties.Add(templatename)

        capacity = ArrayList()
        capacity.Add("Capacity")
        capacity.Add(self.depot.capacity)
        listProperties.Add(capacity)

        parking_capacity = ArrayList()
        parking_capacity.Add("Parking capacity")
        parking_capacity.Add(self.depot.parking_capacity)
        listProperties.Add(parking_capacity)

        vacant = ArrayList()
        vacant.Add("Vacant slots")
        vacant.Add(self.depot.vacant)
        listProperties.Add(vacant)

        vacant_accessible = ArrayList()
        vacant_accessible.Add("Vacant accessible slots")
        vacant_accessible.Add(self.depot.vacant_accessible)
        listProperties.Add(vacant_accessible)

        count = ArrayList()
        count.Add('Vehicles (now)')
        count.Add(self.depot.count)
        listProperties.Add(count)

        max_count = ArrayList()
        max_count.Add('Vehicles (max)')
        max_count.Add(self.depot.max_count)
        listProperties.Add(max_count)

        slotcount_used = ArrayList()
        slotcount_used.Add("Used slots until now")
        slotcount_used.Add(self.depot.maxOccupiedSlots)
        listProperties.Add(slotcount_used)

        checkins = ArrayList()
        checkins.Add("Check-ins")
        checkins.Add(self.depot.checkins)
        listProperties.Add(checkins)

        checkouts = ArrayList()
        checkouts.Add("Check-outs")
        checkouts.Add(self.depot.checkouts)
        listProperties.Add(checkouts)

        overdue_trips = ArrayList()
        overdue_trips.Add("Overdue trips")
        overdue_trips.Add(len(self.depot.overdue_trips))
        listProperties.Add(overdue_trips)

        if self.env.now != 0 and self.simulation_host.gc['depot']['log_sl'] and self.depot.evaluation.sl_logs:
            # Get the most sl recent log
            log = self.depot.evaluation.sl_logs[max(self.depot.evaluation.sl_logs.keys())]
            sl_values = self.depot.evaluation.calculate_sl_single(log)
            for category in self.simulation_host.gc['depot']['vehicle_type_categories']:
                sl = ArrayList()
                sl.Add("Stress level for %s:" % category)
                sl.Add(sl_values[category])
                listProperties.Add(sl)

        total_power = ArrayList()
        total_power.Add("Total current power [kW]")
        total_power.Add(int(self.depot.total_power))
        listProperties.Add(total_power)

        return listProperties

    def exit(self):
        System.Diagnostics.Process.GetCurrentProcess().Kill()

    def updateTitle(self):
        self.Window.Title = self.configurator.templatename + " - eFLIPS Depot GUI"

    def updateConfigDisplay(self):
        for area in self.areas.values():
            if hasattr(area, 'view'):
                area.view.update()
    #endregion

    #region simulation methods
    def init_simulation(self):
        """Setup a SimulationHost without loading a template and without
        completing it yet and create simulation-related shortcut attributes.
        """
        self.simulation_host = SimulationHost(
            [Depotinput(None, show_gui=True)], run_progressbar=False)
        self.simulation_host.load_eflips_settings(inputs['filename_eflips_settings'])
        self.simulation_host.load_timetable(inputs['filename_timetable'])

        self.env = self.simulation_host.env
        self.timetable = self.simulation_host.timetable
        self.depot = self.simulation_host.depot_hosts[0].depot
        self.areas = self.depot.areas
        self.configurator = self.simulation_host.depot_hosts[0].configurator
        self.evaluation = self.simulation_host.depot_hosts[0].evaluation
        self.depot.view = self

        # Now the configuration phase starts where the depot and other settings
        # can be modified before calling start_simulation()

    def start_simulation(self):
        """Complete the configuration phase and run the simulation."""
        success, errormsg = self.configurator.complete()
        if not success:
            MessageBox.Show(errormsg,
                            "Depot GUI",
                            MessageBoxButton.OK,
                            MessageBoxImage.Warning)

        elif not self.isSimulationRunning:
            # Final preparations
            self.simulation_host.complete()
            self.simulation_host.gc['depot']['log_cm_data'] = True
            if not os.path.exists("eflips"):
                # Application root is 'bvg_depot' (PyCharm)
                self.path_results = self.simulation_host.gc['depot']['path_results']

            # Run
            # Set buttons disabled
            self.MbnDepot.IsEnabled = False
            self.MbnStartSimulation.IsEnabled = False

            self.log("INFO", "Starting simulation...")
            self.BtnStart.Content = "Pause"
            self.isSimulationRunning = True
            self.isSimulationPaused = False

            try:
                self.simulation_host.run()
                self.log("INFO", "Simulation finished successfully.")
            except:
                traceback.print_exc()
                self.log("ERROR", "Simulation caused an error!")

            if self.simulation_host.gc['depot']["gui"]['show_occupancy_rate']:
                self.show_occupancy_rate()


            self.PnlSimEnd.Visibility = Visibility.Visible
            self.isSimulationRunning = False
            self.isSimulationPaused = False

            self.MbnAnalysis.IsEnabled = True
            self.BtnStart.Visibility = Visibility.Collapsed
            self.BtnReset.Visibility = Visibility.Collapsed
            self.GbxDeceleration.Visibility = Visibility.Collapsed

    def show_occupancy_rate(self):
        """
        Shows the occupancy ratio after the simulation.
        Creates for this new dummy_vehicle types and dummy_vehicle_items. The occupancy ratio is the "soc" of the vehicles.
        """
        interval = tuple(self.simulation_host.gc['depot']["gui"]['occupancy_rate_interval'])
        self.evaluation.xlim = interval
        slots_and_areas = self.evaluation.occupancy_rate_calculation()
        dummy_vehicles = {}

        for area_key, series in slots_and_areas.items():
            area = self.areas[area_key]
            for slot_key, abs_occ in series.items():
                per_occ = abs_occ / (interval[1] - interval[0])
                if hasattr(area, "view"):
                    if per_occ < 0.5:
                        area.view.slotViews[slot_key - 1].Rectangle.Fill = SolidColorBrush(Color.FromArgb(200, 255, round(per_occ * 2 * 255), 0))
                    else:
                        area.view.slotViews[slot_key - 1].Rectangle.Fill = SolidColorBrush(Color.FromArgb(200, round((1 - per_occ) * 2 * 255), 255, 0))

                    area.view.slotViews[slot_key - 1].shadow.Opacity = 0.0
                    area.view.slotViews[slot_key - 1].LblId.Content = str(round(per_occ*100,2)) + " %"
                    area.view.slotViews[slot_key - 1].LblId.FontSize = 8
                    # if per_occ == 0:
                    #     area.view.slotViews[slot_key - 1].Rectangle.Fill = SolidColorBrush(
                    #         Color.FromArgb(255, round((1 - per_occ) * 255), 0, 0))

    def resetSimulation(self):
        """
        Experimental. So far no way has been found to reset the simulation without restarting the application.
        """
        import simpy
        raise simpy.core.StopSimulation

        self.isSimulationPaused = False
        self.BtnStart.Content = "Start"
        self.log("INFO", "Simulation has been stopped and reset manually.")
    #endregion

    #region template methods
    def loadTemplate(self, filename):
        """
        Loads a depot template from a json file.
        
        filename: [str] including path, without extension
        """
        self.VisualizationArea.Children.Clear()

        success, errormsg = self.configurator.load(filename)

        if success:
            for area in self.areas.values():
                if area.ID in globalConstants["depot"]["gui"]["special_orientation_slots"]:
                    area.slot_orientation = globalConstants["depot"]["gui"]["special_orientation_slots"][area.ID]
                self.addAreaToFrontend(area)
            
            self.Tag = self.configurator.templatename
            self.updateTitle()

            self.log("INFO", "Depot template loaded successfully.")
            return True

        else:
            traceback.print_exc()
            MessageBox.Show("Error while loading template! " + errormsg,
                            "Depot GUI - Load Template", MessageBoxButton.OK,
                            MessageBoxImage.Error)
            return False
    #endregion

    def log(self, logLevel, event):
        """
        GUI logging method (log is placed on the bottom of the GUI screen).
        Independent from print() or other logging methods in use, but may get
        called from them if you want to.
        """
        newEntry = ArrayList()
        newEntry.Add(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        newEntry.Add(str(self.env.now))
        newEntry.Add(seconds2date(self.env.now + globalConstants["general"]["START_DAY"]))
        newEntry.Add(logLevel)
        newEntry.Add(event)
        self.listLog.Add(newEntry)
        self.LvLog.ScrollIntoView(self.LvLog.Items[self.LvLog.Items.Count - 1])

        for area in self.depot.areas.values():
            if hasattr(area, "view"):
                for slotView in area.view.slotViews.values():
                    if slotView.vehicle is not None:
                        slotView.update()

        # simulation control workaround
        while True:
            System.Windows.Forms.Application.DoEvents()
            if not self.isSimulationPaused:
                break
        System.Threading.Thread.Sleep(self.simulationDeceleration)


def start(filename_eflips_settings, filename_timetable):
    """
    [.NET EXPLANATION]
    Long story short:

    Since pyhton normally starts an MTA thread, but WPF or Windows Forms need an STA thread like any other COM assembly,
    we need to start the guy in a new STA thread manually. The old MTA thread will end automatically.
    """
    inputs['filename_eflips_settings'] = filename_eflips_settings
    inputs['filename_timetable'] = filename_timetable

    if main_view is None:
        print('Starting depot GUI thread...')

        guiThread = Thread(ThreadStart(DepotView()))
        guiThread.SetApartmentState(ApartmentState.STA)
        guiThread.Start()
        guiThread.Join()


# if __name__ == "__main__":
#     start()
