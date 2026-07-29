"""Property-based test for image_count range validation.

Feature: ai-background-generation, Property 4: image_count range validation
Validates: Requirements 4.1, 4.3 (and 4.2 omitted-default case)

Hypothesis generates ``image_count`` values covering a wide integer
range (including negatives, zero, and values well above the configured
cap) plus the *omitted* case (the field absent from the JSON body
entirely). For every drawn value the test asserts that the
``POST /projects/{project_id}/image-jobs`` route accepts the request
(202) **iff**::

    1 <= image_count <= MAX_IMAGES_PER_JOB

with the omitted case treated as ``image_count == 1`` per the schema
default declared on
:class:`backend.routers.image_jobs.ImageJobSubmitRequest`. Otherwise the
route rejects with 422.

Two layers can produce the 422 response and both satisfy this property:

* Pydantic body validation — but here every drawn ``image_count`` is
  either an integer or omitted, which the schema accepts at the type
  level. The JobManager service-level range check is what converts an
  out-of-range integer to
  :class:`backend.services.image_job_errors.ImageJobValidationError`.
* :class:`backend.services.image_job_errors.ImageJobValidationError`
  raised by ``JobManager._validate_image_count``, mapped to 422 by the
  router's shared exception mapper.

The ``target`` is fixed at ``whole_video`` so this test isolates the
``image_count`` predicate from the section-index predicate covered by
``test_image_jobs_section_validation``.

The test mounts only the image-jobs router on a freshly-built FastAPI
app per Hypothesis example (mirroring ``test_image_jobs_submit_shape``
and ``test_image_jobs_section_validation``) so the JobManager registry
never carries leftovers between draws and the bound
``MAX_CONCURRENT_IMAGE_JOBS_PER_USER`` cap is large enough to absorb a
back-to-back burst of accepted submissions.
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
from backend.persistence.base import StorageBackend
from backend.routers.image_jobs import router as image_jobs_router
from backend.services.image_capability_state import capability_state
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectNotFoundError
from backend.tests._image_fakes import FakeImageBackend


_OWNER_ID = "owner-image-count-test"
_PROJECT_ID = "project-image-count-test"
# Same default value as ``MAX_IMAGES_PER_JOB`` in ``backend/config.py``.
# Pinned at the module level so the strategy and the acceptance
# predicate share the same constant — changing the shipped default
# would only require updating one line.
_MAX_IMAGES = 4

# Sentinel used by the strategy to mean "omit the ``image_count`` key
# from the request body". A plain ``None`` would be sent over the wire
# as ``"image_count": null`` which Pydantic rejects with 422 at the
# type level (the field is declared ``int``, not ``int | None``); the
# Requirement 4.2 default-of-1 behaviour applies only when the key is
# absent from the JSON body, not when it is present and null.
_OMITTED = object()


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
    ``_load_owned_project``. The stub keeps the test focused on
    ``image_count`` validation rather than wiring a full ProjectService.
    """

    def __init__(self, project: ProjectState) -> None:
        self._project = project

    async def get_project(self, project_id: str) -> ProjectState:
        if project_id != self._project.id:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return self._project


def _make_project() -> ProjectState:
    """Construct a deterministic :class:`ProjectState` for the stub service.

    ``owner_id`` is the same constant the test overrides ``get_owner_id``
    to return so :func:`verify_project_ownership` always passes — the
    image_count property is about the numeric range check, so 403 paths
    are out of scope here. The ``subtitles`` list is left empty because
    the ``whole_video`` target does not consult it (Requirement 3.3
    applies only to the ``section`` target).
    """
    return ProjectState(
        id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        title="Image Count Validation Test",
        story_text="A short story for image_count-validation testing.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        subtitles=[],
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Hypothesis strategy
# ---------------------------------------------------------------------------


# Wide range that reliably exercises every rejection branch:
#
# * negative values (well below 1) — invalid
# * zero — invalid (the lower bound is 1, not 0)
# * 1 through ``_MAX_IMAGES`` — valid
# * ``_MAX_IMAGES + 1`` through 100 — invalid (well above the cap)
#
# A ``just(_OMITTED)`` branch is mixed in via :func:`one_of` to cover
# Requirement 4.2's "omitted defaults to 1" path. The branches are
# weighted so the omitted case shows up reliably across 100 examples
# (without it, Hypothesis spends most of its budget on plain integers).
_image_count_strategy = st.one_of(
    st.just(_OMITTED),
    st.integers(min_value=-100, max_value=100),
)


# ---------------------------------------------------------------------------
# Property 4: image_count range validation
# ---------------------------------------------------------------------------
# Feature: ai-background-generation, Property 4: image_count range validation
# Validates: Requirements 4.1, 4.3 (and 4.2 omitted-default case)


@given(image_count=_image_count_strategy)
@hyp_settings(max_examples=100, deadline=None)
def test_image_count_range_validation(image_count: object) -> None:
    """Feature: ai-background-generation, Property 4: image_count range validation

    Validates: Requirements 4.1, 4.3 (and 4.2 omitted-default case)

    For any ``image_count`` value submitted (or omitted), the job is
    accepted (202) **iff** ``1 <= image_count <= MAX_IMAGES_PER_JOB``,
    with omitted treated as ``1``; otherwise the response is 422.
    """
    # Compute the acceptance predicate up front so the assertion message
    # can include the exact value Hypothesis drew (the request body
    # branches on ``_OMITTED`` below; the predicate works directly on
    # the drawn value without that translation).
    if image_count is _OMITTED:
        effective_count = 1
    else:
        assert isinstance(image_count, int)
        effective_count = image_count

    expected_accept = 1 <= effective_count <= _MAX_IMAGES

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

        project = _make_project()
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

        # Build the body conditionally so the omitted case really does
        # exercise the schema's default-of-1 path (Requirement 4.2)
        # rather than sending ``"image_count": null`` which Pydantic
        # would reject as a type error before the JobManager's range
        # check ever runs.
        body: dict = {
            "prompt": "image_count validation test",
            "target": {"kind": "whole_video"},
        }
        if image_count is not _OMITTED:
            body["image_count"] = image_count

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
                    f"Expected 202 for valid image_count "
                    f"(drawn={image_count!r}, effective={effective_count}); "
                    f"got {resp.status_code}: {resp.text!r}"
                )
            else:
                assert resp.status_code == 422, (
                    f"Expected 422 for invalid image_count "
                    f"(drawn={image_count!r}, effective={effective_count}); "
                    f"got {resp.status_code}: {resp.text!r}"
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
        # ``test_image_jobs_submit_shape`` and
        # ``test_image_jobs_section_validation``).
        asyncio.set_event_loop(asyncio.new_event_loop())
