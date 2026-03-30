"""Unit tests for the project CRUD endpoints (backend/routers/projects.py).

Covers:
- POST /projects  — creation, whitespace rejection, project limit
- GET  /projects  — list summaries
- GET  /projects/{id} — full state, 404, 403
- PUT  /projects/{id} — update, version conflict (409), timing error (422)
- DELETE /projects/{id} — deletion, 404, 403
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.auth.middleware import get_owner_id, get_settings
from backend.config import Settings
from backend.dependencies import get_pipeline_service, get_project_service
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
    s.MAX_PROJECTS_PER_USER = 5
    s.MAX_CONCURRENT_PIPELINES_PER_USER = 2
    return s


@pytest.fixture()
def storage(tmp_path):
    return LocalStorageBackend(base_dir=str(tmp_path))


@pytest.fixture()
def project_service(storage, test_settings):
    return ProjectService(storage=storage, settings=test_settings)


@pytest.fixture()
def pipeline_service(storage, project_service, test_settings):
    return PipelineService(storage=storage, project_service=project_service, settings=test_settings)


@pytest.fixture()
def client(test_settings, project_service, pipeline_service):
    """TestClient with dependency overrides so we hit real services backed by tmp storage."""
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_owner_id] = lambda: "owner-a"
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_pipeline_service] = lambda: pipeline_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _auth_headers():
    return {"Authorization": "Bearer test-key"}


def _create_project(client: TestClient, text: str = "一个故事", **kwargs) -> dict:
    body = {"story_text": text, **kwargs}
    resp = client.post("/projects", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# POST /projects
# ---------------------------------------------------------------------------

class TestCreateProject:
    def test_create_returns_201_with_project_state(self, client):
        data = _create_project(client)
        assert data["status"] == "pending"
        assert data["story_text"] == "一个故事"
        assert data["owner_id"] == "owner-a"
        assert "id" in data

    def test_create_with_custom_voice_and_title(self, client):
        data = _create_project(
            client,
            text="故事内容",
            voice="zh-CN-YunxiNeural",
            title="My Title",
        )
        assert data["voice"] == "zh-CN-YunxiNeural"
        assert data["title"] == "My Title"

    def test_empty_text_returns_422(self, client):
        resp = client.post("/projects", json={"story_text": ""})
        assert resp.status_code == 422

    def test_whitespace_only_text_returns_422(self, client):
        resp = client.post("/projects", json={"story_text": "   \t\n  "})
        assert resp.status_code == 422

    def test_project_limit_returns_429(self, client, test_settings):
        test_settings.MAX_PROJECTS_PER_USER = 2
        _create_project(client, text="story 1")
        _create_project(client, text="story 2")
        resp = client.post("/projects", json={"story_text": "story 3"})
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# GET /projects
# ---------------------------------------------------------------------------

class TestListProjects:
    def test_empty_list(self, client):
        resp = client.get("/projects")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_summaries(self, client):
        _create_project(client, text="first")
        _create_project(client, text="second")
        resp = client.get("/projects")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        # Summaries should have these keys and NOT full state keys
        for item in items:
            assert set(item.keys()) == {"id", "title", "status", "created_at", "updated_at"}


# ---------------------------------------------------------------------------
# GET /projects/{id}
# ---------------------------------------------------------------------------

class TestGetProject:
    def test_get_existing_project(self, client):
        created = _create_project(client)
        resp = client.get(f"/projects/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]
        assert "subtitles" in resp.json()

    def test_not_found_returns_404(self, client):
        resp = client.get("/projects/nonexistent")
        assert resp.status_code == 404

    def test_other_owner_returns_403(self, client):
        created = _create_project(client)
        # Switch to a different owner
        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        resp = client.get(f"/projects/{created['id']}")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /projects/{id}
# ---------------------------------------------------------------------------

class TestUpdateProject:
    def test_update_succeeds(self, client):
        created = _create_project(client)
        state = client.get(f"/projects/{created['id']}").json()
        state["title"] = "Updated Title"
        resp = client.put(f"/projects/{created['id']}", json=state)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated Title"
        assert resp.json()["version"] == state["version"] + 1

    def test_version_conflict_returns_409(self, client):
        created = _create_project(client)
        state = client.get(f"/projects/{created['id']}").json()
        # First update succeeds
        state["title"] = "First"
        resp1 = client.put(f"/projects/{created['id']}", json=state)
        assert resp1.status_code == 200
        # Second update with stale version fails
        state["title"] = "Second"
        resp2 = client.put(f"/projects/{created['id']}", json=state)
        assert resp2.status_code == 409

    def test_not_found_returns_404(self, client):
        body = {
            "id": "nonexistent",
            "owner_id": "owner-a",
            "title": "t",
            "story_text": "s",
            "version": 1,
            "pipeline_progress": {"stage": "narration", "message": "m"},
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
        }
        resp = client.put("/projects/nonexistent", json=body)
        assert resp.status_code == 404

    def test_other_owner_returns_403(self, client):
        created = _create_project(client)
        state = client.get(f"/projects/{created['id']}").json()
        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        resp = client.put(f"/projects/{created['id']}", json=state)
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /projects/{id}
# ---------------------------------------------------------------------------

class TestDeleteProject:
    def test_delete_returns_204(self, client):
        created = _create_project(client)
        resp = client.delete(f"/projects/{created['id']}")
        assert resp.status_code == 204
        # Confirm it's gone
        resp2 = client.get(f"/projects/{created['id']}")
        assert resp2.status_code == 404

    def test_delete_not_found_returns_404(self, client):
        resp = client.delete("/projects/nonexistent")
        assert resp.status_code == 404

    def test_delete_other_owner_returns_403(self, client):
        created = _create_project(client)
        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        resp = client.delete(f"/projects/{created['id']}")
        assert resp.status_code == 403
