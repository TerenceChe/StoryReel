"""Property-based test for image-job submit shape.

Feature: ai-background-generation, Property 9: Job submission shape
Validates: Requirement 5.1

Hypothesis generates valid request bodies for ``POST
/projects/{project_id}/image-jobs``. For every accepted body the test
asserts three things:

1. The HTTP response is 202 (Requirement 5.1: "WHEN the Owner submits a
   Generation_Job, THE Editor SHALL return a 202 response containing a
   ``job_id`` identifying the job").
2. The response body contains a non-empty ``job_id`` string.
3. That ``job_id`` resolves via ``JobManager.get(...)`` with
   ``owner_id`` and ``project_id`` matching the submitter and the
   project the job was submitted against.

The status (``GET .../{job_id}``) HTTP route is not yet implemented
(Task 11.1). Per the task instructions this test isolates Property 9 to
the **submit** endpoint by driving submit through HTTP and reading the
job back via ``JobManager.get(...)`` directly. Coupling to the status
route is intentionally avoided so this property's correctness does not
depend on a route that does not exist yet.

The test mounts only the image-jobs router on a freshly-built FastAPI
app. ``get_image_backend``, ``get_project_service``, ``get_job_manager``,
``get_owner_id``, and ``get_settings`` are all overridden via
``app.dependency_overrides`` so the test runs against the
:class:`FakeImageBackend` and a stub ``ProjectService`` that returns a
deterministic, owner-matched :class:`ProjectState`.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from hypothesis import given, settings as hyp_settings, strategies as st

from backend.auth.middleware import get_owner_id, get_settings
from backend.config import Settings
from backend.dependencies import (
    get_image_backend,
    get_job_manager,
    get_project_service,
)
from backend.models.project import PipelineProgress, ProjectState
from backend.models.subtitle import Position, SubtitleSegment, SubtitleStyle
from backend.persistence.base import StorageBackend
from backend.routers.image_jobs import router as image_jobs_router
from backend.services.image_capability_state import capability_state
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectNotFoundError


# Constants kept module-level so the strategy and the test body share the
# same values without going through fixture scope. ``_MAX_IMAGES`` matches
# the default ``MAX_IMAGES_PER_JOB`` from ``backend/config.py`` so the
# strategy never proposes an image_count the JobManager would reject.
_OWNER_ID = "owner-submit-shape-test"
_PROJECT_ID = "project-submit-shape-test"
_MAX_IMAGES = 4


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _InMemoryStorage(StorageBackend):
    """Minimal in-memory ``StorageBackend`` for the JobManager worker.

    The worker spawned by :meth:`JobManager.submit` calls ``save_file`` /
    ``get_file_url`` while persisting candidate bytes after the
    :class:`FakeImageBackend` produces them. The submit-shape property
    only inspects the *initial* response and the round-tripped job, so
    in practice the worker may or may not have reached the persist step
    by the time the test cancels it. The storage is implemented in full
    so any worker code path the test happens to exercise has somewhere
    to land its bytes without raising ``NotImplementedError``.
    """

    def __init__(self) -> None:
        self._files: dict[tuple[str, str], bytes] = {}

    async def save_file(
        self, project_id: str, filename: str, data: AsyncIterator[bytes]
    ) -> str:
        chunks: list[bytes] = []
        async for chunk in data:
            chunks.append(chunk)
        self._files[(project_id, filename)] = b"".join(chunks)
        return f"/projects/{project_id}/media/{filename}"

    async def load_file(
        self, project_id: str, filename: str
    ) -> AsyncIterator[bytes]:
        try:
            payload = self._files[(project_id, filename)]
        except KeyError as exc:
            raise FileNotFoundError(f"{project_id}/{filename}") from exc

        async def _gen() -> AsyncIterator[bytes]:
            yield payload

        return _gen()

    async def get_file_url(self, project_id: str, filename: str) -> str:
        return f"/projects/{project_id}/media/{filename}"

    async def delete_project(self, project_id: str) -> None:
        self._files = {
            k: v for k, v in self._files.items() if k[0] != project_id
        }


class _StubProjectService:
    """Tiny project-service stub that returns a single canned project.

    The image-jobs router calls ``project_service.get_project(project_id)``
    via the shared ``_load_owned_project`` helper. A full
    :class:`backend.services.project_service.ProjectService` would require
    on-disk state and project creation per example; this stub gives the
    router exactly the shape it needs without that overhead and keeps the
    test focused on the submit endpoint's response and round-trip
    semantics.
    """

    def __init__(self, project: ProjectState) -> None:
        self._project = project

    async def get_project(self, project_id: str) -> ProjectState:
        if project_id != self._project.id:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return self._project


def _make_subtitle(i: int) -> SubtitleSegment:
    """Build a placeholder subtitle so the project has a non-empty list.

    Each segment occupies a half-second window starting at ``i`` so
    consecutive segments never overlap (the
    :meth:`SubtitleSegment.validate_timing` model_validator only checks
    ``start_time < end_time``; non-overlapping is bonus realism).
    """
    return SubtitleSegment(
        id=f"seg-{i}",
        text=f"Line {i}",
        start_time=float(i),
        end_time=float(i) + 0.5,
        position=Position(x=0.5, y=0.85),
        style=SubtitleStyle(),
    )


def _make_project(subtitles_len: int) -> ProjectState:
    """Construct a deterministic :class:`ProjectState` for the stub service.

    ``owner_id`` is the same constant the test overrides ``get_owner_id``
    to return so :func:`verify_project_ownership` always passes — the
    submit-shape property is about successful submissions, so 403 paths
    are out of scope here.
    """
    return ProjectState(
        id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        title="Submit Shape Test",
        story_text="A short story for submit-shape testing.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        subtitles=[_make_subtitle(i) for i in range(subtitles_len)],
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Hypothesis strategy
# ---------------------------------------------------------------------------


@st.composite
def _valid_request_body(draw) -> tuple[dict, int]:
    """Draw a valid submit body together with the matching subtitles_len.

    The strategy returns a tuple so the test body can build the project
    state with exactly the number of subtitles the drawn ``target``
    requires. For ``whole_video`` the count is irrelevant — a random
    value in ``[0, 8]`` is used so the test exercises both the
    no-subtitles and the with-subtitles project shapes against the
    whole-video target. For ``section`` the count is at least 1 (so
    indices can fit) and the indices are constrained to
    ``0 <= start <= end < subtitles_len`` (Requirement 3.4).
    """
    prompt = draw(st.text(max_size=200))
    image_count = draw(st.integers(min_value=1, max_value=_MAX_IMAGES))
    kind = draw(st.sampled_from(["whole_video", "section"]))

    if kind == "whole_video":
        target: dict = {"kind": "whole_video"}
        subtitles_len = draw(st.integers(min_value=0, max_value=8))
    else:
        subtitles_len = draw(st.integers(min_value=1, max_value=8))
        start_index = draw(
            st.integers(min_value=0, max_value=subtitles_len - 1)
        )
        end_index = draw(
            st.integers(min_value=start_index, max_value=subtitles_len - 1)
        )
        target = {
            "kind": "section",
            "start_index": start_index,
            "end_index": end_index,
        }

    body = {
        "prompt": prompt,
        "image_count": image_count,
        "target": target,
    }
    return body, subtitles_len


# ---------------------------------------------------------------------------
# Property 9: Job submission shape
# ---------------------------------------------------------------------------
# Feature: ai-background-generation, Property 9: Job submission shape
# Validates: Requirement 5.1


@given(body_and_len=_valid_request_body())
@hyp_settings(max_examples=100, deadline=None)
def test_submit_shape_returns_202_with_resolvable_job_id(
    body_and_len: tuple[dict, int],
) -> None:
    """Feature: ai-background-generation, Property 9: Job submission shape

    Validates: Requirement 5.1

    For any valid submit request body, the response is 202 with a
    non-empty ``job_id``, and that ``job_id`` resolves via
    ``JobManager.get(...)`` with matching ``owner_id`` and
    ``project_id``.
    """
    body, subtitles_len = body_and_len

    async def _run() -> None:
        # Reset the process-level capability flag so any earlier test in
        # the same process (or a previous Hypothesis example here) that
        # observed a ``ProviderAuthenticationError`` cannot leave the
        # capability disabled and turn this example's submit into a 503.
        capability_state.reset()

        # Per-example app + manager. Building these fresh inside each
        # example keeps the JobManager registry clean (no jobs / running
        # slots leftover from earlier draws) without manual reset hooks
        # on the shared singleton in ``backend/dependencies.py``.
        app = FastAPI()
        app.include_router(image_jobs_router)

        test_settings = Settings()
        # Bypass auth — the test owns the ``get_owner_id`` override so
        # the auth middleware never runs. ``DISABLE_AUTH`` is also flipped
        # for defense in depth in case a future refactor wires the auth
        # middleware ahead of the dependency override.
        test_settings.DISABLE_AUTH = True
        test_settings.LOCAL_OWNER_ID = _OWNER_ID
        test_settings.MAX_IMAGES_PER_JOB = _MAX_IMAGES
        # Large concurrency cap so 100 examples can submit back-to-back
        # without tripping the per-user limit. The cap-honoring property
        # is covered by Property 7; this property is solely about the
        # submit response shape.
        test_settings.MAX_CONCURRENT_IMAGE_JOBS_PER_USER = 1000

        project = _make_project(subtitles_len)
        storage = _InMemoryStorage()
        fake_backend = FakeImageBackend()
        stub_project_service = _StubProjectService(project)
        manager = JobManager(
            storage=storage,
            project_service=stub_project_service,  # type: ignore[arg-type]
            settings=test_settings,
            backend=fake_backend,
        )

        app.dependency_overrides[get_owner_id] = lambda: _OWNER_ID
        app.dependency_overrides[get_settings] = lambda: test_settings
        app.dependency_overrides[get_image_backend] = lambda: fake_backend
        app.dependency_overrides[get_project_service] = (
            lambda: stub_project_service
        )
        app.dependency_overrides[get_job_manager] = lambda: manager

        try:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/projects/{project.id}/image-jobs",
                    json=body,
                )

                # Assertion 1: 202 status code (Requirement 5.1).
                assert resp.status_code == 202, (
                    f"Expected 202 for valid submit body {body!r}; got "
                    f"{resp.status_code}: {resp.text!r}"
                )

                payload = resp.json()

                # Assertion 2: non-empty ``job_id`` string.
                assert "job_id" in payload, (
                    f"Submit response missing 'job_id' field: {payload!r}"
                )
                job_id = payload["job_id"]
                assert isinstance(job_id, str) and len(job_id) > 0, (
                    f"Expected non-empty job_id string; got {job_id!r}"
                )

                # Assertion 3: round-trip via JobManager.get matches.
                # Per the task instructions this test reads the job
                # directly from the manager rather than the not-yet-
                # implemented status route, so Property 9 stays
                # decoupled from Task 11.1's progress.
                job = await manager.get(_OWNER_ID, job_id)
                assert job.owner_id == _OWNER_ID, (
                    f"Round-tripped job owner_id {job.owner_id!r} does "
                    f"not match submitter {_OWNER_ID!r}"
                )
                assert job.project_id == project.id, (
                    f"Round-tripped job project_id {job.project_id!r} "
                    f"does not match submission project {project.id!r}"
                )
        finally:
            # Cancel any worker task spawned by submit() so it does not
            # outlive this example's event loop. ``asyncio.run`` would
            # otherwise warn about pending tasks at loop close, and a
            # leaking task could touch the next example's storage /
            # manager state in surprising ways.
            tasks = list(manager._tasks.values())
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in tasks:
                try:
                    await task
                except BaseException:  # noqa: BLE001 — best-effort drain
                    pass

    try:
        asyncio.run(_run())
    finally:
        # ``asyncio.run`` closes the event loop it created and leaves
        # the thread with no current event loop attached. Several other
        # tests in this suite still call ``asyncio.get_event_loop()``
        # (the pre-3.12 pattern) and rely on a usable loop being
        # installed on the main thread. Install a fresh, unstarted loop
        # so those tests don't pick up a ``RuntimeError: There is no
        # current event loop in thread 'MainThread'.`` from this
        # property test as a side effect.
        asyncio.set_event_loop(asyncio.new_event_loop())


# Imported here (rather than at module top) so a Hypothesis-level import
# cycle in any future restructuring of the fakes module surfaces as a
# clear ImportError at test-collection time, not as a confusing
# `_image_fakes is None` failure inside the strategy. The fake is the
# direct collaborator of this test and importing it last keeps the
# top-of-file imports focused on framework / SUT modules.
from backend.tests._image_fakes import FakeImageBackend  # noqa: E402
