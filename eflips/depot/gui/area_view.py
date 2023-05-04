"""
@author: B.Bober, e.lauth

Additional GUI code for depot areas and their vehicle slots.
"""
from random import randint

import eflips.depot.gui.depot_view
from eflips.depot.gui.depot_view import *
from eflips import globalConstants


class DepotAreaView(Grid):
    """
    This class visually represents a depot area of any type, inherits from .NET [Grid] class.

    For additional explanation about the .NET code see [DepotView] class.

    Attributes:
        slot_orientation: [str] orientation of slots. Used for display purposes
        in the GUI only, has no functional purpose. Actually an attribute of
        *area*, but may be moved to this class in the future. Possible values:
        'HORIZONTAL', 'VERTICAL', 'ANGLE_LEFT', 'ANGLE_RIGHT'.
    """
    def __init__(self, area, parent, hOffset = 0, vOffset = 0):
        self.area = area
        self.parent = parent

        self.orientation = 0
        if self.area.ID in globalConstants["depot"]["gui"]["special_orientation_areas"]:
            if globalConstants["depot"]["gui"]["special_orientation_areas"][area.ID] == "HORIZONTAL":
                self.orientation = 6

        if not hasattr(self.parent, "daId"):
            self.parent.daId = 0
        self.parent.daId = self.parent.daId + 1
        self.Name = "depotarea_" + str(self.parent.daId)

        self.Tag = self.area.ID
        self.position = Point(hOffset, vOffset)        

        parkingLotVisuals = True

        self.Border = Border()
        if parkingLotVisuals:
            self.Border.Style = parent.Window.TryFindResource("ParkingLotBorder")
        else:
            self.Border.Style = parent.Window.TryFindResource("DepotAreaBorder")
        self.Border.Child = self

        # title row: 0
        topRow = RowDefinition()
        self.RowDefinitions.Add(topRow)

        self.LblTitle = Label()
        self.LblTitle.Style = parent.Window.TryFindResource("TitleLabel")
        self.Children.Add(self.LblTitle)
        Grid.SetRow(self.LblTitle, 0)

        # info row: 1
        middleRow = RowDefinition()
        self.RowDefinitions.Add(middleRow)

        if not parkingLotVisuals:
            self.area.slot_orientation = "HORIZONTAL"

            # vehicle slot row: 2
            bottomRow = RowDefinition()
            self.RowDefinitions.Add(bottomRow)

            slotGrid = Grid()
            self.Children.Add(slotGrid)
            Grid.SetRow(slotGrid, 2)

        self.LblInfo = Label()
        self.LblInfo.Style = parent.Window.TryFindResource("DescriptionLabel")
        self.Children.Add(self.LblInfo)
        Grid.SetRow(self.LblInfo, 1)

        # children (VehicleSlotView)
        self.slotViews = {}
        for slot in range(area.capacity):

            slot_height = globalConstants["depot"]["gui"]["slot_length"]["default"]
            if hasattr(area.entry_filter, 'vehicle_types'):
                types = area.entry_filter.vehicle_types
                for type in types:
                    if type in globalConstants["depot"]["gui"]["slot_length"]:
                        # slot_height=max([slot_height,globalConstants["depot"]["gui"]["slot_length"][type]]) #looks for an larger slot heigth then the default one
                        slot_height = globalConstants["depot"]["gui"]["slot_length"][type]
            slotView = VehicleSlotView(self, slot, slot_height, area)
            self.slotViews[slot] = slotView

            if parkingLotVisuals:
                rowDefinition = RowDefinition()
                self.RowDefinitions.Add(rowDefinition)

                self.Children.Add(slotView)
                Grid.SetRow(slotView, self.RowDefinitions.Count - 1)
            else:
                colDefinition = ColumnDefinition()
                slotGrid.ColumnDefinitions.Add(colDefinition)

                slotGrid.Children.Add(slotView)
                Grid.SetColumn(slotView, slotGrid.ColumnDefinitions.Count - 1)

        self.Loaded += self.DepotAreaView_Loaded

        self.MbnRotate = MenuItem()
        self.MbnRotate.Header = "Rotate"
        self.MbnRotate.Click += self.MbnRotate_Click

        self.MbnRemove = MenuItem()
        self.MbnRemove.Header = "Remove"
        self.MbnRemove.Click += self.MbnRemove_Click

        contextMenu = ContextMenu()
        contextMenu.Items.Add(self.MbnRotate)
        contextMenu.Items.Add(self.MbnRemove)
        contextMenu.IsVisibleChanged += self.ContextMenu_IsVisibleChanged
        self.Border.ContextMenu = contextMenu

        self.PreviewMouseLeftButtonDown += self.DepotAreaView_MouseLeftButtonDown # event workaround since not all framworkelements have a click event

    #region control eventhandlers
    def ContextMenu_IsVisibleChanged(self, sender, eventArgs):
        self.MbnRotate.IsEnabled = not self.parent.isSimulationRunning
        self.MbnRemove.IsEnabled = not self.parent.isSimulationRunning

    def DepotAreaView_Loaded(self, sender, eventArgs):
        self.applyRenderTransform()
        self.update()

    def MbnRotate_Click(self, sender, eventArgs):
        self.rotate()

    def MbnRemove_Click(self, sender, eventArgs):
        self.parent.removeAreaFromFrontend(self.area)
        self.parent.configurator.remove_area(self.area)

    def DepotAreaView_MouseLeftButtonDown(self, sender, eventArgs):
        pass
    #endregion
    
    #region visual methods
    def setPosition(self, target):
        """
        Sets the depot area visually on the *target* position.
        Calls applyRenderTransform().
        """
        self.position = target
        self.applyRenderTransform()        

    def rotate(self):
        self.orientation = self.orientation + 1
        self.applyRenderTransform()

    def applyRenderTransform(self):
        """
        [.NET EXPLANATION]
        Any .NET FrameworkElement can be moved (translateTransform) or rotated (rotateTransform)
        relative to its parent. Both can also be combined. This method calls a specific combination
        of both for this DepotAreaView in order to move and/or rotate it visually on the GUI.
        """
        rotateTransform = RotateTransform(float(45 * self.orientation))
        rotateTransform.CenterX = self.Border.ActualWidth / 2
        rotateTransform.CenterY = self.Border.ActualHeight / 2
        translateTransform = TranslateTransform(self.position.X, self.position.Y)

        transformGroup = TransformGroup()
        transformGroup.Children.Add(rotateTransform)
        transformGroup.Children.Add(translateTransform)
        self.Border.RenderTransform = transformGroup

        # rotate labels
        for key in self.slotViews:
            self.slotViews[key].applyRenderTransform()

    def getProperties(self):
        """
        Collects the data displayed in the GUI properties inspector on the right hand side of the screen.
        For more information, see DepotView class.
        """
        listProperties = ObservableCollection[ArrayList]()

        header = ArrayList()
        header.Add(" ----- ----- ")
        header.Add("DEPOT AREA")
        listProperties.Add(header)

        id = ArrayList()
        id.Add("ID")
        id.Add(self.area.ID)
        listProperties.Add(id)

        if hasattr(self.area, "shortName"):
            shortName = ArrayList()
            shortName.Add("Short name")
            shortName.Add(self.area.shortName)
            listProperties.Add(shortName)

        parking_area_group = ArrayList()
        parking_area_group.Add("Parking area group")
        parking_area_group.Add(self.area.parking_area_group.ID + " (Strategy: " + self.area.parking_area_group.parking_strategy_name + ")" if self.area.parking_area_group is not None else "None")
        listProperties.Add(parking_area_group)

        capacity = ArrayList()
        capacity.Add("Capacity")
        capacity.Add(self.area.capacity)
        listProperties.Add(capacity)

        vacant_accessible = ArrayList()
        vacant_accessible.Add("# Vacant accessible slots")
        vacant_accessible.Add(self.area.vacant_accessible)
        listProperties.Add(vacant_accessible)

        count = ArrayList()
        count.Add("# Vehicles (now)")
        count.Add(self.area.count)
        listProperties.Add(count)

        max_count = ArrayList()
        max_count.Add("# Vehicles (max)")
        max_count.Add(self.area.max_count)
        listProperties.Add(max_count)

        slotcount_used = ArrayList()
        slotcount_used.Add("# Used slots until now")
        slotcount_used.Add(self.area.maxOccupiedSlots)
        listProperties.Add(slotcount_used)
        
        return listProperties

    def update(self):
        """
        Updates this object on the GUI.
        """
        # self.LblTitle.Content = "[{0}] {1}".format(self.area.areaNo, self.area.shortName if hasattr(self.area, "shortName") else "")
        self.LblTitle.Content = self.area.ID    # temporary
        #self.LblInfo.Content = "[" + ", ".join(str(x) for x in area.available_processes) + "]"

        if hasattr(self.area, "parking_area_group"):
            parking_area_group = self.area.parking_area_group
            if parking_area_group is not None:
                if not hasattr(parking_area_group, "color"):
                    if len(self.parent.groupColors) > self.parent.nextGroupColor:
                        parking_area_group.color = self.parent.groupColors[self.parent.nextGroupColor]
                        self.parent.nextGroupColor = self.parent.nextGroupColor + 1
                    else:
                        parking_area_group.color = [randint(0, 255), randint(0, 255), randint(0, 255)]

                self.Border.Background = SolidColorBrush(Color.FromArgb(255, parking_area_group.color[0], parking_area_group.color[1], parking_area_group.color[2]))
                return

        self.Border.ClearValue(Border.BackgroundProperty)
    #endregion

    def parkVehicle(self, put_event):
        """
        Parks a vehicle on this DepotAreaViews depot area and the
        VehicleSlotView.
        """
        self.slotViews[self.area.items.index(put_event.item)].parkVehicle(put_event)

    def unparkVehicle(self, get_event):
        """
        Unarks a vehicle from this DepotAreaViews depot area.
        Vehicle will also get removed from its VehicleSlotView.
        """
        for slotView in self.slotViews.values():
            if slotView.vehicle is get_event.value:
                slotView.unparkVehicle(get_event)
                break


