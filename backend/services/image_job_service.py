"""In-process JobManager for AI background image-generation jobs.

This service is the heart of the image-job feature. It owns:

* the in-memory job registry (``_jobs``),
* per-owner concurrent-job accounting (``_running_per_owner``),
* the worker ``asyncio.Task`` per job (``_tasks``),
* lifecycle transitions ``pending → running → succeeded | failed``,
* persistence of generated candidate bytes via ``StorageBackend``, and
* applying a chosen candidate to ``ProjectState`` through
  ``ProjectService.update_project`` (so optimistic-concurrency, ownership,
  and timing validation continue to live in one place).

Design notes:

* The job registry is **not** persisted. A backend restart loses in-flight
  jobs; status reads then return 404 and clients must resubmit. Already
  applied candidates remain on disk and on ``ProjectState``.
* The router maps :mod:`backend.services.image_job_errors` exceptions to
  HTTP status codes. The JobManager raises domain exceptions, not
  ``HTTPException``.
* The candidate methods (``generate_candidates``,
  ``generate_section_candidates``) are not declared on the abstract
  ``ImageGenerationBackend`` base — they are duck-typed on concrete
  adapters (and on ``DisabledImageBackend``). The worker calls them via a
  ``Protocol`` cast so type checkers can still help.
* All user-facing error strings are operator-opaque: no provider name, no
  environment variable name, no provider stack trace. Provider failures
  are categorized for log lines (e.g. ``auth_failed``, ``unknown``); the
  raw exception is **not** placed on ``GenerationJob.error_message``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol, cast

from backend.config import Settings
from backend.models.image_gen import ImageGenerationBackend
from backend.models.image_jobs import (
    CandidateImage,
    GenerationJob,
    GenerationTarget,
)
from backend.models.project import ProjectState, SectionBackground
from backend.persistence.base import StorageBackend
from backend.services.image_capability_state import (
    CapabilityState,
    capability_state as default_capability_state,
)
from backend.services.image_job_errors import (
    ImageJobCandidateNotFoundError,
    ImageJobConcurrencyError,
    ImageJobInvalidStateError,
    ImageJobNotFoundError,
    ImageJobValidationError,
    ProviderAuthenticationError,
)
from backend.services.project_service import ProjectService

logger = logging.getLogger(__name__)


# Operator-opaque message surfaced to end users on any provider failure.
# Reused across all failure paths so a future log-capture / response-safety
# property test can pin on a single string.
_GENERIC_FAILURE_MESSAGE = "AI background generation is currently unavailable"


class _SupportsCandidateGeneration(Protocol):
    """Duck-typed contract the worker relies on for generation.

    Lives outside the abstract ``ImageGenerationBackend`` base on purpose:
    keeping the abstract surface narrow preserves backward compatibility
    while letting concrete adapters (and the disabled fallback) expose the
    richer "N candidates with optional reference image" API the JobManager
    actually uses.
    """

    async def generate_candidates(
        self,
        prompt: str,
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[bytes]: ...

    async def generate_section_candidates(
        self,
        prompts: list[str],
        *,
        image_count: int,
        reference_image_bytes: bytes | None,
    ) -> list[list[bytes]]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _categorize_provider_error(exc: BaseException) -> str:
    """Map a provider exception to a sanitized log category.

    The log line never contains the original exception message — only the
    category — because provider error strings can carry sensitive detail
    (e.g. echoed credentials in some SDKs). Property 1 in the design
    requires user-facing payloads to stay operator-opaque, and this helper
    keeps the same discipline for log records.
    """
    if isinstance(exc, ProviderAuthenticationError):
        return "auth_failed"
    if isinstance(exc, asyncio.CancelledError):
        return "cancelled"
    return "unknown"


class JobManager:
    """In-process registry + driver for ``GenerationJob`` instances."""

    def __init__(
        self,
        storage: StorageBackend,
        project_service: ProjectService,
        settings: Settings,
        backend: ImageGenerationBackend,
        capability_state: CapabilityState | None = None,
    ) -> None:
        self.storage = storage
        self.project_service = project_service
        self.settings = settings
        self.backend = backend
        # Tests can inject an isolated CapabilityState; production code
        # uses the process-level default singleton so the capability
        # endpoint and the JobManager observe the same flag.
        self.capability_state = capability_state or default_capability_state

        self._jobs: dict[str, GenerationJob] = {}
        self._running_per_owner: dict[str, set[str]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(
        self,
        owner_id: str,
        project: ProjectState,
        *,
        prompt: str,
        image_count: int,
        target: GenerationTarget,
    ) -> GenerationJob:
        """Create and start a new ``GenerationJob``.

        Validates ``image_count`` and the ``target`` *before* taking the
        per-owner concurrency lock, so a malformed submission doesn't burn
        a slot. Slot acquisition and registry insertion happen atomically
        under ``self._lock`` so a burst of concurrent submissions cannot
        race past the cap.
        """
        self._validate_image_count(image_count)
        self._validate_target(target, project)

        now = _now_iso()
        job = GenerationJob(
            id=uuid.uuid4().hex,
            project_id=project.id,
            owner_id=owner_id,
            prompt=prompt,
            image_count=image_count,
            target=target,
            status="pending",
            created_at=now,
            updated_at=now,
        )

        async with self._lock:
            running = self._running_per_owner.setdefault(owner_id, set())
            cap = self.settings.MAX_CONCURRENT_IMAGE_JOBS_PER_USER
            if len(running) >= cap:
                raise ImageJobConcurrencyError(
                    f"You already have {len(running)} image-generation "
                    f"jobs running. Wait for one to finish."
                )
            running.add(job.id)
            self._jobs[job.id] = job

        # Schedule the worker outside the lock. ``create_task`` only
        # registers the coroutine with the event loop; the actual body
        # won't run until the next loop tick, by which point the lock is
        # released.
        self._tasks[job.id] = asyncio.create_task(self._run_job(job))
        return job

    async def get(self, owner_id: str, job_id: str) -> GenerationJob:
        """Return a snapshot of the job if it belongs to ``owner_id``.

        We return a deep copy so a caller serializing the result cannot
        observe a torn state half-way through a worker mutation.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner_id != owner_id:
                raise ImageJobNotFoundError(f"Job {job_id} not found")
            return job.model_copy(deep=True)

    async def attach_reference(
        self,
        owner_id: str,
        job_id: str,
        reference_filename: str,
    ) -> None:
        """Record a previously-uploaded reference image on the job.

        The router has already saved the bytes via ``StorageBackend``; this
        method just notes the storage filename so the worker can load it
        before dispatching to the provider. The job must still be in
        ``pending`` state — once the worker starts, attaching a reference
        is too late and we surface 409.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner_id != owner_id:
                raise ImageJobNotFoundError(f"Job {job_id} not found")
            if job.status != "pending":
                raise ImageJobInvalidStateError(
                    "Reference image must be attached before the job "
                    "starts running"
                )
            job.reference_image_filename = reference_filename
            job.updated_at = _now_iso()

    async def apply_candidate(
        self,
        owner_id: str,
        job_id: str,
        *,
        candidate_id: str,
        version: int,
    ) -> ProjectState:
        """Apply a chosen candidate to the project's background fields.

        Persistence flows through ``ProjectService.update_project`` so the
        existing optimistic-concurrency check on ``version`` runs in the
        canonical path. Ownership is re-checked here as defense in depth;
        the router should already have verified it before reaching us.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner_id != owner_id:
                raise ImageJobNotFoundError(f"Job {job_id} not found")
            if job.status != "succeeded":
                raise ImageJobInvalidStateError(
                    "Candidate can only be applied after the job succeeds"
                )
            candidate = next(
                (c for c in job.candidates if c.id == candidate_id),
                None,
            )
            if candidate is None:
                raise ImageJobCandidateNotFoundError("Unknown candidate")
            # Snapshot what we need outside the JobManager lock; the
            # ProjectService has its own concurrency model and we don't
            # want to hold both locks simultaneously.
            project_id = job.project_id
            target = job.target.model_copy()
            candidate_url = candidate.url

        state = await self.project_service.get_project(project_id)
        if state.owner_id != owner_id:
            # Defense in depth — the router uses verify_project_ownership
            # before calling us, but a JobManager that trusts inputs is a
            # JobManager that leaks data when a router bug slips through.
            raise ImageJobInvalidStateError("Not authorized")

        # The caller's expected version drives the optimistic-concurrency
        # check inside ProjectService.update_project. Override whatever
        # version the freshly-loaded state happens to carry so a stale
        # ``version`` from the request is detected as a conflict.
        state.version = version

        if target.kind == "whole_video":
            state.background_image = candidate_url
        else:
            assert target.start_index is not None
            assert target.end_index is not None
            new_entry = SectionBackground(
                start_index=target.start_index,
                end_index=target.end_index,
                image_url=candidate_url,
            )
            replaced = False
            for i, sb in enumerate(state.section_backgrounds):
                if (
                    sb.start_index == target.start_index
                    and sb.end_index == target.end_index
                ):
                    state.section_backgrounds[i] = new_entry
                    replaced = True
                    break
            if not replaced:
                state.section_backgrounds.append(new_entry)

        return await self.project_service.update_project(project_id, state)

    async def delete_job(self, owner_id: str, job_id: str) -> None:
        """Forget the job and cancel its worker task if still running.

        Storage cleanup (reference image, candidate files) is the caller's
        responsibility — the JobManager only owns in-memory state.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner_id != owner_id:
                raise ImageJobNotFoundError(f"Job {job_id} not found")
            task = self._tasks.pop(job_id, None)
            del self._jobs[job_id]
            running = self._running_per_owner.get(owner_id)
            if running is not None:
                running.discard(job_id)

        if task is not None and not task.done():
            task.cancel()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_image_count(self, image_count: int) -> None:
        if not (1 <= image_count <= self.settings.MAX_IMAGES_PER_JOB):
            raise ImageJobValidationError(
                f"image_count must be between 1 and "
                f"{self.settings.MAX_IMAGES_PER_JOB}"
            )

    def _validate_target(
        self, target: GenerationTarget, project: ProjectState
    ) -> None:
        if target.kind == "whole_video":
            return
        # kind == "section"; the GenerationTarget model_validator already
        # ensured both indices are present, but we re-check defensively.
        if target.start_index is None or target.end_index is None:
            raise ImageJobValidationError("Invalid section indices")
        subs_len = len(project.subtitles)
        if subs_len == 0:
            raise ImageJobValidationError(
                "Subtitles must be generated before requesting a "
                "section background"
            )
        if (
            target.start_index < 0
            or target.end_index >= subs_len
            or target.start_index > target.end_index
        ):
            raise ImageJobValidationError("Invalid section indices")

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _run_job(self, job: GenerationJob) -> None:
        """Drive a single job to completion.

        Always releases the per-owner concurrency slot in ``finally``,
        regardless of success, failure, or cancellation. Provider error
        details are logged at WARN with a sanitized category only — they
        never make it into ``GenerationJob.error_message``.
        """
        try:
            reference_bytes = await self._load_reference_bytes(job)

            async with self._lock:
                job.status = "running"
                job.updated_at = _now_iso()

            adapter = cast(_SupportsCandidateGeneration, self.backend)
            if job.target.kind == "whole_video":
                images = await adapter.generate_candidates(
                    job.prompt,
                    image_count=job.image_count,
                    reference_image_bytes=reference_bytes,
                )
            else:
                sectioned = await adapter.generate_section_candidates(
                    [job.prompt],
                    image_count=job.image_count,
                    reference_image_bytes=reference_bytes,
                )
                # Single-prompt section job: take the first (and only)
                # bucket of candidate bytes. ``generate_section_candidates``
                # is shaped for future multi-prompt section jobs but the
                # current API submits exactly one prompt per job.
                images = sectioned[0] if sectioned else []

            candidates = await self._persist_candidates(job, images)

            async with self._lock:
                job.candidates = candidates
                job.status = "succeeded"
                job.updated_at = _now_iso()
        except asyncio.CancelledError:
            # Cancellation arrives via delete_job. We don't mutate the job
            # state because delete_job has already removed it from the
            # registry. Re-raise so the cancellation propagates correctly.
            raise
        except BaseException as exc:  # noqa: BLE001 — categorize then drop
            category = _categorize_provider_error(exc)
            logger.warning(
                "Image generation job %s failed (category=%s)",
                job.id,
                category,
            )
            # Provider authentication failures flip the capability off
            # for the rest of the process (Requirement 5.5). The
            # capability endpoint and the router's submission gate both
            # consult this flag, so subsequent submissions short-circuit
            # to 503 without ever touching the provider again.
            if isinstance(exc, ProviderAuthenticationError):
                self.capability_state.disable_for_session()
            async with self._lock:
                job.status = "failed"
                job.error_message = _GENERIC_FAILURE_MESSAGE
                job.updated_at = _now_iso()
        finally:
            async with self._lock:
                running = self._running_per_owner.get(job.owner_id)
                if running is not None:
                    running.discard(job.id)
                self._tasks.pop(job.id, None)

    async def _load_reference_bytes(
        self, job: GenerationJob
    ) -> bytes | None:
        if not job.reference_image_filename:
            return None
        stream = await self.storage.load_file(
            job.project_id, job.reference_image_filename
        )
        chunks: list[bytes] = []
        async for chunk in stream:
            chunks.append(chunk)
        return b"".join(chunks)

    async def _persist_candidates(
        self, job: GenerationJob, images: list[bytes]
    ) -> list[CandidateImage]:
        candidates: list[CandidateImage] = []
        for image_bytes in images:
            candidate_id = uuid.uuid4().hex
            filename = f"imgjobs/{job.id}/candidate-{candidate_id}.png"

            async def _chunks(payload: bytes = image_bytes):
                yield payload

            await self.storage.save_file(job.project_id, filename, _chunks())
            url = await self.storage.get_file_url(job.project_id, filename)
            candidates.append(
                CandidateImage(id=candidate_id, url=url, filename=filename)
            )
        return candidates
