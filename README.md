[![Tests](https://github.com/mpm-tu-berlin/eflips-depot/actions/workflows/postgres_eflips_depot.yml/badge.svg)](https://github.com/mpm-tu-berlin/eflips-depot/actions/workflows/postgres_eflips_depot.yml)

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
   #### macOS
    ```bash
    poetry env use 3.11
    poetry install
    ```
    #### Windows
   If you are using Windows, you have to provide the full path to the desired Python executable, e.g.:
    ```bash
   poetry env use C:\Users\user\AppData\Local\Programs\Python\Python311\python.exe
   poetry install
   ```

3. To start a simulation, the script `bus_depot/STARTSIM_busdepot.py` needs to be executed. This loads the 3 necessary files for
   settings, schedule and template for depot layout. After the execution, all relevant results are in the `ev` variable
   in the workspace (e.g. if you are using PyCharm as your IDE, if the script is run with the "Run with Python Console" option). To analyse or plot
   results the example calls for the console in ```eflips/depot/plots.py``` can be used.
    ```python
    import os
    os.chdir('bus_depot') # Optional, if not already in the bus_depot folder
    exec(open(os.path.join('STARTSIM_busdepot.py')).read())
    
    ev.sl_all() # For example to plot a result
    ```
4. To use eFLIPS-Depot API, see script `bus_depot/user_example.py`

## Usage

Please refer to the [Documentation section](#documentation) of this Readme for information
on how to use eFLIPS-Depot.

The API of eFLIPS-Depot can be accessed via ```eFLIPS.depot.api```. A usage example can be found in the
script `bus_depot/user_example.py`. Furthermore, until an official documentation of the API
is available, you can manually check the files inside the ```eflips/depot/api``` folder to get more insights on how to use the API. 

## Testing

---

**NOTE**: Be aware that the tests will clear the database specified in the `DATABASE_URL` environment variable. Make sure that you are not using a database that you want to keep.

---

Testing is done using the `pytest` framework with tests located in the `tests`directory. To run the tests, execute the following command in the root directory of the repository:

```bash
   export PYTHONPATH=tests:. # To make sure that the tests can find the eflips package
   export DATABASE_URL=postgis://postgres:postgres@localhost:5432/postgres # Or whatever your database URL is
   export DJANGO_SETTINGS_MODULE=tests.api.djangosettings # To make sure that the tests use the correct settings
   pytest
```
## Documentation

Documentation is automatically created from the docstrings in the code using [sphinx-autoapi](https://sphinx-autoapi.readthedocs.io/en/latest/). If you have downloaded a specific release, the documentation is included in the `docs` directory. If you have cloned the repository, you can create the documentation yourself by executing the following command in the root directory of the repository:

```bash
   cd docs/
   sphinx-build -b html . _build
```


## Development

We utilize the [GitHub Flow](https://docs.github.com/get-started/quickstart/github-flow) branching structure. This means
that the `main` branch is always deployable and that all development happens in feature branches. The feature branches
are merged into `main` via pull requests.

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

We use [black](https://black.readthedocs.io/en/stable/) for code formatting. You can use 
[pre-commit](https://pre-commit.com/) to ensure the code is formatted correctly before committing. You are also free to
use other methods to format the code, but please ensure that the code is formatted correctly before committing.

Please make sure that your `poetry.lock` and `pyproject.toml` files are consistent before committing. You can use `poetry check` to check this. This is also checked by pre-commit.

## License

This project is licensed under the AGPLv3 license - see the [LICENSE](LICENSE.md) file for details.
