"""
Django settings for ebusdjango project.

Generated by 'django-admin startproject' using Django 4.2.

For more information on this file, see
https://docs.djangoproject.com/en/4.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.2/ref/settings/
"""
import os
import tempfile
from pathlib import Path
import environ

# Only uncomment when used as external dependency
# from django.conf.global_settings import *

# Build paths inside the project like this: BASE_DIR / 'subdir'.

BASE_DIR = Path(__file__).resolve()
ROOT_DIR = environ.Path(__file__) - 1
env = environ.Env()
env.read_env(str(ROOT_DIR.path(".env")))
ALLOWED_HOSTS = []

# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_extensions",
    # custom apps
    "core",
    "ebustoolbox",
    "ebus_map",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # if the header and footer tags are in use this setting should be used. No problem if not in use
    "django_plotly_dash.middleware.BaseMiddleware",
    "core.middleware.TimezoneMiddleware",
]

UPLOAD_PATH = tempfile.gettempdir()

DATABASES = {"default": env.db("DATABASE_URL")}


# CELERY_BROKER_URL = None
CELERY_USE = False


LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

MAP_ENGINE_CLUSTER_ZOOM = 12

STATIC_URL = "static/"