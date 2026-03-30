"""Unit tests for API error responses (task 5.4).

Covers error scenarios NOT already tested in test_projects_router.py,
test_sse_export_router.py, test_media_voices_router.py, or test_auth.py:

- Invalid subtitle timing on PUT → 422 (TimingValidationError at router level)
- 401 on actual project endpoints (real auth middleware, not overridden)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.auth.middleware import get_owner_id, get_settings as get_settings_auth
from backend.config import Settings
from backend.dependencies import (
    get_pipeline_service,
    get_project_service,
    get_settings as get_settings_dep,
)
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
    s.MAX_UPLOAD_SIZE_MB = 1
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
    """Client with auth overridden (for non-auth tests)."""
    app.dependency_overrides[get_settings_auth] = lambda: test_settings
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[get_owner_id] = lambda: "owner-a"
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_pipeline_service] = lambda: pipeline_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture()
def auth_client(test_settings, project_service, pipeline_service, storage):
    """Client with real auth middleware (get_owner_id NOT overridden)."""
    app.dependency_overrides[get_settings_auth] = lambda: test_settings
    app.dependency_overrides[get_settings_dep] = lambda: test_settings
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_pipeline_service] = lambda: pipeline_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _create_project(client: TestClient, text: str = "一个故事") -> dict:
    resp = client.post("/projects", json={"story_text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Invalid subtitle timing on PUT → 422
# ---------------------------------------------------------------------------


class TestInvalidSubtitleTiming:
    """PUT /projects/{id} with start_time >= end_time should return 422."""

    def test_start_equal_to_end_returns_422(self, client):
        created = _create_project(client)
        state = client.get(f"/projects/{created['id']}").json()
        state["subtitles"] = [
            {
                "id": "sub-1",
                "text": "你好",
                "start_time": 1.0,
                "end_time": 1.0,  # equal — invalid
                "position": {"x": 0.5, "y": 0.85},
                "style": {},
            }
        ]
        resp = client.put(f"/projects/{created['id']}", json=state)
        assert resp.status_code == 422

    def test_start_greater_than_end_returns_422(self, client):
        created = _create_project(client)
        state = client.get(f"/projects/{created['id']}").json()
        state["subtitles"] = [
            {
                "id": "sub-1",
                "text": "你好",
                "start_time": 5.0,
                "end_time": 2.0,  # start > end — invalid
                "position": {"x": 0.5, "y": 0.85},
                "style": {},
            }
        ]
        resp = client.put(f"/projects/{created['id']}", json=state)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 401 on actual project endpoints (real auth middleware)
# ---------------------------------------------------------------------------


class TestAuth401OnEndpoints:
    """Verify that actual project endpoints return 401 when no valid token
    is provided. Uses auth_client which does NOT override get_owner_id."""

    def test_create_project_no_token_returns_401(self, auth_client):
        resp = auth_client.post("/projects", json={"story_text": "一个故事"})
        assert resp.status_code == 401

    def test_create_project_invalid_token_returns_401(self, auth_client):
        resp = auth_client.post(
            "/projects",
            json={"story_text": "一个故事"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_list_projects_no_token_returns_401(self, auth_client):
        resp = auth_client.get("/projects")
        assert resp.status_code == 401

    def test_get_project_no_token_returns_401(self, auth_client):
        resp = auth_client.get("/projects/some-id")
        assert resp.status_code == 401

    def test_delete_project_no_token_returns_401(self, auth_client):
        resp = auth_client.delete("/projects/some-id")
        assert resp.status_code == 401
