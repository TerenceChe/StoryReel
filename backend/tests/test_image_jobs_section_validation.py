"""Property-based test for section target index validation.

Feature: ai-background-generation, Property 3: Section target index validation
Validates: Requirements 3.2, 3.3, 3.4

Hypothesis generates ``(subtitles_len, start_index, end_index)`` triples
covering both valid and invalid ranges (including negatives and
out-of-range values). For every triple the test asserts that the
``POST /projects/{project_id}/image-jobs`` route accepts the request
(202) **iff**::

    subtitles_len > 0 AND 0 <= start_index <= end_index < subtitles_len

Otherwise the route rejects with 422. Two layers can produce the 422
response and both satisfy this property:

* Pydantic body validation (e.g. wrong field types) — but here all
  three indices are integers and the schema accepts negative or
  out-of-range integers, so Pydantic does not short-circuit on these
  values; the JobManager service-level check is what converts them to
  ``ImageJobValidationError``.
* :class:`backend.services.image_job_errors.ImageJobValidationError`
  raised by ``JobManager._validate_target``, mapped to 422 by the
  router's shared exception mapper.

The test mounts only the image-jobs router on a freshly-built FastAPI
app per Hypothesis example (mirroring ``test_image_jobs_submit_shape``)
so the JobManager registry never carries leftovers between draws and
the bound ``MAX_CONCURRENT_IMAGE_JOBS_PER_USER`` cap is large enough to
absorb a back-to-back burst of accepted submissions.
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
from backend.tests._image_fakes import FakeImageBackend


_OWNER_ID = "owner-section-validation-test"
_PROJECT_ID = "project-section-validation-test"
# Same default value as ``MAX_IMAGES_PER_JOB`` in ``backend/config.py``.
# The strategy emits ``image_count = 1`` always so this constant matters
# only for the manager's range check; setting it explicitly makes the
# test independent of any future change to the shipped default.
_MAX_IMAGES = 4


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _InMemoryStorage(StorageBackend):
    """Minimal in-memory ``StorageBackend`` for the JobManager worker.

    The worker spawned by accepted submissions writes candidate bytes
    via ``save_file``. Rejection cases never reach the worker, but the
    accepted cases do, so the storage needs working ``save_file`` /
    ``get_file_url`` methods. Bytes are dropped on shutdown along with
    the per-example app.
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
    """Stub project-service that returns a single canned project.

    The router calls ``project_service.get_project(project_id)`` via
    ``_load_owned_project``. The stub keeps the test focused on section
    index validation rather than wiring a full ProjectService.
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
    ``SubtitleSegment.validate_timing`` (``start_time < end_time``)
    holds for every index ``i >= 0``.
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
    """Construct a deterministic :class:`ProjectState` with N subtitles."""
    return ProjectState(
        id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        title="Section Validation Test",
        story_text="A short story for section-validation testing.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        subtitles=[_make_subtitle(i) for i in range(subtitles_len)],
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Hypothesis strategy
# ---------------------------------------------------------------------------


@st.composite
def _section_triple(draw) -> tuple[int, int, int]:
    """Draw ``(subtitles_len, start_index, end_index)`` for a section job.

    The strategy intentionally covers values outside the valid range so
    rejection paths are exercised:

    * ``subtitles_len`` ∈ [0, 8] — includes the no-subtitles case
      (Requirement 3.3).
    * ``start_index`` and ``end_index`` ∈ [-3, 10] — includes negative
      values and indices ≥ ``subtitles_len``.

    With a ``subtitles_len`` cap of 8 and an index range of [-3, 10],
    roughly 30% of triples are valid and the rest exercise one or more
    rejection branches in ``JobManager._validate_target`` (Requirement
    3.2, 3.3, 3.4).
    """
    subtitles_len = draw(st.integers(min_value=0, max_value=8))
    start_index = draw(st.integers(min_value=-3, max_value=10))
    end_index = draw(st.integers(min_value=-3, max_value=10))
    return subtitles_len, start_index, end_index


# ---------------------------------------------------------------------------
# Property 3: Section target index validation
# ---------------------------------------------------------------------------
# Feature: ai-background-generation, Property 3: Section target index validation
# Validates: Requirements 3.2, 3.3, 3.4


@given(triple=_section_triple())
@hyp_settings(max_examples=100, deadline=None)
def test_section_target_indices_validation(
    triple: tuple[int, int, int],
) -> None:
    """Feature: ai-background-generation, Property 3: Section target index validation

    Validates: Requirements 3.2, 3.3, 3.4

    For any ``(subtitles_len, start_index, end_index)`` triple submitted
    as a ``section`` Generation_Target, the job is accepted (202) **iff**
    ``subtitles_len > 0 AND 0 <= start_index <= end_index < subtitles_len``;
    otherwise the response is 422.
    """
    subtitles_len, start_index, end_index = triple

    expected_accept = (
        subtitles_len > 0
        and 0 <= start_index
        and start_index <= end_index
        and end_index < subtitles_len
    )

    async def _run() -> None:
        # Reset the process-level capability flag in case any previous
        # test in this process flipped it off via a simulated provider
        # auth failure. Without this reset, the submit handler would
        # short-circuit to 503 and our 202/422 distinction would
        # collapse into a single false-negative for every example.
        capability_state.reset()

        # Per-example app + manager so the JobManager registry stays
        # clean across draws (no leftover jobs / running slots).
        app = FastAPI()
        app.include_router(image_jobs_router)

        test_settings = Settings()
        # Bypass auth — the test owns the ``get_owner_id`` override so
        # the auth middleware never runs. ``DISABLE_AUTH`` is also
        # flipped for defense in depth.
        test_settings.DISABLE_AUTH = True
        test_settings.LOCAL_OWNER_ID = _OWNER_ID
        test_settings.MAX_IMAGES_PER_JOB = _MAX_IMAGES
        # Large concurrency cap so 100 examples can submit back-to-back
        # without tripping the per-user limit. The cap-honoring property
        # is covered by Property 7 elsewhere.
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

        body = {
            "prompt": "section validation test",
            "image_count": 1,
            "target": {
                "kind": "section",
                "start_index": start_index,
                "end_index": end_index,
            },
        }

        try:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/projects/{project.id}/image-jobs",
                    json=body,
                )

            if expected_accept:
                assert resp.status_code == 202, (
                    f"Expected 202 for valid section target "
                    f"(subtitles_len={subtitles_len}, "
                    f"start_index={start_index}, "
                    f"end_index={end_index}); got "
                    f"{resp.status_code}: {resp.text!r}"
                )
            else:
                assert resp.status_code == 422, (
                    f"Expected 422 for invalid section target "
                    f"(subtitles_len={subtitles_len}, "
                    f"start_index={start_index}, "
                    f"end_index={end_index}); got "
                    f"{resp.status_code}: {resp.text!r}"
                )
        finally:
            # Cancel any worker task spawned by an accepted submit so it
            # does not outlive the per-example event loop.
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
        # the thread without a current event loop. Other tests in the
        # suite still rely on a usable loop being installed on the main
        # thread, so install a fresh one (matching the hygiene used by
        # ``test_image_jobs_submit_shape``).
        asyncio.set_event_loop(asyncio.new_event_loop())
