"""FastAPI dependency injection providers."""

from backend.config import settings
from backend.persistence import LocalStorageBackend, StorageBackend
from backend.services.project_service import ProjectService
from backend.services.pipeline_service import PipelineService

_storage_backend: StorageBackend = LocalStorageBackend()
_project_service: ProjectService = ProjectService(_storage_backend, settings)
_pipeline_service: PipelineService = PipelineService(
    _storage_backend, _project_service, settings
)


def get_settings():
    return settings


def get_storage() -> StorageBackend:
    return _storage_backend


def get_project_service() -> ProjectService:
    return _project_service


def get_pipeline_service() -> PipelineService:
    return _pipeline_service
