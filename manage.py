#!/usr/bin/env python

"""enable manage.py makemigrations and migrate"""
import busmodel.settings


def init_django():
    import django
    from django.conf import settings

    settings.configure(
        busmodel.settings)  # TODO: Override the database location with a sensible value taken from a settings file using configparser (or maybe something different?)

    django.setup()


if __name__ == "__main__":
    from django.core.management import execute_from_command_line

    init_django()
    execute_from_command_line()
