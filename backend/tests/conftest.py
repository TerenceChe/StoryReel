"""Shared pytest configuration for backend tests."""

import os

import pytest


def pytest_configure(config):
    """Set asyncio mode to auto so async tests work without explicit markers."""
    config.option.asyncio_mode = "auto"
    # Ensure Auth0 config is always available so auth doesn't block tests.
    os.environ.setdefault("AUTH0_DOMAIN", "test-auth0.example.com")
    os.environ.setdefault("AUTH0_AUDIENCE", "test-audience")


@pytest.fixture()
def fake_image_backend():
    """Fresh :class:`FakeImageBackend` per test, with FastAPI dependency override.

    Every test gets a brand-new ``FakeImageBackend`` instance — the ``calls``
    list and ``last_reference_image`` are therefore per-test by construction
    and don't need to be reset inside the test body.

    The fixture also wires the fake into the FastAPI dependency-injection
    container so any router that depends on ``get_image_backend`` (the
    dependency Task 15.1 will introduce) will receive this instance during
    the test. Today, ``backend.dependencies.get_image_backend`` does not
    exist yet, so the override path is a no-op and the fixture simply
    yields the fake. Once Task 15.1 lands the fixture will start patching
    ``app.dependency_overrides`` automatically — no test changes required.

    TODO(task-15.1): once ``get_image_backend`` exists in
    ``backend.dependencies``, this fixture's override path will be exercised
    end-to-end by the image-jobs router tests.
    """
    # Imports are inside the fixture body so importing this conftest does not
    # trigger circular imports during module collection (``backend.main``
    # transitively imports several services that import ``backend.config``).
    from backend import dependencies as _deps
    from backend.main import app
    from backend.tests._image_fakes import FakeImageBackend

    fake = FakeImageBackend()

    get_image_backend = getattr(_deps, "get_image_backend", None)
    if get_image_backend is not None:
        app.dependency_overrides[get_image_backend] = lambda: fake
        try:
            yield fake
        finally:
            app.dependency_overrides.pop(get_image_backend, None)
    else:
        # Task 15.1 has not landed yet — no FastAPI dependency to override.
        # Tests that just need the fake instance still work; tests that rely
        # on the router seeing the fake will start passing once 15.1 lands.
        yield fake
