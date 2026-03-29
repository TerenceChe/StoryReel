"""Abstract interface for AI image generation providers."""

from abc import ABC, abstractmethod


class ImageGenerationBackend(ABC):

    @abstractmethod
    async def generate_single(self, prompt: str) -> bytes:
        """Generate a single image from a text prompt."""
        ...

    @abstractmethod
    async def generate_sectioned(self, prompts: list[str]) -> list[bytes]:
        """Generate images for multiple story sections."""
        ...
