"""Router-level tests for project-title validation (tasks 4.5, 4.6).

Covers the HTTP surface contracts defined in
``.kiro/specs/project-titles/design.md``:

- POST /projects with no title → 422 ``title_required`` (Example E1).
- POST /projects with each ``TitleErrorCode`` shape failure → 422 with the
  matching ``error_code``; duplicate → 409.
- PATCH /projects/{id}/title with each ``TitleErrorCode`` shape failure →
  422 with the matching ``error_code``; duplicate → 409.

The error body shape is ``{"detail": {"error_code", "field", "message"}}``.
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
from backend.services.title_validator import MAX_TITLE_LENGTH


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_settings():
    s = Settings()
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
    from unittest.mock import AsyncMock

    svc = PipelineService(
        storage=storage, project_service=project_service, settings=test_settings
    )
    svc.run_pipeline = AsyncMock()
    return svc


@pytest.fixture()
def client(test_settings, project_service, pipeline_service):
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_owner_id] = lambda: "owner-a"
    app.dependency_overrides[get_project_service] = lambda: project_service
    app.dependency_overrides[get_pipeline_service] = lambda: pipeline_service
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _create(client: TestClient, *, story_text: str, title: str) -> dict:
    resp = client.post(
        "/projects", json={"story_text": story_text, "title": title}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _assert_title_error(
    resp, *, expected_status: int, expected_code: str
) -> None:
    """Assert the structured title-error body shape."""
    assert resp.status_code == expected_status, resp.text
    body = resp.json()
    assert "detail" in body, body
    detail = body["detail"]
    assert isinstance(detail, dict), detail
    assert detail.get("field") == "title", detail
    assert detail.get("error_code") == expected_code, detail
    assert "message" in detail and isinstance(detail["message"], str), detail


# ---------------------------------------------------------------------------
# Example E1 (Task 4.5): missing title → 422 title_required
# ---------------------------------------------------------------------------


class TestMissingTitleOnCreate:
    """Validates Requirement 1.2 — POST /projects without ``title`` returns 422."""

    def test_missing_title_returns_422_title_required(self, client):
        resp = client.post("/projects", json={"story_text": "故事"})
        _assert_title_error(
            resp, expected_status=422, expected_code="title_required"
        )


# ---------------------------------------------------------------------------
# Task 4.6: router error mapping per TitleErrorCode on POST /projects
# ---------------------------------------------------------------------------


class TestCreateTitleErrorMapping:
    """One test per ``TitleErrorCode`` on the create endpoint."""

    def test_empty_title_returns_422_title_empty(self, client):
        resp = client.post(
            "/projects", json={"story_text": "故事", "title": "   "}
        )
        _assert_title_error(
            resp, expected_status=422, expected_code="title_empty"
        )

    def test_too_long_title_returns_422_title_too_long(self, client):
        # 101 ASCII chars after trim
        long_title = "a" * (MAX_TITLE_LENGTH + 1)
        resp = client.post(
            "/projects", json={"story_text": "故事", "title": long_title}
        )
        _assert_title_error(
            resp, expected_status=422, expected_code="title_too_long"
        )

    def test_control_chars_title_returns_422_title_control_chars(self, client):
        # Embed a NUL (Cc category) in an otherwise valid title.
        resp = client.post(
            "/projects",
            json={"story_text": "故事", "title": "good\x00title"},
        )
        _assert_title_error(
            resp, expected_status=422, expected_code="title_control_chars"
        )

    def test_duplicate_title_returns_409_title_duplicate(self, client):
        _create(client, story_text="first", title="My Title")
        # Vary case + whitespace to confirm trim+casefold matching.
        resp = client.post(
            "/projects",
            json={"story_text": "second", "title": "  my TITLE  "},
        )
        _assert_title_error(
            resp, expected_status=409, expected_code="title_duplicate"
        )


# ---------------------------------------------------------------------------
# Task 4.6: router error mapping per TitleErrorCode on PATCH /title
# ---------------------------------------------------------------------------


class TestRenameTitleErrorMapping:
    """One test per ``TitleErrorCode`` on the rename endpoint."""

    def _seed(self, client: TestClient) -> dict:
        return _create(client, story_text="故事", title="Original")

    def test_empty_title_returns_422_title_empty(self, client):
        proj = self._seed(client)
        resp = client.patch(
            f"/projects/{proj['id']}/title",
            json={"title": "   ", "version": proj["version"]},
        )
        _assert_title_error(
            resp, expected_status=422, expected_code="title_empty"
        )

    def test_too_long_title_returns_422_title_too_long(self, client):
        proj = self._seed(client)
        long_title = "x" * (MAX_TITLE_LENGTH + 1)
        resp = client.patch(
            f"/projects/{proj['id']}/title",
            json={"title": long_title, "version": proj["version"]},
        )
        _assert_title_error(
            resp, expected_status=422, expected_code="title_too_long"
        )

    def test_control_chars_title_returns_422_title_control_chars(self, client):
        proj = self._seed(client)
        resp = client.patch(
            f"/projects/{proj['id']}/title",
            json={"title": "bad\x01title", "version": proj["version"]},
        )
        _assert_title_error(
            resp, expected_status=422, expected_code="title_control_chars"
        )

    def test_duplicate_title_returns_409_title_duplicate(self, client):
        # Two projects under the same owner; rename second to match first.
        first = _create(client, story_text="一个故事", title="Alpha")
        second = _create(client, story_text="另一个故事", title="Beta")
        resp = client.patch(
            f"/projects/{second['id']}/title",
            json={"title": "alpha", "version": second["version"]},
        )
        _assert_title_error(
            resp, expected_status=409, expected_code="title_duplicate"
        )
        # Sanity: the first project is unmodified.
        assert first["title"] == "Alpha"


# ---------------------------------------------------------------------------
# Task 4.4: PUT /projects/{id} routes title errors through the global handler
# ---------------------------------------------------------------------------


class TestPutTitleErrorMapping:
    """``PUT /projects/{id}`` lets ``TitleValidationError`` propagate to the
    global exception handler so title problems use the same structured shape.
    """

    def test_put_with_too_long_title_returns_422_title_too_long(self, client):
        proj = _create(client, story_text="故事", title="Original")
        state = client.get(f"/projects/{proj['id']}").json()
        state["title"] = "z" * (MAX_TITLE_LENGTH + 1)
        resp = client.put(f"/projects/{proj['id']}", json=state)
        _assert_title_error(
            resp, expected_status=422, expected_code="title_too_long"
        )

    def test_put_with_duplicate_title_returns_409_title_duplicate(self, client):
        _create(client, story_text="一个", title="Taken")
        proj = _create(client, story_text="另一个", title="Free")
        state = client.get(f"/projects/{proj['id']}").json()
        state["title"] = "TAKEN"
        resp = client.put(f"/projects/{proj['id']}", json=state)
        _assert_title_error(
            resp, expected_status=409, expected_code="title_duplicate"
        )
