"""Shared pytest configuration for backend tests."""

import pytest


def pytest_configure(config):
    """Set asyncio mode to auto so async tests work without explicit markers."""
    config.option.asyncio_mode = "auto"
