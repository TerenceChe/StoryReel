"""Shared pytest configuration for backend tests."""

import os

import pytest


def pytest_configure(config):
    """Set asyncio mode to auto so async tests work without explicit markers."""
    config.option.asyncio_mode = "auto"
    # Ensure a test API secret key is always available so auth doesn't block tests.
    os.environ.setdefault("API_SECRET_KEY", "test-secret-key")
