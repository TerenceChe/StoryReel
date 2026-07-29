"""Scripted scenario test for the capability auth-failure flip.

Feature: ai-background-generation, Property 10: Provider auth failure flips capability for the session
Validates: Requirement 5.5

Scenario (intentionally not deeply randomized — see the design's
"Testing Strategy" section, which calls out Property 10 as a scripted
scenario rather than a property-based test):

1. Reset the process-level :class:`CapabilityState` singleton so a
   prior test's auth-failure flip can't pollute this run.
2. Build a minimal FastAPI app that mounts *only* the capability
   router. Task 15.2 is what wires the router into ``backend/main.py``;
   until then the route lives in isolation, so this test mounts it
   directly rather than against the production app.
3. Bind a ``FakeImageBackend`` (a *non*-disabled backend) via the
   ``get_image_backend`` dependency override and flip its
   ``simulate_auth_failure`` toggle on. The capability route's
   "is the bound backend disabled?" check returns ``False`` for the
   fake, so the *initial* GET reports ``image_generation_enabled=true``.
4. Drive ``JobManager.submit(...)`` directly. The image-jobs HTTP submit
   route is Task 9.x; this test isolates Property 10 to the
   capability-flip behavior and avoids depending on a route that
   doesn't exist yet.
5. Await the worker task to completion. The fake raises
   :class:`ProviderAuthenticationError` from
   ``generate_candidates``, the JobManager catches it, marks the job
   ``failed`` with the generic operator-opaque message, and calls
   ``capability_state.disable_for_session()``.
6. The *next* GET capability returns ``image_generation_enabled=false``
   — the assertion that pins Requirement 5.5.

The JobManager and the capability route share the **same** module-level
:data:`capability_state` singleton (the JobManager picks up the default
when its ``capability_state`` constructor argument is omitted, and the
capability route imports the singleton directly). That shared state is
what makes the auth-failure flip observable across the two layers in
this test.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from backend.auth.middleware import get_owner_id, get_settings
from backend.config import Settings
from backend.dependencies import get_image_backend
from backend.models.image_jobs import GenerationTarget
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.base import StorageBackend
from backend.routers.image_generation import router as capability_router
from backend.services.image_capability_state import capability_state
from backend.services.image_job_service import JobManager
from backend.services.project_service import ProjectService
from backend.tests._image_fakes import FakeImageBackend


_OWNER_ID = "owner-capability-test"


class _InMemoryStorage(StorageBackend):
    """Minimal in-memory ``StorageBackend`` for the JobManager worker.

    The worker only writes (would-be) candidate files via ``save_file``
    and reads URLs via ``get_file_url``. In the auth-failure path the
    worker raises before ever persisting anything, so this fake's
    success-path behavior is never actually exercised in this test —
    but it is implemented in full so an unexpected change to the worker
    that takes the success path doesn't blow up here with an opaque
    ``NotImplementedError``.
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


def _build_settings() -> Settings:
    s = Settings()
    # Bypass auth so the capability route's ``get_owner_id`` dependency
    # short-circuits to the local owner — no JWT setup needed for this
    # test, which is about the capability flip, not authentication.
    s.DISABLE_AUTH = True
    s.LOCAL_OWNER_ID = _OWNER_ID
    return s


def _build_project() -> ProjectState:
    return ProjectState(
        id="project-capability-test",
        owner_id=_OWNER_ID,
        title="Capability Flip Test",
        story_text="A short story for the capability flip scenario.",
        pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
    )


async def _wait_for_failure(
    manager: JobManager, job_id: str, *, timeout: float = 5.0
) -> None:
    """Await the worker task and then poll until status flips to ``failed``.

    The worker's ``finally`` block releases the per-owner concurrency
    slot *after* it sets ``status=failed`` and calls
    ``capability_state.disable_for_session()``, so awaiting the task is
    sufficient in practice. The short polling loop is defense in depth
    in case future worker refactors split the order of those two
    operations.
    """
    task = manager._tasks.get(job_id)
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            pytest.fail(
                f"Worker task for job {job_id} did not complete within "
                f"{timeout}s"
            )

    # Belt-and-braces: poll the job status briefly in case the task is
    # done but the lock-protected ``status`` mutation hasn't been
    # observed yet by this coroutine.
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        job = await manager.get(_OWNER_ID, job_id)
        if job.status == "failed":
            return
        await asyncio.sleep(0.01)
    pytest.fail(
        f"Job {job_id} did not reach status='failed' within {timeout}s"
    )


