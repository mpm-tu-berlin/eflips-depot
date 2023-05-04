"""
@author: B.Bober

Popup Window for depot configuration settings.

+++ All functionalities except loading templates are deactivated until a GUI
settings update. The reason is that features introduced here such as saving and
configurating templates were replaced by depot.configuration. The GUI settings
update will incorporate the new methods. +++

For more explanation about the .NET code see DepotView class.
"""
from os.path import isfile, join
import math

import eflips.depot.gui.depot_view
from eflips.depot.gui.depot_view import *


class SettingsWindow(Window):
    def __init__(self, parent):
        self.parent = parent

        stream = StreamReader(parent.path_root + "eflips\\depot\\gui\\settings_view.xaml")
        self.Window = XamlReader.Load(stream.BaseStream)
        self.Owner = parent.Window
        
        self.dragStartPosition = None
        self.areaCounter = 0

        self.configurator = self.parent.configurator
        self.depot = self.configurator.depot
        self.areas = self.depot.areas
        self.selectedGroup = None

        self.LbTemplates = self.Window.FindName("LbTemplates")
        self.LbTemplates.SelectionChanged += self.LbTemplates_SelectionChanged
        self.LbTemplates.MouseDoubleClick += self.LbTemplates_MouseDoubleClick

        self.TxtTemplateName = self.Window.FindName("TxtTemplateName")
        self.TxtTemplateName.Text = self.configurator.templatename
        
        self.GrbNewGroup = self.Window.FindName("GrbNewGroup")
        self.TxtGroupName = self.Window.FindName("TxtGroupName")
        #self.TxtGroupName.KeyDown += self.TxtGroupName_KeyDown

        self.PnlRadioButtons = self.Window.FindName("PnlRadioButtons")
        availableGroupStrategies = eflips.depot.depot.ParkingAreaGroup.parking_strategies
        self.listRadioButtons = []
        for name in availableGroupStrategies:
            radioButton = RadioButton()
            radioButton.Name = "Rbn" + name
            radioButton.Content = availableGroupStrategies[name].short_description
            radioButton.Tag = name
            radioButton.Margin = Thickness(6.0)
            radioButton.GroupName = "1"
            self.listRadioButtons.append(radioButton)

            infoSign = Label()
            infoSign.Padding = Thickness(-8.0)
            infoSign.Content = "?"
            infoSign.Width = 15.0
            infoSign.Height = 15.0
            infoSign.HorizontalContentAlignment = HorizontalAlignment.Center
            infoSign.VerticalContentAlignment = VerticalAlignment.Center

            toolTip = ToolTip()
            infoSign.ToolTip = toolTip
            toolTip.Content = availableGroupStrategies[name].tooltip

            infoSignBorder = Border()
            infoSignBorder.Child = infoSign
            infoSignBorder.BorderBrush = Brushes.Gray
            infoSignBorder.BorderThickness = Thickness(0.5)
            infoSignBorder.HorizontalAlignment = HorizontalAlignment.Right 
            infoSignBorder.VerticalAlignment = VerticalAlignment.Center
            infoSignBorder.CornerRadius = CornerRadius(7.5)

            dockPanel = DockPanel()
            dockPanel.Children.Add(radioButton)
            dockPanel.Children.Add(infoSignBorder)

            self.PnlRadioButtons.Children.Add(dockPanel)

        self.BtnSaveGroup = self.Window.FindName("BtnSaveGroup")
        self.BtnSaveGroup.Click += self.BtnSaveGroup_Click

        self.TvDepotAreas = self.Window.FindName("TvDepotAreas")
        self.TvDepotAreas.MouseMove += self.TvDepotAreas_MouseMove
        self.TvDepotAreas.DragEnter += self.TvDepotAreas_DragEnter
        self.TvDepotAreas.PreviewMouseLeftButtonDown += self.TvDepotAreas_PreviewMouseLeftButtonDown
        self.TvDepotAreas.SelectedItemChanged += self.TvDepotAreas_SelectedItemChanged
        self.TvDepotAreas.Drop += self.TvDepotAreas_Drop

        self.BtnLoad = self.Window.FindName("BtnLoad")
        self.BtnLoad.Click += self.BtnLoad_Click

        self.BtnBrowseForTemplate = self.Window.FindName("BtnBrowseForTemplate")
        self.BtnBrowseForTemplate.Click += self.BtnBrowseForTemplate_Click

        self.BtnSave = self.Window.FindName("BtnSave")
        self.BtnSave.Click += self.BtnSave_Click

        self.BtnRemoveTemplate = self.Window.FindName("BtnRemoveTemplate")
        self.BtnRemoveTemplate.Click += self.BtnRemoveTemplate_Click

        self.BtnRemoveArea = self.Window.FindName("BtnRemoveArea")
        self.BtnRemoveArea.Click += self.BtnRemoveArea_Click

        self.BtnOk = self.Window.FindName("BtnOk")
        self.BtnOk.Click += self.BtnOk_Click

        # Block unused until settings GUI update; deactivated because of areaNo
        # incompatibility:

        # areasByNo = self.parent.getAreasByNo()
        # areaNumbers = [x for x in areasByNo.keys()]
        # areaNumbers.sort()
        # for areaNo in areaNumbers:
        #     depotArea = areasByNo[areaNo]
        #
        #     tvItem = TreeViewItem()
        #     tvItem.AllowDrop = True
        #     tvItem.Drop += self.TvDepotAreas_Drop
        #     tvItem.Tag = depotArea.ID
        #
        #     if type(depotArea) is eflips.depot.AreaGroup:
        #         tvItem.FontWeight = FontWeights.Bold
        #         if self.getTreeViewItem(depotArea.ID) == None:
        #             self.TvDepotAreas.Items.Add(tvItem)
        #     else:
        #         tvItem.FontWeight = FontWeights.Normal
        #
        #         if depotArea.assignmentGroup is not None:
        #             assignmentGroup = depotArea.assignmentGroup
        #             self.areas[assignmentGroup.ID] = assignmentGroup
        #
        #             grItem = self.getTreeViewItem(assignmentGroup.ID)
        #             if grItem == None:
        #                 grItem = TreeViewItem()
        #                 grItem.AllowDrop = True
        #                 grItem.Drop += self.TvDepotAreas_Drop
        #                 grItem.Tag = assignmentGroup.ID
        #                 grItem.FontWeight = FontWeights.Bold
        #
        #                 self.TvDepotAreas.Items.Add(grItem)
        #             grItem.Items.Add(tvItem)
        #         else:
        #             self.TvDepotAreas.Items.Add(tvItem)
        #
        # self.sortTreeView(self.TvDepotAreas)

        self.listTemplates()

    #region control eventhandlers
    def BtnLoad_Click(self, sender, eventArgs):
        self.loadTemplate(self.parent.path_templates
                          + self.LbTemplates.SelectedItem[:-len('.json')])

    def BtnBrowseForTemplate_Click(self, sender, eventArgs):
        dialog = OpenFileDialog()
        dialog.Filter = "JSON Files (*.json)|*.json"
        if dialog.ShowDialog():
            selected_file = dialog.FileName
            print(selected_file)
            self.loadTemplate(selected_file[:-len('.json')])

    def BtnSave_Click(self, sender, eventArgs):
        """Unused until settings GUI update"""
        from eflips.depot.gui.inputbox import InputBox
        inputBox = InputBox(self, "Save Template", "Please enter a name for your template.", self.TxtTemplateName.Text)
        if inputBox.show():
            self.saveTemplate(inputBox.returnValue + ".json")
        inputBox = None        

    def BtnRemoveTemplate_Click(self, sender, eventArgs):
        if self.LbTemplates.SelectedItem is not None:
            self.removeTemplate(self.LbTemplates.SelectedItem)

    def BtnRemoveArea_Click(self, sender, eventArgs):
        """Unused until settings GUI update"""
        if self.TvDepotAreas.SelectedItem is not None:
            self.TvDepotAreas.SelectedItem.Parent.Items.Remove(self.TvDepotAreas.SelectedItem)

    def BtnSaveGroup_Click(self, sender, eventArgs):
        """Unused until settings GUI update"""
        groupStrategy = None
        for radioButton in self.listRadioButtons:
            if radioButton.IsChecked:
                groupStrategy = radioButton.Tag
                break

        if groupStrategy == None:
            MessageBox.Show("Please select a parking strategy for your new group!", "Depot GUI - Create New Group", MessageBoxButton.OK, MessageBoxImage.Warning)
        else:
            if self.TxtGroupName.Text == "":
                MessageBox.Show("Group name must not be empty!", "Depot GUI - Create New Group", MessageBoxButton.OK, MessageBoxImage.Warning)
            else:
                if self.selectedGroup is not None:
                    #edit group
                    oldTag = self.selectedGroup.ID
                    tvItem = self.getTreeViewItem(oldTag)
                
                    self.selectedGroup.ID = self.TxtGroupName.Text
                    self.selectedGroup.parkingStrategy = groupStrategy

                    #update tv
                    tvItem.Tag = self.selectedGroup.ID
                    del self.areas[oldTag]
                    self.areas[self.selectedGroup.ID] = self.selectedGroup

                    self.areaCounter = 0
                    self.sortTreeView(self.TvDepotAreas)   
                else:
                    #new group
                    self.addAreaGroup(self.TxtGroupName.Text, groupStrategy)

    def BtnOk_Click(self, sender, eventArgs):
        try:
            # self.saveDepot()

            self.parent.updateTitle()
            self.parent.updateConfigDisplay()
            self.Window.DialogResult = System.Nullable[System.Boolean](True)
        except:
            traceback.print_exc()

    def LbTemplates_SelectionChanged(self, sender, eventArgs):
        pass

    def LbTemplates_MouseDoubleClick(self, sender, eventArgs):
        if self.LbTemplates.SelectedItem is not None:
            self.loadTemplate(self.parent.path_templates +
                              self.LbTemplates.SelectedItem[:-len('.json')])

    def TvDepotAreas_PreviewMouseLeftButtonDown(self, sender, eventArgs):
        try:
            self.dragStartPosition = eventArgs.GetPosition(None)
        except:
            traceback.print_exc()

    def TvDepotAreas_SelectedItemChanged(self, sender, eventArgs):
        if self.TvDepotAreas.SelectedItem is not None:
            selectedArea = self.areas[self.TvDepotAreas.SelectedItem.Tag]
            if type(selectedArea) is eflips.depot.depot.AreaGroup:
                self.selectedGroup = selectedArea
            else:
                self.selectedGroup = None

        if self.selectedGroup is not None:
            #set "edit group"
            self.GrbNewGroup.Header = "Edit existing group"
            self.TxtGroupName.Text = self.selectedGroup.ID
            for radioButton in self.listRadioButtons:
                radioButton.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(radioButton.Tag == self.selectedGroup.parkingStrategy)
            self.BtnSaveGroup.Content = "Save"
        else:
            #reset to "create new group"
            self.GrbNewGroup.Header = "Create new group"
            self.TxtGroupName.Text = "new group"
            for radioButton in self.listRadioButtons:
                radioButton.IsChecked = eflips.depot.gui.depot_view.convertToNullableBool(False)
            self.BtnSaveGroup.Content = "Add"

    def TvDepotAreas_MouseMove(self, sender, eventArgs):
        """
        (part of the drag&drop feature)
        """
        try:
            if eventArgs.LeftButton == MouseButtonState.Pressed:
                currentPosition = eventArgs.GetPosition(None)
                diffX = self.dragStartPosition.X - currentPosition.X
                diffY = self.dragStartPosition.Y - currentPosition.Y

                if math.fabs(diffX) > SystemParameters.MinimumHorizontalDragDistance or math.fabs(diffY) > SystemParameters.MinimumVerticalDragDistance:
                    treeView = sender
                    treeViewItem = eventArgs.OriginalSource

                    if treeView == None or treeViewItem == None:
                        return
                    folderViewModel = treeView.SelectedItem
                    if folderViewModel == None:
                        return;

                    dragData = DataObject(folderViewModel)
                    DragDrop.DoDragDrop(treeViewItem, dragData, DragDropEffects.Move)
        except:
            traceback.print_exc()

    def TvDepotAreas_DragEnter(self, sender, eventArgs):
        """
        (part of the drag&drop feature)
        """
        try:
            if not type(eventArgs.Data.GetDataPresent("System.Windows.Controls.TreeViewItem")) is TreeViewItem:
                eventArgs.Effects = 0
        except:
            traceback.print_exc()

    def TvDepotAreas_Drop(self, sender, eventArgs):
        """
        (part of the drag&drop feature)
        """
        try:
            targetItem = sender
            draggedItem = eventArgs.Data.GetData("System.Windows.Controls.TreeViewItem")
            if draggedItem is not None and targetItem is not None:
                if type(targetItem.Parent) is Grid \
                        or (type(targetItem.Parent) is TreeView
                            and type(self.areas[draggedItem.Tag])
                            is not eflips.depot.depot.AreaGroup
                            and type(self.areas[targetItem.Tag])
                            is eflips.depot.depot.AreaGroup
                            and draggedItem.Items.Count == 0): # only 1st level drops, 2nd level only groups
                    if draggedItem.Parent is not None:
                        #remove item from old group
                        draggedItem.Parent.Items.Remove(draggedItem)

                    #add item to new group
                    targetItem.Items.Add(draggedItem)
                    
                    eventArgs.Handled = True
                    self.areaCounter = 0
                    self.sortTreeView(self.TvDepotAreas)
        except:
            traceback.print_exc()

    def TxtGroupName_KeyDown(self, sender, eventArgs):
        """
        Unused until settings GUI update: configurator.add_group will be used
        together with frontend actions
        """
        if eventArgs.Key == 6:
            self.addAreaGroup(self.TxtGroupName.Text)
    #endregion

    def getTreeViewItem(self, tag):
        """
        Finds tree view item from given [tag].
        """
        for tvItem in self.TvDepotAreas.Items:
            if tvItem.Tag == tag:
                return tvItem
            for childItem in tvItem.Items:
                if childItem.Tag == tag:
                    return childItem
        return None

    def sortTreeView(self, item):
        """
        Sorts the tree view (tree of depot areas) according to their [areaNo].
        Gets called recursively. First call needs to have item = self.TvDepotAreas.
        """
        for child in item.Items:

            child.TabIndex = self.areaCounter   # workaround for sorting
            self.areaCounter += + 1

            if type(child.Parent) is TreeView:
                child.Header = "[" + str(child.TabIndex) + "] " + child.Tag
            else:
                child.Header = "(" + str(child.TabIndex) + ") " + child.Tag
            
            self.sortTreeView(child)    # recursive call for children (next level)
            item.IsExpanded = True      # expand parent to view children

        # sort items visually
        item.Items.SortDescriptions.Clear()
        item.Items.SortDescriptions.Add(SortDescription("TabIndex", ListSortDirection.Ascending));

    #region template methods
    def listTemplates(self):
        """
        Lists json templates from [path] (see below) source directory in self.LbTemplates.
        """
        self.LbTemplates.Items.Clear()
        path = self.parent.path_templates
        if not os.path.exists(path):
            os.makedirs(path)

        for filename in os.listdir(path):
            if isfile(join(path, filename)) and filename.endswith(".json"):
                self.LbTemplates.Items.Add(filename)

    def loadTemplate(self, filename):
        """
        Resets current depot and loads depot template.
        Asks for confirmation.
        """
        if MessageBox.Show("The current depot will be discarded. Are you sure?", "Depot GUI - Load Depot Template", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes:
            if self.parent.loadTemplate(filename):
                self.Window.Close()

    def saveTemplate(self, filename):
        """
        Saves the current depot in [filename] as a json template.
        """
        if filename is not None:
            success, errormsg = self.parent.configurator.save(filename)
        if success:
            MessageBox.Show("Template saved successfully.",
                            "Depot GUI - Save Template", MessageBoxButton.OK,
                            MessageBoxImage.Information)
            self.listTemplates()
        else:
            MessageBox.Show("Error while saving template! " + errormsg,
                            "Depot GUI - Save Template", MessageBoxButton.OK,
                            MessageBoxImage.Error)

        # if filename is not None:
        #     result = eflips.depot.gui.depotView.writeJsonFile(self.parent.path_templates + "depots\\" + filename, self.parent.toJson())
        #     if result == 1:
        #         MessageBox.Show("Template saved successfully.", "Depot GUI - Save Template", MessageBoxButton.OK, MessageBoxImage.Information)
        #         self.listTemplates()
        #     elif result == -1:
        #         MessageBox.Show("Error while saving template!", "Depot GUI - Save Template", MessageBoxButton.OK, MessageBoxImage.Error)

    def removeTemplate(self, filename):
        """
        Removes the currently selected template in the list.
        Asks for confirmation.
        """
        filename = self.parent.path_templates + "depots\\" + filename
        if os.path.exists(filename) and isfile(filename) and MessageBox.Show("Do you really want to delete this template?", "Depot GUI - Confirm Delete Template", MessageBoxButton.YesNo, MessageBoxImage.Question) == MessageBoxResult.Yes:
            os.remove(filename)
            self.listTemplates()
    #endregion

    def addAreaGroup(self, groupName, parkingStrategy):
        """
        Adds a new AreaGroup instance as an parking area group to the depot.

        Unused until settings GUI update: configurator.add_group will be used
        together with frontend actions
        """
        if not groupName in self.areas:
            import eflips
            newGroup = eflips.depot.depot.AreaGroup(self.parent.env, [], groupName, parkingStrategy = parkingStrategy)
            self.areas[groupName] = newGroup

            tvItem = TreeViewItem()
            tvItem.AllowDrop = True
            tvItem.Drop += self.TvDepotAreas_Drop
            tvItem.Tag = newGroup.ID
            tvItem.FontWeight = FontWeights.Bold
            self.TvDepotAreas.Items.Add(tvItem)

            self.areaCounter = 0
            self.sortTreeView(self.TvDepotAreas)
            self.TxtGroupName.Text = "new group"
        else:
            MessageBox.Show("A group with the same name already exists.", "Depot GUI", MessageBoxButton.OK, MessageBoxImage.Exclamation)

    # def saveDepot(self):
    #     """
    #     Saves the current depot configuration.
    #
    #     Unused until settings GUI update. Configuration changes can't be
    #     reverted/canceling not possible until then.
    #     """
    #     areaNo = 0
    #     listNewAreaIds = []
    #
    #     # clear assignment_groups
    #     self.depot.assignment_groups = []
    #
    #     #update existing areas
    #     for item in self.TvDepotAreas.Items:
    #         depotArea = self.areas[item.Tag]
    #
    #         if type(depotArea) is eflips.depot.AreaGroup and item.Items.Count == 0:
    #             #remove empty group
    #             self.parent.removeAreaFromFrontend(depotArea)
    #             continue
    #
    #         depotArea.assignmentGroup = None
    #         depotArea.areaNo = areaNo
    #         areaNo = areaNo + 1
    #
    #         self.parent.areas[depotArea.ID] = depotArea
    #         listNewAreaIds.append(depotArea.ID)
    #
    #         if isinstance(depotArea, eflips.depot.AreaGroup):
    #
    #             childAreas = []
    #             for child in item.Items:
    #                 childArea = self.areas[child.Tag]
    #                 childArea.areaNo = areaNo
    #                 areaNo = areaNo + 1
    #
    #                 self.parent.areas[childArea.ID] = childArea
    #                 listNewAreaIds.append(childArea.ID)
    #
    #                 childAreas.append(childArea)
    #                 childArea.assignmentGroup = depotArea
    #
    #                 if hasattr(childArea, "view"):
    #                     childArea.view.update()
    #
    #             depotArea.depot = self.depot
    #             depotArea.list_stores = childAreas
    #             self.depot.addAssignmentGroup(depotArea)
    #
    #         if hasattr(depotArea, "view"):
    #             depotArea.view.update()
    #
    #     #remove areas
    #     listOldAreaIds = [x for x in self.parent.getAreas().keys()]
    #     for areaId in listOldAreaIds:
    #         if areaId not in listNewAreaIds:
    #             self.parent.removeAreaFromFrontend(self.parent.areas[areaId])
    #
    #     # self.parent.updateDepotMeta()
    #
    #     self.configurator.templatename = self.TxtTemplateName.Text
    #     self.parent.updateTitle()

    def show(self):
        return self.Window.ShowDialog()
