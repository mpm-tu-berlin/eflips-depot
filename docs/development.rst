Development
===========

We utilize the `GitHub
Flow <https://docs.github.com/get-started/quickstart/github-flow>`__
branching structure. This means that the ``main`` branch is always
deployable and that all development happens in feature branches. The
feature branches are merged into ``main`` via pull requests.

We use `black <https://black.readthedocs.io/en/stable/>`__ for code
formatting. You can use `pre-commit <https://pre-commit.com/>`__ to
ensure the code is formatted correctly before committing. You are also
free to use other methods to format the code, but please ensure that the
code is formatted correctly before committing.

Please make sure that your ``poetry.lock`` and ``pyproject.toml`` files
are consistent before committing. You can use ``poetry check`` to check
this. This is also checked by pre-commit.

Testing
-------



**NOTE**: Be aware that the tests will clear the database specified in
the ``DATABASE_URL`` environment variable. Make sure that you are not
using a database that you want to keep.



Testing is done using the ``pytest`` framework with tests located in the
``tests``\ directory. To run the tests, execute the following command in
the root directory of the repository:

.. code:: bash

      export PYTHONPATH=tests:. # To make sure that the tests can find the eflips package
      export DATABASE_URL=postgis://postgres:postgres@localhost:5432/postgres # Or whatever your database URL is
      export DJANGO_SETTINGS_MODULE=tests.api.djangosettings # To make sure that the tests use the correct settings
      pytest

Documentation
-------------

Documentation is automatically created from the docstrings in the code
using
`sphinx-autoapi <https://sphinx-autoapi.readthedocs.io/en/latest/>`__.
If you have downloaded a specific release, the documentation is included
in the ``docs`` directory. If you have cloned the repository, you can
create the documentation yourself by executing the following command in
the root directory of the repository:

.. code:: bash

      cd docs/
      sphinx-build -b html . _build
