"""FastAPI dependency injection providers."""

from backend.config import settings
from backend.storage import LocalStorageBackend, StorageBackend

_storage_backend: StorageBackend = LocalStorageBackend()


def get_settings():
    return settings


def get_storage() -> StorageBackend:
    return _storage_backend
