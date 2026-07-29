"""Log-capture safety test for the JobManager's failure path.

Feature: ai-background-generation, Task 15.4 — Log-capture safety
Validates: Requirement 9.2

Drives a single failing job through :class:`JobManager` against a
:class:`FakeImageBackend` configured to raise
:class:`ProviderAuthenticationError`. Captures every log record emitted
during the run via the ``caplog`` fixture at ``DEBUG`` level (so the
suite catches anything the worker, the JobManager, or any transitively
imported module logs while handling the failure), and asserts none of
the records carry operator-facing strings.

Specifically the assertion forbids — case-insensitive substring search:

* the synthetic API key marker injected into the fake backend,
* the literal env var name ``OPENAI_API_KEY``,
* the literal env var name ``IMAGE_GEN_PROVIDER``,
* the literal phrase ``API key``.

The JobManager's own log discipline is encoded in
``backend/services/image_job_service.py``: provider failures are logged
at ``WARN`` with a sanitized category string only (e.g. ``auth_failed``,
``unknown``), never the raw exception. This test pins that discipline
against an end-to-end run so a future refactor that accidentally logs
the underlying exception (or wires the API key into a debug log line)
gets caught here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import pytest

from backend.config import Settings
from backend.models.image_jobs import GenerationTarget
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.base import StorageBackend
from backend.services.image_capability_state import CapabilityState
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectNotFoundError
from backend.tests._image_fakes import FakeImageBackend


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_OWNER_ID = "owner-logging-test"
_PROJECT_ID = "project-logging-test"

# Distinctive synthetic API key value. Hard-to-collide so a substring
# search inside any captured log record only matches a true leak. The
# :class:`FakeImageBackend` records this on its instance but never
# echoes it — the property under test is that the marker never reaches
# any log record under the failing-job path.
_SYNTHETIC_API_KEY = "SyntheticApiKeyValue_LOG_TEST_DO_NOT_LEAK_8a3c"

# Substrings that MUST NOT appear in any captured log record's message,
# case-insensitive. Sourced verbatim from Task 15.4's instructions.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    _SYNTHETIC_API_KEY,
    "OPENAI_API_KEY",
    "IMAGE_GEN_PROVIDER",
    "API key",
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _InMemoryStorage(StorageBackend):
    """Minimal in-memory ``StorageBackend`` for the JobManager worker.

    The worker for a failing job never reaches the candidate-persist
    branch (the failure short-circuits before ``_persist_candidates``),
    but the storage interface still needs a real implementation so the
    JobManager can be constructed without errors.
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
    """Stub project-service exposing only ``get_project``.

    The JobManager's failing-job path under test here never calls
    ``apply_candidate``, so ``get_project`` is the only method exercised
    — and it's only consulted when the worker needs to verify ownership
    on an apply call. For the failure-only flow this stub is strictly
    a constructor argument, but we keep ``get_project`` faithful so a
    future regression that triggers project lookups in the failure path
    still sees a sensible response.
    """

    def __init__(self, project: ProjectState) -> None:
        self._project = project

    async def get_project(self, project_id: str) -> ProjectState:
        if project_id != self._project.id:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return self._project