class VehicleSlotView(Viewbox):
    """
    This class visually represents a single vehicle slot inside a depot area of any type, inherits from .NET [ViewBox] class.

    For additional explanation about the .NET code see [DepotView] class.
    """
    def __init__(self, parent, id, slot_height, area):
        self.parent = parent
        self.area = area
        self.id = id
        self.Name = parent.Name + "_slot_" + str(id)
        self.vehicle = None
        self.hasBeenUsed = False

        self.LblId = Label()
        self.LblId.FontSize = 8
        self.LblId.Foreground = Brushes.Black
        self.LblId.VerticalContentAlignment = VerticalAlignment.Center

        self.Rectangle = Rectangle()
        if parent.area.slot_orientation.upper() == "HORIZONTAL":
            self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Horizontal")
        elif parent.area.slot_orientation.upper() == "ANGLE_LEFT":
            if self.area.capacity-1 == self.id:
                if slot_height <= 120:
                    self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Angle_Left_Last_12m")
                else:
                    self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Angle_Left_Last_18m")
            else:
                if slot_height <= 120:
                    self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Angle_Left_12m")
                else:
                    self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Angle_Left_18m")
        elif parent.area.slot_orientation.upper() == "ANGLE_RIGHT":
            if self.area.capacity-1 == self.id:
                if slot_height <= 120:
                    self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Angle_Right_Last_12m")
                else:
                    self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Angle_Right_Last_18m")
            else:
                if slot_height <= 120:
                    self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Angle_Right_12m")
                else:
                    self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Angle_Right_18m")

        elif parent.area.slot_orientation.upper() == "VERTICAL":
            self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Vertical")
        else:
            self.Rectangle.Style = parent.parent.Window.TryFindResource("VehicleSlot_Vertical")

        self.Rectangle.Width = slot_height #sets the slot height



        self.childGrid = Grid()
        gridStyle = Style()
        trigger = Trigger()
        trigger.Property = Grid.IsMouseOverProperty
        trigger.Value = True
        setter = Setter()
        setter.Property = Grid.BackgroundProperty
        setter.Value = Brushes.Gray
        trigger.Setters.Add(setter)
        gridStyle.Triggers.Add(trigger)
        self.childGrid.Style = gridStyle

        self.childGrid.Children.Add(self.Rectangle)
        self.childGrid.Children.Add(self.LblId)
        self.Child = self.childGrid

        self.shadow = DropShadowEffect()
        self.shadow.Direction = 0.0
        self.shadow.ShadowDepth = 5.0
        self.shadow.Opacity = 0.0
        self.Effect = self.shadow

        #self.Loaded += self.VehicleSlotView_Loaded
        self.PreviewMouseLeftButtonDown += self.VehicleSlotView_PreviewMouseLeftButtonDown # event workaround since not all framworkelements have a click event

    #region control eventhandlers
    def VehicleSlotView_Loaded(self, sender, eventArgs):
        self.applyRenderTransform()
        self.update()

    def VehicleSlotView_PreviewMouseLeftButtonDown(self, sender, eventArgs):
        self.parent.parent.setPropertyFocus(self)
        self.parent.parent.update()
    #endregion

    def parkVehicle(self, put_event):
        """
        Parks the given vehicle on this slot.
        """
        self.vehicle = put_event.item

        self.vehicle.view = self

        self.parent.parent.log("INFO", self.parent.area.ID + ": Parked vehicle " + self.vehicle.ID)

        self.parent.parent.update()

    def unparkVehicle(self, get_event):
        """
        Removes the current vehicle from this vehicle slot.
        """
        #self.parent.parent.log("INFO", self.parent.area.ID + ": Unparked vehicle on slot " + str(self.id))
        if self.parent.area.issink:
            self.parent.parent.log("INFO", self.parent.area.ID + ": Vehicle %s departed" % self.vehicle.ID)

        self.vehicle.view = None
        self.vehicle = None
        self.parent.parent.update()
        self.update()

    def update(self):
        """
        Updates the vehicle slot visually on the GUI.
        """

        if self.vehicle is not None:
            # self.LblId.Content = self.vehicle.vehicleType + ' ' + self.vehicle.ID

            self.LblId.Content = self.vehicle.ID
            self.LblId.FontSize = 8

            batteryPercent = self.vehicle.battery.soc
            try:
                if batteryPercent < 0.5:
                    self.Rectangle.Fill = SolidColorBrush(Color.FromArgb(255, 255, round(batteryPercent * 2 * 255), 0))
                else:
                    self.Rectangle.Fill = SolidColorBrush(Color.FromArgb(255, round((1 - batteryPercent) * 2 * 255), 255, 0))
            except:
                # print('SolidColorBrush Error in depotAreaView.VehicleSlotView.update()')
                pass

            # if batteryPercent == self.vehicle.battery.soc_max:
            #     self.LblId.Foreground = Brushes.Black
            # else:
            #     self.LblId.Foreground = Brushes.Beige

            if self.parent.area.issink:
                self.shadow.Opacity = 1.0
                if self.vehicle.trip is not None:
                    self.shadow.Color = Colors.LightGreen
                else:
                    self.shadow.Color = Colors.Tomato

            self.hasBeenUsed = True
        else:
            self.shadow.Opacity = 0.0
            self.LblId.Content = ""
            if self.hasBeenUsed:
                self.Rectangle.Fill = Brushes.LightBlue
            else:
                self.Rectangle.Fill = None

        #workaround for misplaced labels
        if self.parent.area.slot_orientation != "HORIZONTAL":
            self.applyRenderTransform()

    def applyRenderTransform(self):
        """
        Applies the .NET RenderTransform() method to itself, which moves and/or rotates an object on the screen.
        """

        if self.parent.area.slot_orientation == "ANGLE_RIGHT":
            backrotation = -1
        elif self.parent.area.slot_orientation == "VERTICAL":
            backrotation = 2
        elif self.parent.area.slot_orientation == "ANGLE_LEFT":
            backrotation = 1
        else:
            backrotation = 0

        rotateTransform_Label = RotateTransform(float(45 * backrotation))
        rotateTransform_Label.CenterX = self.LblId.ActualWidth / 2
        # if self.area.capacity - 1 == self.id:
        #     rotateTransform_Label.CenterY = self.LblId.ActualHeight / 2
        # else:
        rotateTransform_Label.CenterY = self.LblId.ActualHeight / 2
        self.LblId.RenderTransform = rotateTransform_Label

    def getProperties(self):
        """
        Collects the data displayed in the GUI properties inspector on the right hand side of the screen.
        For more information, see [DepotView] class.
        """
        listProperties = ObservableCollection[ArrayList]()

        #parent listProperties (depot area)
        for property in self.parent.getProperties():
            listProperties.Add(property)

        slotHeader = ArrayList()
        slotHeader.Add(" ----- ----- ")
        slotHeader.Add("VEHICLE SLOT")
        listProperties.Add(slotHeader)

        id = ArrayList()
        id.Add("ID")
        id.Add(self.id)
        listProperties.Add(id)

        if self.parent.area.charging_interfaces is not None:
            max_power = ArrayList()
            max_power.Add("kW Max power")
            max_power.Add(str(self.parent.area.charging_interfaces[self.id].max_power))
            listProperties.Add(max_power)

        used = ArrayList()
        used.Add("Has been used?")
        used.Add(str(self.hasBeenUsed))
        listProperties.Add(used)

        if self.vehicle is not None:
            busHeader = ArrayList()
            busHeader.Add(" ----- ----- ")
            busHeader.Add("VEHICLE")
            listProperties.Add(busHeader)

            vehicleId = ArrayList()
            vehicleId.Add("ID")
            vehicleId.Add(self.vehicle.ID)
            listProperties.Add(vehicleId)

            vehicle_type = ArrayList()
            vehicle_type.Add("Type")
            vehicle_type.Add(self.vehicle.vehicle_type.ID)
            listProperties.Add(vehicle_type)

            soc = ArrayList()
            soc.Add("% Battery SoC")
            soc.Add(self.vehicle.battery.soc)
            listProperties.Add(soc)

            energy = ArrayList()
            energy.Add("kWh Battery current energy")
            energy.Add(self.vehicle.battery.energy)
            listProperties.Add(energy)

            energy_real = ArrayList()
            energy_real.Add("kWh Battery real energy capacity")
            energy_real.Add(self.vehicle.battery.energy_real)
            listProperties.Add(energy_real)

            energy_nominal = ArrayList()
            energy_nominal.Add("kWh Battery nominal energy capacity")
            energy_nominal.Add(self.vehicle.battery.energy_nominal)
            listProperties.Add(energy_nominal)

            soh = ArrayList()
            soh.Add("% Battery SoH")
            soh.Add(self.vehicle.battery.soh)
            listProperties.Add(soh)

            if self.vehicle.vehicle_type.CR is not None:
                cr = ArrayList()
                if self.parent.parent.simulation_host.gc['depot']['consumption_calc_mode'] == 'CR_distance_based':
                    cr.Add("kWh/km Consumption rate")
                else:
                    cr.Add("kW Consumption rate")
                cr.Add(self.vehicle.vehicle_type.CR)
                listProperties.Add(cr)

            scheduledTrip = ArrayList()
            scheduledTrip.Add("Scheduled trip")
            if self.vehicle.trip is not None:
                scheduledTrip.Add(self.vehicle.trip.ID + " (std = " + str(self.vehicle.trip.std) + ")")
            else: 
                scheduledTrip.Add("None")
            listProperties.Add(scheduledTrip)

            if hasattr(self.vehicle, "finished_trips"):
                finished_trips = ArrayList()
                finished_trips.Add("# Finished trips")
                finished_trips.Add(len(self.vehicle.finished_trips))
                listProperties.Add(finished_trips)

            if hasattr(self.vehicle, "mileage"):
                mileage = ArrayList()
                mileage.Add("km Mileage")
                mileage.Add(self.vehicle.mileage)
                listProperties.Add(mileage)

        return listProperties