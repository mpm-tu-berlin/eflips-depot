# eflips-depot

eFLIPS has been developed within several research projects at the Department of Methods of Product Development and Mechatronics at the Technische Universität Berlin (see https://www.tu.berlin/en/mpm/research/projects/eflips).

With eFLIPS, electric fleets and depots can be simulated, planned and designed.
This repository contains only the planning and design tool for depots.

![eflips_overview](https://user-images.githubusercontent.com/74250473/236144949-4192e840-0e3d-4b65-9f78-af8e01ad9ef3.png)

The repository contains an example for the simulation and planning of an elecric bus depot, which is based on the dissertation by Dr.-Ing. Enrico Lauth (see https://depositonce.tu-berlin.de/items/f47662f7-c9ae-4fbf-9e9c-bcd307b73aa7).

To start a simulation, the script STARTSIM_busdepot.py need to be executed. This already contains the 3 necessary files for settings, schedule and template for depot layout. After the execution, all relevant results are in "ev" in the workspace. To analyse or plot results the example calls for the console in elfips/depot/plots.py can be used.  
