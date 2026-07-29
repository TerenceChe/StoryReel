"""Property-based test for reference image upload validation.

Feature: ai-background-generation, Property 2: Reference image upload validation
Validates: Requirements 2.2, 2.3, 9.3

Hypothesis generates ``(content_type, filename_extension, byte_size)``
triples covering the cross-product of allowed and disallowed values for
each axis. For every triple the test asserts that the
``POST /projects/{project_id}/image-jobs/{job_id}/reference`` route
behaves per the design's Property 2:

* Accepts the upload (200) **iff** all three predicates hold:
  ``content_type ∈ {"image/png", "image/jpeg"}``  AND
  ``filename_extension ∈ {".png", ".jpg", ".jpeg"}``  AND
  ``byte_size ≤ MAX_REFERENCE_IMAGE_SIZE_MB × 1024 × 1024``.
* Rejects with 422 when the type/extension check fails — checked first,
  irrespective of size, mirroring the router's order of validation
  (Requirements 2.3, 9.3).
* Rejects with 413 when only the size check fails — i.e. when the type
  and extension are valid but the body is too large (Requirement 2.2).

Design notes baked into this test:

* The route's reference-attachment path requires the target job to be
  in ``pending`` state — once the worker transitions to ``running``
  (which the in-process worker does very quickly with
  :class:`FakeImageBackend`, since its ``generate_candidates`` body has
  no real awaits), :class:`ImageJobInvalidStateError` would surface as
  a 409 and dilute the property's 422 / 413 / 200 tri-state. To keep
  the predicate clean the test bypasses :meth:`JobManager.submit` and
  inserts a synthetic ``pending`` :class:`GenerationJob` directly into
  the JobManager's in-memory registry. No worker task is spawned, so
  no race exists. The HTTP attach-reference path under test is exercised
  in full; only the upstream "create job" step is short-circuited. This
  matches the task notes' permission to "submit the job and immediately
  upload the reference" (the manual insertion is the deterministic
  endpoint of that strategy).
* ``MAX_REFERENCE_IMAGE_SIZE_MB`` is set to 1 (the smallest integer MB
  the route accepts), making the byte cap 1 MiB. The strategy biases
  ``byte_size`` toward two regions — small values well under the cap and
  values densely clustered around the boundary — so Hypothesis exercises
  both the obviously-OK case and the boundary case (cap, cap+1) without
  allocating absurd buffers per example.
* Each example builds a fresh FastAPI app + manager and tears it down,
  so the JobManager's in-memory registry never carries leftovers between
  draws and ``app.dependency_overrides`` stay scoped to the example.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from hypothesis import given, settings as hyp_settings, strategies as st

from backend.auth.middleware import get_owner_id
from backend.auth.middleware import get_settings as get_auth_settings
from backend.config import Settings
from backend.dependencies import (
    get_image_backend,
    get_job_manager,
    get_project_service,
    get_settings,
    get_storage,
)
from backend.models.image_jobs import GenerationJob, GenerationTarget
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.base import StorageBackend
from backend.routers.image_jobs import router as image_jobs_router
from backend.services.image_capability_state import capability_state
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectNotFoundError
from backend.tests._image_fakes import FakeImageBackend


_OWNER_ID = "owner-reference-upload-test"
_PROJECT_ID = "project-reference-upload-test"
_JOB_ID = "job-reference-upload-test"
# ``MAX_REFERENCE_IMAGE_SIZE_MB`` is configured per-example to the
# smallest practical integer (1 MB) so the byte buffers Hypothesis
# generates near the boundary stay manageable across 100 examples.
_MAX_REFERENCE_MB = 1
_MAX_BYTES = _MAX_REFERENCE_MB * 1024 * 1024


# ---------------------------------------------------------------------------
# Strategy domains — including both allowed values and a representative
# selection of disallowed values so the predicate's "iff" semantics are
# probed in both directions.
# ---------------------------------------------------------------------------

_CONTENT_TYPES = (
    # Allowed
    "image/png",
    "image/jpeg",
    # Disallowed
    "application/octet-stream",
    "text/plain",
    "",
)

_FILENAME_EXTENSIONS = (
    # Allowed
    ".png",
    ".jpg",
    ".jpeg",
    # Disallowed
    ".gif",
    ".txt",
    "",
)

_ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg"}
_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _InMemoryStorage(StorageBackend):
    """Minimal in-memory ``StorageBackend`` for the reference upload path.

    The route's success path calls ``save_file`` to persist the uploaded
    bytes under ``imgjobs/{job_id}/reference.{ext}``. Tests that hit the
    422 / 413 branches never reach ``save_file``, but the implementation
    is in full so the success path lands successfully.
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

    The router's ``_load_owned_project`` helper calls
    ``project_service.get_project(project_id)``; the stub gives the
    helper exactly the shape it needs without standing up a full
    :class:`backend.services.project_service.ProjectService` per example.
    """

    def __init__(self, project: ProjectState) -> None:
        self._project = project

    async def get_project(self, project_id: str) -> ProjectState:
        if project_id != self._project.id:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return self._project


def _make_project() -> ProjectState:
    """Construct a deterministic :class:`ProjectState` for the stub service.

    ``owner_id`` matches ``_OWNER_ID`` so :func:`verify_project_ownership`
    always passes — Property 2 is about the upload validation predicate,
    not ownership (Property 6 covers ownership separately).
    """
    return ProjectState(
        id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        title="Reference Upload Validation Test",
        story_text="A short story for reference-upload-validation testing.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        subtitles=[],
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


def _make_pending_job() -> GenerationJob:
    """Build a :class:`GenerationJob` in ``pending`` state for direct insertion.

    The route requires the target job to be in ``pending`` state (Task
    10.1: "can only be attached while the job is in ``pending`` state").
    Going through :meth:`JobManager.submit` would spawn an asyncio worker
    task that races to set ``status = "running"`` before the test's
    upload request arrives; manually inserting the job sidesteps that
    race entirely so the test pins the validation predicate, not the
    worker scheduler.
    """
    return GenerationJob(
        id=_JOB_ID,
        project_id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        prompt="reference upload validation test",
        image_count=1,
        target=GenerationTarget(kind="whole_video"),
        status="pending",
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Hypothesis strategy
# ---------------------------------------------------------------------------


@st.composite
def _upload_triple(draw) -> tuple[str, str, int]:
    """Draw ``(content_type, filename_extension, byte_size)`` for a request.

    ``content_type`` and ``filename_extension`` are sampled from the
    fixed sets defined above so the predicate has a small, fully-covered
    domain.

    ``byte_size`` is biased toward two regions:

    * ``[0, 64]`` — small payloads well under the 1 MiB cap. These
      cover the size-OK case efficiently without per-example MB-sized
      allocations.
    * ``[_MAX_BYTES - 8, _MAX_BYTES + 8]`` — densely clustered around
      the boundary so Hypothesis exercises both the largest accepted
      size and the smallest rejected size. This is where the 413 path
      and the 200 path differ by a single byte.
    """
    content_type = draw(st.sampled_from(_CONTENT_TYPES))
    extension = draw(st.sampled_from(_FILENAME_EXTENSIONS))
    byte_size = draw(
        st.one_of(
            st.integers(min_value=0, max_value=64),
            st.integers(
                min_value=_MAX_BYTES - 8, max_value=_MAX_BYTES + 8
            ),
        )
    )
    return content_type, extension, byte_size


# ---------------------------------------------------------------------------
# Property 2: Reference image upload validation
# ---------------------------------------------------------------------------
# Feature: ai-background-generation, Property 2: Reference image upload validation
# Validates: Requirements 2.2, 2.3, 9.3


@given(triple=_upload_triple())
@hyp_settings(max_examples=100, deadline=None)
def test_reference_upload_validation(triple: tuple[str, str, int]) -> None:
    """Feature: ai-background-generation, Property 2: Reference image upload validation

    Validates: Requirements 2.2, 2.3, 9.3

    For any ``(content_type, filename_extension, byte_size)`` tuple
    submitted to ``POST /projects/{id}/image-jobs/{job_id}/reference``:

    * 200 iff ``content_type ∈ {"image/png", "image/jpeg"}`` AND
      ``filename_extension ∈ {".png", ".jpg", ".jpeg"}`` AND
      ``byte_size ≤ MAX_REFERENCE_IMAGE_SIZE_MB × 1024 × 1024``.
    * 422 when the type/extension check fails (independent of size —
      the route validates type/ext first and short-circuits).
    * 413 when only the size check fails (type and extension are both
      valid but the body exceeds the cap).
    """
    content_type, extension, byte_size = triple

    # Compute the expected status code from the predicate. The order
    # mirrors the route's checks so the test pins the same precedence:
    # type/ext first (422), then size (413), then 200 on the success
    # path.
    type_ok = content_type in _ALLOWED_CONTENT_TYPES
    ext_ok = extension in _ALLOWED_EXTENSIONS
    size_ok = byte_size <= _MAX_BYTES

    if not (type_ok and ext_ok):
        expected_status = 422
    elif not size_ok:
        expected_status = 413
    else:
        expected_status = 200

    async def _run() -> None:
        # Reset the process-level capability flag in case any earlier
        # test in this process flipped it off via a simulated provider
        # auth failure. The reference upload route does not consult the
        # capability flag itself, but ``_load_owned_project`` runs
        # before the validation logic and downstream behavior should
        # not be affected by stale flag state.
        capability_state.reset()

        # Per-example app + manager so the JobManager registry stays
        # clean across draws (no leftover jobs / running slots) and the
        # ``MAX_REFERENCE_IMAGE_SIZE_MB`` override is scoped to this
        # example.
        app = FastAPI()
        app.include_router(image_jobs_router)

        test_settings = Settings()
        # Bypass auth — the test owns the ``get_owner_id`` override so
        # the auth middleware never runs. ``DISABLE_AUTH`` is also
        # flipped for defense in depth.
        test_settings.DISABLE_AUTH = True
        test_settings.LOCAL_OWNER_ID = _OWNER_ID
        # Pin the reference image cap to a small integer so generated
        # bytes near the boundary stay tractable. Other limits are not
        # exercised by this property.
        test_settings.MAX_REFERENCE_IMAGE_SIZE_MB = _MAX_REFERENCE_MB

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

        # Manually insert a pending job into the manager's registry so
        # the route's job-state check passes without spawning a worker
        # task. See the module docstring's "Design notes" for why this
        # bypass is intentional.
        pending_job = _make_pending_job()
        manager._jobs[pending_job.id] = pending_job
        manager._running_per_owner[_OWNER_ID] = {pending_job.id}

        app.dependency_overrides[get_owner_id] = lambda: _OWNER_ID
        app.dependency_overrides[get_settings] = lambda: test_settings
        app.dependency_overrides[get_auth_settings] = lambda: test_settings
        app.dependency_overrides[get_image_backend] = lambda: fake_backend
        app.dependency_overrides[get_project_service] = (
            lambda: stub_project_service
        )
        app.dependency_overrides[get_job_manager] = lambda: manager
        app.dependency_overrides[get_storage] = lambda: storage

        # Build the multipart payload. ``filename`` is "reference{ext}"
        # so the server-side ``filename.endswith(_REFERENCE_ALLOWED_EXTS)``
        # check sees exactly the drawn extension. With an empty drawn
        # extension the filename is "reference" with no suffix, which
        # the route correctly rejects.
        filename = f"reference{extension}"
        body = b"\x00" * byte_size

        try:
            transport = ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    f"/projects/{_PROJECT_ID}"
                    f"/image-jobs/{_JOB_ID}/reference",
                    files={"file": (filename, body, content_type)},
                )

            assert resp.status_code == expected_status, (
                f"Expected {expected_status} for "
                f"(content_type={content_type!r}, "
                f"extension={extension!r}, "
                f"byte_size={byte_size}); "
                f"got {resp.status_code}: {resp.text!r}"
            )
        finally:
            # No worker tasks were spawned (we bypassed ``submit``), but
            # cancel any that exist defensively in case a future
            # refactor of the manual-insert path starts spawning them.
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
        # peer property tests in this directory).
        asyncio.set_event_loop(asyncio.new_event_loop())
