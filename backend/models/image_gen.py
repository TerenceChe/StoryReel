"""Abstract interface for AI image generation providers."""

from abc import ABC, abstractmethod


class ImageGenerationDisabledError(Exception):
    """Raised by the disabled image-generation backend on any generation call.

    The router translates this exception to an HTTP 503 with a generic,
    operator-opaque message. The exception message itself MUST stay generic
    (no provider names, no environment variable names) since it is used as
    the user-facing detail in the 503 response.
    """


class ImageGenerationBackend(ABC):

    @abstractmethod
    async def generate_single(self, prompt: str) -> bytes:
        """Generate a single image from a text prompt."""
        ...

    @abstractmethod
    async def generate_sectioned(self, prompts: list[str]) -> list[bytes]:
        """Generate images for multiple story sections."""
        ...


class DisabledImageBackend(ImageGenerationBackend):
    """Fallback backend selected when no provider is configured at startup.

    Every generation method raises :class:`ImageGenerationDisabledError` so
    the router never has to special-case a missing provider. The candidate
    methods (`generate_candidates`, `generate_section_candidates`) are not
    on the abstract base — they are duck-typed on concrete adapters per the
    design — but the disabled backend implements them too so the JobManager
    can call them uniformly without an `isinstance` check.
    """

    _DISABLED_MESSAGE = "Image generation is not configured"

    async def generate_single(self, prompt: str) -> bytes:
        raise ImageGenerationDisabledError(self._DISABLED_MESSAGE)

    async def generate_sectioned(self, prompts: list[str]) -> list[bytes]:
        raise ImageGenerationDisabledError(self._DISABLED_MESSAGE)

    async def generate_candidates(
        self,
        prompt: str,
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[bytes]:
        raise ImageGenerationDisabledError(self._DISABLED_MESSAGE)

    async def generate_section_candidates(
        self,
        prompts: list[str],
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[list[bytes]]:
        raise ImageGenerationDisabledError(self._DISABLED_MESSAGE)
