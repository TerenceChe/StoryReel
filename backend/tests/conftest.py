"""Shared pytest configuration for backend tests."""

import os

import pytest


def pytest_configure(config):
    """Set asyncio mode to auto so async tests work without explicit markers."""
    config.option.asyncio_mode = "auto"
    # Ensure Auth0 config is always available so auth doesn't block tests.
    os.environ.setdefault("AUTH0_DOMAIN", "test-auth0.example.com")
    os.environ.setdefault("AUTH0_AUDIENCE", "test-audience")
