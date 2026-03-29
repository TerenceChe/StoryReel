"""Local filesystem storage backend."""

import shutil
from pathlib import Path
from typing import AsyncIterator

import aiofiles

from backend.config import settings
from backend.persistence.base import StorageBackend


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
