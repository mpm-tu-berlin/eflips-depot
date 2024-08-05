[![Tests](https://github.com/mpm-tu-berlin/eflips-depot/actions/workflows/unittests.yml/badge.svg)](https://github.com/mpm-tu-berlin/eflips-depot/actions/workflows/unittests.yml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)


# eflips-depot

---

Part of the [eFLIPS/simBA](https://github.com/stars/ludgerheide/lists/ebus2030) list of projects.

---


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

4. To start a simulation, an existing [eFLIPS-Model](https://github.com/mpm-tu-berlin/eflips-model) database is
   requires. Once you have that, you can run the script `bin/example.py`

## Usage

Please refer to the [Documentation section](#documentation) of this Readme for information
on how to use eFLIPS-Depot.

The API of eFLIPS-Depot can be accessed via ```eFLIPS.depot.api```. A usage example can be found in the
script `bus_depot/user_example.py`.

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

Documentation is available on [Read the Docs](https://eflips-depot.readthedocs.io/en/latest/).

To locally create the documentaiton from the docstrings in the code
using [sphinx-autoapi](https://sphinx-autoapi.readthedocs.io/en/latest/), you can create the documentation execute the
following command in the root directory of the repository:

```bash
   cd docs/
   sphinx-build -b html . _build
```


## Development

We utilize the [GitHub Flow](https://docs.github.com/get-started/quickstart/github-flow) branching structure. This means
that the `main` branch is always deployable and that all development happens in feature branches. The feature branches
are merged into `main` via pull requests.

We use [black](https://black.readthedocs.io/en/stable/) for code formatting. You can use 
[pre-commit](https://pre-commit.com/) to ensure the code is formatted correctly before committing. You are also free to
use other methods to format the code, but please ensure that the code is formatted correctly before committing.

Please make sure that your `poetry.lock` and `pyproject.toml` files are consistent before committing. You can use `poetry check` to check this. This is also checked by pre-commit.

## License

This project is licensed under the AGPLv3 license - see the [LICENSE](LICENSE.md) file for details.

## Funding Notice

This code was developed as part of the project [eBus2030+]([https://www.eflip.de/](https://www.now-gmbh.de/projektfinder/e-bus-2030/)) funded by the Federal German Ministry for Digital and Transport (BMDV) under grant number 03EMF0402.
