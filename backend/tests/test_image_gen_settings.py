"""Unit tests for image-generation settings defaults in :mod:`backend.config`.

Feature: ai-background-generation
Validates: Requirements 4.1, 7.1

The four image-generation settings on :class:`backend.config.Settings` are
read at *class import time* via ``os.getenv``. To exercise env-var behavior
we reload the ``backend.config`` module under a ``monkeypatch.setenv`` /
``monkeypatch.delenv`` context using :func:`importlib.reload`. Each test
then reads values from a freshly instantiated ``Settings`` on the reloaded
module so that class-level defaults are recomputed against the patched
environment.
"""

from __future__ import annotations

import importlib

import pytest

from backend import config as config_module


def _reload_config():
    """Reload :mod:`backend.config` and return the reloaded module.

    ``Settings`` evaluates its defaults at class-body evaluation time, so a
    fresh ``importlib.reload`` is required after env vars change.
    """
    return importlib.reload(config_module)


@pytest.fixture(autouse=True)
def _restore_config_module():
    """Reload :mod:`backend.config` after each test so the module-level
    ``settings`` instance is recomputed against the (unpatched) environment.

    Tests in this file use ``monkeypatch`` to set/unset env vars and then
    reload the config module. ``monkeypatch`` restores env vars on teardown,
    but the reloaded module retains the values it computed under the patched
    env. This fixture reloads once more against the restored environment so
    that subsequent test files see ``backend.config`` as if it had only ever
    been imported once.
    """
    yield
    importlib.reload(config_module)


def test_image_gen_provider_default_empty(monkeypatch):
    """With ``IMAGE_GEN_PROVIDER`` unset, the default is the empty string.

    Validates: Requirement 7.1
    """
    monkeypatch.delenv("IMAGE_GEN_PROVIDER", raising=False)
    reloaded = _reload_config()
    assert reloaded.Settings().IMAGE_GEN_PROVIDER == ""


def test_max_images_per_job_default_4(monkeypatch):
    """With ``MAX_IMAGES_PER_JOB`` unset, the default is ``4``.

    Validates: Requirement 4.1
    """
    monkeypatch.delenv("MAX_IMAGES_PER_JOB", raising=False)
    reloaded = _reload_config()
    assert reloaded.Settings().MAX_IMAGES_PER_JOB == 4


def test_max_concurrent_image_jobs_per_user_default_2(monkeypatch):
    """With ``MAX_CONCURRENT_IMAGE_JOBS_PER_USER`` unset, the default is ``2``.

    Validates: Requirement 7.1
    """
    monkeypatch.delenv("MAX_CONCURRENT_IMAGE_JOBS_PER_USER", raising=False)
    reloaded = _reload_config()
    assert reloaded.Settings().MAX_CONCURRENT_IMAGE_JOBS_PER_USER == 2


def test_max_reference_image_size_mb_falls_back_to_max_upload_size_mb(monkeypatch):
    """When ``MAX_REFERENCE_IMAGE_SIZE_MB`` is unset, the value falls back to
    the current ``MAX_UPLOAD_SIZE_MB`` (here pinned to 77 to prove fallback).

    Validates: Requirement 4.1 (size-cap parity with the existing upload cap)
    """
    monkeypatch.delenv("MAX_REFERENCE_IMAGE_SIZE_MB", raising=False)
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", "77")
    reloaded = _reload_config()
    settings = reloaded.Settings()
    assert settings.MAX_UPLOAD_SIZE_MB == 77
    assert settings.MAX_REFERENCE_IMAGE_SIZE_MB == 77


def test_max_images_per_job_env_override(monkeypatch):
    """Setting ``MAX_IMAGES_PER_JOB=10`` in the environment yields 10.

    Validates: Requirement 4.1 (env-var override path)
    """
    monkeypatch.setenv("MAX_IMAGES_PER_JOB", "10")
    reloaded = _reload_config()
    assert reloaded.Settings().MAX_IMAGES_PER_JOB == 10
