"""FastAPI dependency injection providers."""

from backend.config import settings


def get_settings():
    return settings
