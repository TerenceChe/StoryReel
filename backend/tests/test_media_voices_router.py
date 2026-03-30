"""Unit tests for media, upload, and voices endpoints (task 5.3).

Covers:
- GET  /projects/{id}/media/{filename} — serve media, path traversal rejection (400)
- POST /projects/{id}/background       — upload background image (format, size limit)
- GET  /voices                          — list available voices (no auth)
"""

from __future__ import annotations

import asyncio
import io

import pytest
from fastapi.testclient import TestClient

from backend.auth.middleware import get_owner_id, get_settings as get_settings_auth
from backend.config import Settings
from backend.dependencies import get_pipeline_service, get_project_service, get_storage, get_settings as get_settings_dep
from backend.main import app
from backend.persistence.local import LocalStorageBackend
from backend.services.pipeline_service import PipelineService
from backend.services.project_service import ProjectService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_settings():
    s = Settings()
    s.API_SECRET_KEY = "test-key"
    s.DEV_OWNER_ID = "owner-a"
    s.MAX_PROJECTS_PER_USER = 20
    s.MAX_UPLOAD_SIZE_MB = 1  # 1 MB for easy testing
    return s


@pytest.fixture()
def storage(tmp_path):
    return LocalStorageBackend(base_dir=str(tmp_path))


@pytest.fixture()
def project_service(storage, test_settings):
    return ProjectService(storage=storage, settings=test_settings)


@pytest.fixture()
def pipeline_service(storage, project_service, test_settings):
    from unittest.mock import AsyncMock

    svc = PipelineService(
        storage=storage, project_service=project_service, settings=test_settings
    )
    svc.run_pipeline = AsyncMock()
    return svc


@pytest.fixture()
def client(test_settings, project_service, pipeline_service, storage):
    app.dependency_overrides[get_settings_auth] = lambda: test_settings
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[get_owner_id] = lambda: "owner-a"
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_pipeline_service] = lambda: pipeline_service
    app.dependency_overrides[get_storage] = lambda: storage
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


async def _write_fake_file(
    storage: LocalStorageBackend, project_id: str, filename: str, data: bytes
) -> None:
    async def _chunks():
        yield data

    await storage.save_file(project_id, filename, _chunks())


def _create_project(client: TestClient, text: str = "一个故事") -> dict:
    resp = client.post("/projects", json={"story_text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# GET /projects/{id}/media/{filename}
# ---------------------------------------------------------------------------


class TestServeMedia:
    def test_serve_existing_file(self, client, storage):
        created = _create_project(client)
        pid = created["id"]
        fake_audio = b"\xff\xfb\x90\x00" + b"\x00" * 100
        asyncio.get_event_loop().run_until_complete(
            _write_fake_file(storage, pid, "narration.mp3", fake_audio)
        )
        resp = client.get(f"/projects/{pid}/media/narration.mp3")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == fake_audio

    def test_path_traversal_dotdot_returns_400(self, client):
        created = _create_project(client)
        # ".." in the filename should be rejected
        resp = client.get(f"/projects/{created['id']}/media/..state.json")
        assert resp.status_code == 400

    def test_path_traversal_slash_returns_400(self, client):
        created = _create_project(client)
        # Backslash in the filename should be rejected
        resp = client.get(f"/projects/{created['id']}/media/sub\\file.txt")
        assert resp.status_code == 400

    def test_path_traversal_backslash_returns_400(self, client):
        created = _create_project(client)
        resp = client.get(f"/projects/{created['id']}/media/sub%5Cfile.txt")
        assert resp.status_code == 400

    def test_file_not_found_returns_404(self, client):
        created = _create_project(client)
        resp = client.get(f"/projects/{created['id']}/media/nonexistent.mp4")
        assert resp.status_code == 404

    def test_project_not_found_returns_404(self, client):
        resp = client.get("/projects/nonexistent/media/file.mp3")
        assert resp.status_code == 404

    def test_other_owner_returns_403(self, client):
        created = _create_project(client)
        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        resp = client.get(f"/projects/{created['id']}/media/file.mp3")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /projects/{id}/background
# ---------------------------------------------------------------------------


class TestUploadBackground:
    def test_upload_png_succeeds(self, client):
        created = _create_project(client)
        pid = created["id"]
        # Minimal valid PNG header (not a real image, but enough for content-type check)
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        resp = client.post(
            f"/projects/{pid}/background",
            files={"file": ("bg.png", io.BytesIO(png_data), "image/png")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "background_image" in body
        assert body["detail"] == "Background image uploaded"

    def test_upload_jpeg_succeeds(self, client):
        created = _create_project(client)
        pid = created["id"]
        jpg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        resp = client.post(
            f"/projects/{pid}/background",
            files={"file": ("bg.jpg", io.BytesIO(jpg_data), "image/jpeg")},
        )
        assert resp.status_code == 200

    def test_upload_invalid_format_returns_422(self, client):
        created = _create_project(client)
        pid = created["id"]
        resp = client.post(
            f"/projects/{pid}/background",
            files={"file": ("bg.gif", io.BytesIO(b"GIF89a"), "image/gif")},
        )
        assert resp.status_code == 422
        assert "Unsupported file format" in resp.json()["detail"]

    def test_upload_exceeds_size_returns_413(self, client, test_settings):
        created = _create_project(client)
        pid = created["id"]
        # test_settings.MAX_UPLOAD_SIZE_MB is 1, so 2 MB should exceed
        big_data = b"\x00" * (2 * 1024 * 1024)
        resp = client.post(
            f"/projects/{pid}/background",
            files={"file": ("bg.png", io.BytesIO(big_data), "image/png")},
        )
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_upload_project_not_found_returns_404(self, client):
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        resp = client.post(
            "/projects/nonexistent/background",
            files={"file": ("bg.png", io.BytesIO(png_data), "image/png")},
        )
        assert resp.status_code == 404

    def test_upload_other_owner_returns_403(self, client):
        created = _create_project(client)
        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        resp = client.post(
            f"/projects/{created['id']}/background",
            files={"file": ("bg.png", io.BytesIO(png_data), "image/png")},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /voices
# ---------------------------------------------------------------------------


class TestListVoices:
    def test_returns_voices_without_auth(self):
        """The /voices endpoint does NOT require authentication."""
        # Create a client WITHOUT auth overrides
        clean_client = TestClient(app, raise_server_exceptions=False)
        resp = clean_client.get("/voices")
        assert resp.status_code == 200
        voices = resp.json()
        assert isinstance(voices, list)
        assert len(voices) >= 3
        voice_ids = [v["id"] for v in voices]
        assert "zh-CN-XiaoxiaoNeural" in voice_ids
        assert "zh-CN-YunxiNeural" in voice_ids
        assert "zh-CN-YunjianNeural" in voice_ids

    def test_voice_structure(self, client):
        resp = client.get("/voices")
        assert resp.status_code == 200
        for voice in resp.json():
            assert "id" in voice
            assert "name" in voice
            assert "language" in voice
