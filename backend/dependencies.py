"""FastAPI dependency injection providers."""

import logging
import os

from backend.config import settings
from backend.models.image_gen import (
    DisabledImageBackend,
    ImageGenerationBackend,
)
from backend.persistence import LocalStorageBackend, StorageBackend
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectService
from backend.services.pipeline_service import PipelineService

logger = logging.getLogger(__name__)

_storage_backend: StorageBackend = LocalStorageBackend()
_project_service: ProjectService = ProjectService(_storage_backend, settings)
_pipeline_service: PipelineService = PipelineService(
    _storage_backend, _project_service, settings
)


def _build_image_backend() -> ImageGenerationBackend:
    """Pick the image-generation backend based on environment configuration.

    The provider is selected once at process startup from
    ``IMAGE_GEN_PROVIDER`` (lowercased and trimmed). For each known
    provider we read its credential from a provider-specific environment
    variable; if the credential is empty or the provider name is unknown
    or unset, we fall back to :class:`DisabledImageBackend` so the rest of
    the system stays cleanly importable and the capability endpoint
    reports ``image_generation_enabled=false``.

    Logging discipline (per design.md):
    * NEVER log the API key value, in any form.
    * NEVER log the credential environment variable name when the
      backend is disabled.
    * Emit exactly one ``image_generation enabled=<bool>`` line at INFO
      level so operators can confirm provisioning without leaking which
      variable they need to set.
    """
    provider = (os.getenv("IMAGE_GEN_PROVIDER") or "").strip().lower()
    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY") or ""
        if key:
            try:
                # The concrete adapter is implemented in Task 16.1. The
                # dependencies layer is forward-compatible: if the module
                # is not yet present (or fails to import for any reason),
                # we log a sanitized notice and fall through to the
                # disabled fallback rather than crash startup. The error
                # message is intentionally vague so a misconfiguration
                # doesn't leak provider identity into operator logs.
                from backend.services.image_backends.openai import (  # noqa: I001
                    OpenAIImageBackend,
                )

                return OpenAIImageBackend(api_key=key)
            except ImportError:
                logger.warning(
                    "image_generation provider adapter unavailable; "
                    "falling back to disabled backend"
                )
    return DisabledImageBackend()


_image_backend: ImageGenerationBackend = _build_image_backend()
_job_manager: JobManager = JobManager(
    storage=_storage_backend,
    project_service=_project_service,
    settings=settings,
    backend=_image_backend,
)

# Single startup log line. The boolean is the only piece of provisioning
# information that crosses the log boundary — never the key, never the
# variable name when disabled.
logger.info(
    "image_generation enabled=%s",
    not isinstance(_image_backend, DisabledImageBackend),
)


def get_settings():
    return settings


def get_storage() -> StorageBackend:
    return _storage_backend


def get_project_service() -> ProjectService:
    return _project_service


def get_pipeline_service() -> PipelineService:
    return _pipeline_service


def get_image_backend() -> ImageGenerationBackend:
    """Return the process-wide :class:`ImageGenerationBackend` binding.

    Resolved once at module import time by :func:`_build_image_backend`.
    Tests override this dependency via ``app.dependency_overrides`` to
    inject a ``FakeImageBackend``.
    """
    return _image_backend


def get_job_manager() -> JobManager:
    """Return the process-wide :class:`JobManager` singleton.

    Constructed once at module import time and bound to the same
    ``ImageGenerationBackend`` returned by :func:`get_image_backend` so
    the capability gate and the worker observe the same provider.
    Tests override this dependency via ``app.dependency_overrides``.
    """
    return _job_manager
