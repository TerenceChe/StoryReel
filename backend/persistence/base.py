"""Abstract storage backend interface."""

from abc import ABC, abstractmethod
from typing import AsyncIterator


class StorageBackend(ABC):
    """Abstract interface for file storage operations."""

    @abstractmethod
    async def save_file(
        self, project_id: str, filename: str, data: AsyncIterator[bytes]
    ) -> str:
        """Save file from an async byte stream and return its path/URL."""
        ...

    async def save_file_from_path(
        self, project_id: str, filename: str, source_path: str
    ) -> str:
        """Save file from a local path.

        Default implementation reads the file synchronously and delegates to
        save_file. Uses sync I/O which is acceptable since pipeline tasks run
        in background threads. Override in subclasses for async I/O if needed.
        """

        async def _read_chunks() -> AsyncIterator[bytes]:
            with open(source_path, "rb") as f:
                while chunk := f.read(8192):
                    yield chunk

        return await self.save_file(project_id, filename, _read_chunks())

    @abstractmethod
    async def load_file(
        self, project_id: str, filename: str
    ) -> AsyncIterator[bytes]:
        """Load file contents as an async byte stream."""
        ...

    @abstractmethod
    async def get_file_url(self, project_id: str, filename: str) -> str:
        """Get a URL/path to serve the file."""
        ...

    @abstractmethod
    async def delete_project(self, project_id: str) -> None:
        """Delete all files for a project."""
        ...
