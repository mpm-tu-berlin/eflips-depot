# eflips-depot

eFLIPS has been developed within several research projects at the Department of Methods of Product Development and
Mechatronics at the Technische Universität Berlin (see https://www.tu.berlin/mpm/forschung/projekte/eflips).

With eFLIPS, electric fleets and depots can be simulated, planned and designed.
This repository contains only the planning and design tool for depots.

![eflips_overview](https://user-images.githubusercontent.com/74250473/236144949-4192e840-0e3d-4b65-9f78-af8e01ad9ef3.png)

The repository contains an example for the simulation and planning of an elecric bus depot, which is based on the
dissertation by Dr.-Ing. Enrico Lauth (see https://depositonce.tu-berlin.de/items/f47662f7-c9ae-4fbf-9e9c-bcd307b73aa7).

## Installation

1. Clone this git repository (or [download a specific release](https://github.com/mpm-tu-berlin/eflips-depot/releases))

    ```bash
    git clone git@github.com:mpm-tu-berlin/eflips-depot.git
    ```
2. Install the packages listed in `poetry.lock` and `pyproject.toml` into your Python environment. Notes:
    - The suggested Python version os 3.11.*, it may work with other versions, but this is not tested.
    - The supported platforms are macOS and Windows, Linux should work, but is not tested.
    - Using the [poetry](https://python-poetry.org/) package manager is recommended. It can be installed accoring to the
      instructions listed [here](https://python-poetry.org/docs/#installing-with-the-official-installer).
    ```bash
    poetry env use 3.11
    poetry install
    ```

3. To start a simulation, the script `STARTSIM_busdepot.py` needs to be executed. This loads the 3 necessary files for
   settings, schedule and template for depot layout. After the execution, all relevant results are in the `ev` variable
   in the workspace (if the script is run with a "keep python running after last statement" option) . To analyse or plot
   results the example calls for the console in eflips/depot/plots.py can be used.
    ```python
    import os
    os.chdir('bus_depot') # Optional, if not already in the bus_depot folder
    exec(open(os.path.join('STARTSIM_busdepot.py')).read())
    
    ev.sl_all() # For example to plot a result
    ```

## Testing

*There is no real testing yet. We are moving towards using [pytest](https://docs.pytest.org/) for testing, but this is
not yet complete.*

## Documentation

*There is no real documentation yet. We are moving towards using [sphinx](https://www.sphinx-doc.org/en/master/) for
documentation, but this is not yet complete.*

## Development

We utilize the [GitHub Flow](https://docs.github.com/get-started/quickstart/github-flow) branching structure. This means
that the `main` branch is always deployable and that all development happens in feature branches. The feature branches
are merged into `main` via pull requests.

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

We use [black](https://black.readthedocs.io/en/stable/) for code formatting. You can use 
[pre-commit](https://pre-commit.com/) to ensure the code is formatted correctly before committing. You are also free to
use other methods to format the code, but please ensure that the code is formatted correctly before committing.

## License

This project is licensed under the AGPLv3 license - see the [LICENSE](LICENSE.md) file for details.