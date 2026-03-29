"""FastAPI dependency injection providers."""

from backend.config import settings
from backend.persistence import LocalStorageBackend, StorageBackend
from backend.services.project_service import ProjectService

_storage_backend: StorageBackend = LocalStorageBackend()
_project_service: ProjectService = ProjectService(_storage_backend, settings)


def get_settings():
    return settings


def get_storage() -> StorageBackend:
    return _storage_backend


def get_project_service() -> ProjectService:
    return _project_service
