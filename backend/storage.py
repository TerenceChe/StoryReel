"""Storage backend abstraction and local filesystem implementation."""

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator

import aiofiles
import aiofiles.os

from backend.config import settings


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


class LocalStorageBackend(StorageBackend):
    """Storage backend using the local filesystem.

    Files are stored under ``{base_dir}/projects/{project_id}/``.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.DATA_DIR)

    def _project_dir(self, project_id: str) -> Path:
        return self.base_dir / "projects" / project_id

    def _file_path(self, project_id: str, filename: str) -> Path:
        return self._project_dir(project_id) / filename

    async def save_file(
        self, project_id: str, filename: str, data: AsyncIterator[bytes]
    ) -> str:
        dest = self._file_path(project_id, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)

        async with aiofiles.open(dest, "wb") as f:
            async for chunk in data:
                await f.write(chunk)

        return str(dest)

    async def load_file(
        self, project_id: str, filename: str
    ) -> AsyncIterator[bytes]:
        path = self._file_path(project_id, filename)
        if not path.exists():
            raise FileNotFoundError(
                f"File not found: {filename} in project {project_id}"
            )

        async def _stream() -> AsyncIterator[bytes]:
            async with aiofiles.open(path, "rb") as f:
                while chunk := await f.read(8192):
                    yield chunk

        return _stream()

    async def get_file_url(self, project_id: str, filename: str) -> str:
        return f"/projects/{project_id}/media/{filename}"

    async def delete_project(self, project_id: str) -> None:
        project_dir = self._project_dir(project_id)
        if project_dir.exists():
            shutil.rmtree(project_dir)
