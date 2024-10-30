# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "eFLIPS-Depot"
copyright = "2023, MPM TU Berlin"
author = "MPM TU Berlin"
release = "4.3.17"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = ["sphinx.ext.autodoc", "autoapi.extension"]
autodoc_typehints = "description"

templates_path = ["_templates"]
exclude_patterns = []

# -- Options for AutoAPI -----------------------------------------------------

# At the moment, we are just documenteing the public API in `eflips/depot/api`.
# This means no documentation exists for the packages "above" it, which breaks
# the TOC. So we add the `eflips.depot.api` package to the TOC manually.
autoapi_add_toctree_entry = False

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "alabaster"
html_static_path = ["_static"]


# -- Options for autoapi -----------------------------------------------------
autoapi_dirs = ["../eflips/depot/api"]