def _make_project() -> ProjectState:
    """Construct a deterministic :class:`ProjectState` for the test.

    The project text and title are benign ASCII so they don't accidentally
    collide with any forbidden substring; this keeps the assertion sharp
    if the JobManager were to log project metadata.
    """
    return ProjectState(
        id=_PROJECT_ID,
        owner_id=_OWNER_ID,
        title="Logging Safety Test",
        story_text="A short benign story for log-capture safety testing.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_failing_job_logs_never_leak_operator_facing_strings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Feature: ai-background-generation, Task 15.4 — Log-capture safety

    Validates: Requirement 9.2

    Drive a single auth-failure job and assert no captured log record's
    rendered message carries the synthetic API key value, the strings
    ``OPENAI_API_KEY``, ``IMAGE_GEN_PROVIDER``, or ``API key``
    (case-insensitive substring match).
    """
    # Capture from every logger in the process at DEBUG level for the
    # duration of the test. The JobManager logs at WARN today, but a
    # future refactor could add DEBUG-level lines that mistakenly carry
    # the provider exception body — we want this test to flag that
    # regression too. ``caplog.set_level(DEBUG)`` configures both the
    # caplog handler and the root logger so propagated records are kept.
    caplog.set_level(logging.DEBUG)

    # Build the test app's services. The fake backend is configured to
    # always raise ``ProviderAuthenticationError`` so the worker takes
    # the failure branch in :meth:`JobManager._run_job`.
    storage = _InMemoryStorage()
    project = _make_project()
    fake_backend = FakeImageBackend(api_key_marker=_SYNTHETIC_API_KEY)
    fake_backend.simulate_auth_failure = True
    stub_project_service = _StubProjectService(project)

    test_settings = Settings()
    test_settings.MAX_IMAGES_PER_JOB = 4
    test_settings.MAX_CONCURRENT_IMAGE_JOBS_PER_USER = 2

    manager = JobManager(
        storage=storage,
        project_service=stub_project_service,  # type: ignore[arg-type]
        settings=test_settings,
        backend=fake_backend,
        # Per-test isolated CapabilityState so the auth-failure flip
        # doesn't bleed into the global singleton (which other tests in
        # the suite consult).
        capability_state=CapabilityState(),
    )

    async def _run() -> None:
        # Submit a single job. The prompt is a benign ASCII string so
        # any future log line that does include it (e.g. for diagnostics)
        # cannot accidentally collide with one of the forbidden
        # substrings; if the JobManager's discipline drifts and starts
        # logging the prompt verbatim, this test still works as a
        # leak detector for the synthetic API key marker.
        job = await manager.submit(
            _OWNER_ID,
            project,
            prompt="logging safety test prompt",
            image_count=1,
            target=GenerationTarget(kind="whole_video"),
        )

        # Await the worker so the auth-failure path runs to completion.
        # The JobManager's worker registers its task in ``_tasks`` and
        # cleans up on completion; pulling the task here mirrors the
        # pattern used by ``test_image_jobs_response_safety``.
        task = manager._tasks.get(job.id)
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:  # pragma: no cover — defensive
                raise AssertionError(
                    f"Worker task for job {job.id} did not complete "
                    f"within 5s"
                )

        # Sanity check the failure path actually ran — without this,
        # an accidental change that turned the auth-failure into a
        # success would silently pass the leak assertion below.
        snapshot = await manager.get(_OWNER_ID, job.id)
        assert snapshot.status == "failed", (
            f"Test setup invariant: the job should have reached 'failed' "
            f"via the auth-failure path, but status was {snapshot.status!r}"
        )

    try:
        asyncio.run(_run())
    finally:
        # ``asyncio.run`` closes the event loop it created and leaves
        # the thread without a current event loop. Other tests in the
        # suite rely on a usable loop being installed on the main
        # thread, so install a fresh one (matching the hygiene used by
        # peer property tests in this directory).
        asyncio.set_event_loop(asyncio.new_event_loop())

    # Inspect every captured record. ``caplog.records`` is the canonical
    # accessor for the LogRecord objects pytest captured during the test.
    # We use ``getMessage()`` (the rendered message after %-arg
    # substitution) rather than ``record.msg`` so a hypothetical log
    # like ``logger.warning("api key: %s", key)`` would still surface
    # the substituted key value to this check.
    leaks: list[tuple[str, str, str]] = []
    for record in caplog.records:
        message = record.getMessage()
        haystack = message.lower()
        for needle in _FORBIDDEN_SUBSTRINGS:
            if needle.lower() in haystack:
                leaks.append((record.name, record.levelname, message))
                break

    assert not leaks, (
        "Failing-job log capture leaked operator-facing strings. "
        f"Offending records: {leaks!r}"
    )
