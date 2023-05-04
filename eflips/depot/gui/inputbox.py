"""
@author: B.Bober

This is a simple UI InputBox that returns a string value.

Example code:

inputBox = InputBox(self, [TITLE], [PROMPT])
if inputBox.show():
    DO_STUFF(inputBox.returnValue)
inputBox = None
"""

import eflips.depot.gui.depot_view
from eflips.depot.gui.depot_view import *

class InputBox(Window):

    def __init__(self, parent, title, description, defaultInput = ""):
        self.parent = parent
        self.Owner = parent.Window

        global main_view
        stream = StreamReader(main_view.path_root + "eflips\\depot\\gui\\inputbox.xaml")
        self.Window = XamlReader.Load(stream.BaseStream)
        self.Window.Title += " - " + title
        
        self.TxtDescription = self.Window.FindName("TxtDescription")
        self.TxtDescription.Text = description

        self.TxtInput = self.Window.FindName("TxtInput")
        self.TxtInput.Text = defaultInput

        self.BtnOk = self.Window.FindName("BtnOk")
        self.BtnOk.Click += self.BtnOk_Click

    def BtnOk_Click(self, sender, eventArgs):
        self.returnValue = self.TxtInput.Text
        self.Window.DialogResult = System.Nullable[System.Boolean](True)

    def show(self):
        return self.Window.ShowDialog()