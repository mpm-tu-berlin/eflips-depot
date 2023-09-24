Usage
=====

Installation
------------

1. Clone this git repository (or [download a specific release](https://github.com/mpm-tu-berlin/eflips-depot/releases))

    .. code-block:: console

       git clone git@github.com:mpm-tu-berlin/eflips-depot.git


2. Install the packages listed in `poetry.lock` and `pyproject.toml` into your Python environment. Notes:
    - The suggested Python version os 3.11.*, it may work with other versions, but this is not tested.
    - The supported platforms are macOS and Windows, Linux should work, but is not tested.
    - Using the [poetry](https://python-poetry.org/) package manager is recommended. It can be installed according to the instructions listed [here](https://python-poetry.org/docs/#installing-with-the-official-installer).

    .. code-block:: console

        poetry env use 3.11
        poetry install



3. To start a simulation, the script `STARTSIM_busdepot.py` needs to be executed. This loads the 3 necessary files for settings, schedule and template for depot layout. After the execution, all relevant results are in the `ev` variable in the workspace (if the script is run with a "keep python running after last statement" option) . To analyse or plot results the example calls for the console in eflips/depot/plots.py can be used.
    .. code-block:: console

       import os
       os.chdir('bus_depot') # Optional, if not already in the bus_depot folder
       exec(open(os.path.join('STARTSIM_busdepot.py')).read())
       ev.sl_all() # For example to plot a result

4. To use eFLIPS-Depot API, see script `bus_depot/user_example.py`