async def test_provider_auth_failure_flips_capability_for_session(tmp_path):
    """Feature: ai-background-generation, Property 10: Provider auth failure flips capability for the session

    Validates: Requirement 5.5

    Scripted scenario per the design's testing strategy (Property 10 is
    intentionally not randomized): a ``ProviderAuthenticationError``
    raised by the bound :class:`FakeImageBackend` during a job MUST
    cause subsequent ``GET /image-generation/capability`` calls to
    return ``image_generation_enabled=false`` for the rest of the
    process lifetime.
    """
    # Step 1 — reset the singleton so a prior test's flip doesn't leak in.
    # The capability route reads this same module-level instance, and so
    # does the JobManager (via its default ``capability_state`` argument),
    # so resetting it here gives both layers a clean slate.
    capability_state.reset()
    assert capability_state.is_enabled, (
        "Pre-condition: capability_state must be enabled at test start"
    )

    # Step 2 — minimal FastAPI app with only the capability router. The
    # capability route is not yet wired into ``backend/main.py`` (Task
    # 15.2). Mounting just this router keeps the test focused and avoids
    # importing every other production dependency.
    app = FastAPI()
    app.include_router(capability_router)

    test_settings = _build_settings()
    fake_backend = FakeImageBackend()
    # Step 3a — configure the fake to raise a provider auth failure on
    # every candidate-generation call. The fake is *not* the disabled
    # fallback, so the capability route's first signal — "bound backend
    # is enabled" — is True.
    fake_backend.simulate_auth_failure = True

    app.dependency_overrides[get_image_backend] = lambda: fake_backend
    app.dependency_overrides[get_settings] = lambda: test_settings
    # Override get_owner_id directly so the capability route doesn't
    # need a JWT. ``DISABLE_AUTH`` would also work via the settings
    # override, but the explicit owner override is more targeted and
    # makes the test independent of any future change to the auth
    # module's DISABLE_AUTH semantics.
    app.dependency_overrides[get_owner_id] = lambda: _OWNER_ID

    storage = _InMemoryStorage()
    project_service = ProjectService(storage, test_settings)
    project = _build_project()

    # Build the JobManager *without* an injected ``capability_state`` so
    # it picks up the same module-level singleton the capability route
    # reads. That shared instance is what makes the auth-failure flip
    # observable across the two layers in step 6.
    manager = JobManager(
        storage=storage,
        project_service=project_service,
        settings=test_settings,
        backend=fake_backend,
    )
    assert manager.capability_state is capability_state, (
        "JobManager must default to the module-level capability_state "
        "singleton so the flip is visible to the capability route"
    )

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        # Step 3b — initial capability is reported true because the
        # bound backend is the fake (not DisabledImageBackend) and the
        # capability_state has not yet been flipped.
        before = await client.get("/image-generation/capability")
        assert before.status_code == 200
        assert before.json() == {"image_generation_enabled": True}, (
            "Initial capability must be true while the bound backend is "
            "enabled and no auth failure has been observed yet"
        )

        # Step 4 — drive JobManager.submit directly. The HTTP submit
        # route doesn't exist yet (Task 9.x); this test isolates the
        # capability flip from that route's eventual implementation.
        job = await manager.submit(
            _OWNER_ID,
            project,
            prompt="moonlit forest",
            image_count=1,
            target=GenerationTarget(kind="whole_video"),
        )

        # Step 5 — wait for the worker to finish. The fake raises
        # ProviderAuthenticationError from generate_candidates, the
        # JobManager catches it, marks the job failed, and calls
        # capability_state.disable_for_session().
        await _wait_for_failure(manager, job.id)

        failed_job = await manager.get(_OWNER_ID, job.id)
        assert failed_job.status == "failed", (
            f"Job must reach status='failed' on auth failure; got "
            f"{failed_job.status!r}"
        )

        # Step 6 — the headline assertion: capability is now false.
        after = await client.get("/image-generation/capability")
        assert after.status_code == 200
        assert after.json() == {"image_generation_enabled": False}, (
            "After a ProviderAuthenticationError flips capability_state, "
            "GET /image-generation/capability must return "
            "image_generation_enabled=false (Requirement 5.5)"
        )

    # Cleanup the global singleton so a subsequent test in the same
    # process isn't accidentally observing this test's flip. Other tests
    # that need isolation construct their own CapabilityState; this
    # cleanup just keeps the shared default consistent with how it
    # arrived.
    capability_state.reset()
