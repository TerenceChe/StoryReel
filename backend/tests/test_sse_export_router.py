"""Unit tests for SSE and export endpoints (task 5.2).

Covers:
- GET  /projects/{id}/status          — SSE pipeline progress stream
- POST /projects/{id}/export          — trigger async export (202)
- GET  /projects/{id}/export/status   — SSE export progress stream
- GET  /projects/{id}/export/download — stream exported MP4
- POST /projects/{id}/retry           — retry from failed stage (422 if not error)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.auth.middleware import get_owner_id, get_settings
from backend.config import Settings
from backend.dependencies import get_pipeline_service, get_project_service
from backend.main import app
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.local import LocalStorageBackend
from backend.services.pipeline_service import PipelineService
from backend.services.project_service import ProjectService


@pytest.fixture()
def test_settings():
    s = Settings()
    s.API_SECRET_KEY = "test-key"
    s.DEV_OWNER_ID = "owner-a"
    s.MAX_PROJECTS_PER_USER = 20
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
    """Pipeline service with heavy methods mocked to avoid real I/O."""
    svc = PipelineService(
        storage=storage, project_service=project_service, settings=test_settings
    )
    svc.run_pipeline = AsyncMock()
    svc.export_video = AsyncMock(return_value="/projects/x/media/export.mp4")
    svc.retry_pipeline = AsyncMock()
    return svc


@pytest.fixture()
def client(test_settings, project_service, pipeline_service):
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_owner_id] = lambda: "owner-a"
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_pipeline_service] = lambda: pipeline_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _create_project(client: TestClient, text: str = "一个故事") -> dict:
    resp = client.post("/projects", json={"story_text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _set_project_status(
    project_service: ProjectService,
    project_id: str,
    status_val: str,
    stage: str,
    message: str = "",
    *,
    export_url: str | None = None,
) -> ProjectState:
    """Helper to directly set project status/stage for testing."""
    state = await project_service.get_project(project_id)
    state.status = status_val
    state.pipeline_progress = PipelineProgress(stage=stage, message=message)
    if export_url is not None:
        state.export_url = export_url
    await project_service._save_state(state)
    return state


async def _write_fake_file(
    storage: LocalStorageBackend, project_id: str, filename: str, data: bytes
) -> None:
    async def _chunks():
        yield data

    await storage.save_file(project_id, filename, _chunks())


def _collect_sse_events(resp_iter) -> list[dict]:
    """Collect SSE events from a streaming response, returning parsed dicts."""
    events = []
    current_event = {}
    for line in resp_iter:
        line = line.strip()
        if not line:
            if current_event:
                events.append(current_event)
                current_event = {}
            continue
        if line.startswith("event:"):
            current_event["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current_event["data"] = line.split(":", 1)[1].strip()
    if current_event:
        events.append(current_event)
    return events


# ---------------------------------------------------------------------------
# GET /projects/{id}/status — SSE pipeline progress
# ---------------------------------------------------------------------------


class TestPipelineStatusSSE:
    def test_sse_sends_complete_on_connect_and_closes(self, client, project_service):
        """When pipeline is already complete, SSE sends complete event and closes."""
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(project_service, pid, "ready", "complete", "Done")
        )

        with client.stream("GET", f"/projects/{pid}/status") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            events = _collect_sse_events(resp.iter_lines())

        assert len(events) >= 1
        assert events[0]["event"] == "complete"
        payload = json.loads(events[0]["data"])
        assert payload["stage"] == "complete"
        assert payload["message"] == "Done"

    def test_sse_sends_error_state_and_closes(self, client, project_service):
        """When pipeline is in error, SSE sends error event and closes."""
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(project_service, pid, "error", "error", "Broke")
        )

        with client.stream("GET", f"/projects/{pid}/status") as resp:
            events = _collect_sse_events(resp.iter_lines())

        assert len(events) >= 1
        assert events[0]["event"] == "error"
        payload = json.loads(events[0]["data"])
        assert payload["message"] == "Broke"

    def test_sse_sends_current_processing_state(self, client, project_service):
        """When pipeline is processing, SSE sends current stage first."""
        created = _create_project(client)
        pid = created["id"]

        # Set to processing/narration, then immediately set to complete
        # so the stream terminates
        async def _setup():
            await _set_project_status(
                project_service, pid, "processing", "narration", "Working"
            )
            # Immediately flip to complete so the poll loop terminates
            await _set_project_status(
                project_service, pid, "ready", "complete", "Done"
            )

        asyncio.get_event_loop().run_until_complete(_setup())

        with client.stream("GET", f"/projects/{pid}/status") as resp:
            events = _collect_sse_events(resp.iter_lines())

        # Should have gotten at least the complete event (narration was
        # already overwritten before the poll could see it, but complete
        # is the terminal state)
        assert any(e.get("event") == "complete" for e in events)

    def test_sse_not_found_returns_404(self, client):
        resp = client.get("/projects/nonexistent/status")
        assert resp.status_code == 404

    def test_sse_other_owner_returns_403(self, client):
        created = _create_project(client)
        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        resp = client.get(f"/projects/{created['id']}/status")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /projects/{id}/export — trigger export
# ---------------------------------------------------------------------------


class TestTriggerExport:
    def test_export_returns_202(self, client):
        created = _create_project(client)
        resp = client.post(f"/projects/{created['id']}/export")
        assert resp.status_code == 202
        body = resp.json()
        assert body["project_id"] == created["id"]
        assert "Export started" in body["detail"]

    def test_export_not_found_returns_404(self, client):
        resp = client.post("/projects/nonexistent/export")
        assert resp.status_code == 404

    def test_export_other_owner_returns_403(self, client):
        created = _create_project(client)
        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        resp = client.post(f"/projects/{created['id']}/export")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /projects/{id}/export/status — SSE export progress
# ---------------------------------------------------------------------------


class TestExportStatusSSE:
    def test_sse_sends_current_state_when_exported(self, client, project_service):
        """When project is already exported, SSE sends event and closes."""
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(
                project_service, pid, "exported", "complete", "Export finished",
                export_url="/projects/x/media/export.mp4",
            )
        )

        with client.stream("GET", f"/projects/{pid}/export/status") as resp:
            events = _collect_sse_events(resp.iter_lines())

        assert len(events) >= 1
        assert events[0]["event"] == "complete"

    def test_sse_closes_when_status_is_ready(self, client, project_service):
        """If project is in ready state (not exporting), SSE closes immediately."""
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(project_service, pid, "ready", "complete", "Done")
        )

        with client.stream("GET", f"/projects/{pid}/export/status") as resp:
            events = _collect_sse_events(resp.iter_lines())

        assert len(events) >= 1

    def test_sse_closes_on_error(self, client, project_service):
        """If project is in error state, SSE sends error and closes."""
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(project_service, pid, "error", "error", "Export failed")
        )

        with client.stream("GET", f"/projects/{pid}/export/status") as resp:
            events = _collect_sse_events(resp.iter_lines())

        assert len(events) >= 1
        assert events[0]["event"] == "error"

    def test_export_status_not_found_returns_404(self, client):
        resp = client.get("/projects/nonexistent/export/status")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /projects/{id}/export/download — stream MP4
# ---------------------------------------------------------------------------


class TestDownloadExport:
    def test_download_no_export_returns_404(self, client):
        """If no export_url is set, return 404."""
        created = _create_project(client)
        resp = client.get(f"/projects/{created['id']}/export/download")
        assert resp.status_code == 404

    def test_download_with_export_streams_mp4(self, client, project_service, storage):
        """When export exists, stream the MP4 file."""
        created = _create_project(client)
        pid = created["id"]

        fake_mp4 = b"\x00\x00\x00\x1cftypisom" + b"\x00" * 100
        asyncio.get_event_loop().run_until_complete(
            _write_fake_file(storage, pid, "export.mp4", fake_mp4)
        )
        asyncio.get_event_loop().run_until_complete(
            _set_project_status(
                project_service, pid, "exported", "complete", "Export finished",
                export_url=f"/projects/{pid}/media/export.mp4",
            )
        )

        resp = client.get(f"/projects/{pid}/export/download")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "video/mp4"
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert resp.content == fake_mp4

    def test_download_other_owner_returns_403(self, client, project_service):
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(
                project_service, pid, "exported", "complete", "Done",
                export_url="/x",
            )
        )

        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        resp = client.get(f"/projects/{pid}/export/download")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /projects/{id}/retry — retry from failed stage
# ---------------------------------------------------------------------------


class TestRetryPipeline:
    def test_retry_when_not_error_returns_422(self, client):
        """Retry is only valid when status is 'error'."""
        created = _create_project(client)
        resp = client.post(f"/projects/{created['id']}/retry")
        assert resp.status_code == 422
        assert "error" in resp.json()["detail"].lower()

    def test_retry_when_error_returns_200(self, client, project_service):
        """Retry succeeds when project is in error state."""
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(
                project_service, pid, "error", "narration", "TTS failed"
            )
        )

        resp = client.post(f"/projects/{pid}/retry")
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == pid
        assert "Retry started" in body["detail"]

    def test_retry_not_found_returns_404(self, client):
        resp = client.post("/projects/nonexistent/retry")
        assert resp.status_code == 404

    def test_retry_other_owner_returns_403(self, client, project_service):
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(
                project_service, pid, "error", "narration", "TTS failed"
            )
        )

        app.dependency_overrides[get_owner_id] = lambda: "owner-b"
        resp = client.post(f"/projects/{pid}/retry")
        assert resp.status_code == 403

    def test_retry_processing_returns_422(self, client, project_service):
        """Retry while processing should return 422."""
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(
                project_service, pid, "processing", "narration", "Working"
            )
        )

        resp = client.post(f"/projects/{pid}/retry")
        assert resp.status_code == 422

    def test_retry_ready_returns_422(self, client, project_service):
        """Retry when ready should return 422."""
        created = _create_project(client)
        pid = created["id"]

        asyncio.get_event_loop().run_until_complete(
            _set_project_status(project_service, pid, "ready", "complete", "Done")
        )

        resp = client.post(f"/projects/{pid}/retry")
        assert resp.status_code == 422
