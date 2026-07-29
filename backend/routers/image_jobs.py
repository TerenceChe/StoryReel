"""HTTP routing for AI background image-generation jobs.

This module hosts the four owner-scoped job endpoints described in the
design (`/projects/{project_id}/image-jobs[/...]`). Task 9.1 lays down
the router skeleton, the shared ownership/availability helpers, and the
exception → HTTP status mapper that every concrete handler in the
follow-up tasks (9.2, 10.1, 11.1, 12.1) will share. The handlers
themselves are added in those subsequent tasks.

Design constraints baked into this module from the start:

* The router depends only on the abstract
  :class:`backend.models.image_gen.ImageGenerationBackend`. It MUST NOT
  import any concrete provider SDK (e.g. ``openai``,
  ``stability_sdk``); a static import-check test (Task 15.3) enforces
  that. Provider selection happens once at startup in
  :mod:`backend.dependencies`; the router is provider-agnostic by design
  (Requirement 8.3).
* User-facing detail strings are **operator-opaque**: no provider names,
  no environment variable names, no README pointers, no provider stack
  traces. Property 1 in the design pins this and Requirements 1.4, 1.7,
  and 5.4 enforce it. The :data:`_OPAQUE_*` constants below are the
  canonical strings every helper in this module returns.
* The capability gate consults two signals — the bound backend's
  runtime type AND the process-level
  :class:`backend.services.image_capability_state.CapabilityState`
  flag — so a provider-configured deployment that has observed a
  provider auth failure mid-session is still reported as unavailable
  (Requirement 5.5).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel

from backend.auth.middleware import get_owner_id, verify_project_ownership
from backend.config import Settings
from backend.dependencies import (
    get_image_backend,
    get_job_manager,
    get_project_service,
    get_settings,
    get_storage,
)
from backend.models.image_gen import (
    DisabledImageBackend,
    ImageGenerationBackend,
    ImageGenerationDisabledError,
)
from backend.models.image_jobs import GenerationJob, GenerationTarget
from backend.models.project import ProjectState
from backend.persistence.base import StorageBackend
from backend.services.image_capability_state import capability_state
from backend.services.image_job_errors import (
    ImageJobCandidateNotFoundError,
    ImageJobConcurrencyError,
    ImageJobInvalidStateError,
    ImageJobNotFoundError,
    ImageJobValidationError,
)
from backend.services.image_job_service import JobManager
from backend.services.project_service import (
    ProjectNotFoundError,
    ProjectService,
    VersionConflictError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/projects/{project_id}/image-jobs",
    tags=["image-jobs"],
)


# ---------------------------------------------------------------------------
# Operator-opaque user-facing strings
# ---------------------------------------------------------------------------
#
# Every ``HTTPException.detail`` in this module is one of these constants
# (or a parameterized string built from configured numeric limits, which
# are not operator credentials). The constants are deliberately bland —
# no provider names, no env var names, no setup hints — so the response
# safety property test (Task 13.2) can assert their presence without
# tripping on operator-facing details.

_OPAQUE_UNAVAILABLE = "AI background generation is currently unavailable"
_OPAQUE_PROJECT_NOT_FOUND = "Project not found"
_OPAQUE_JOB_NOT_FOUND = "Job not found"
_OPAQUE_VERSION_CONFLICT = "Version conflict"
_OPAQUE_INVALID_STATE = "Operation not permitted in the job's current state"
_OPAQUE_VALIDATION = "Invalid request"
_OPAQUE_CANDIDATE_NOT_FOUND = "Unknown candidate"
_OPAQUE_CONCURRENCY = "Too many image-generation jobs in flight"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _load_owned_project(
    project_id: str,
    owner_id: str,
    project_service: ProjectService,
) -> ProjectState:
    """Load a project and verify the caller owns it.

    Mirrors the helper of the same name in
    :mod:`backend.routers.projects` so the two routers share identical
    not-found / forbidden semantics. ``ProjectNotFoundError`` becomes a
    404 with a generic detail; ownership mismatches raise 403 via
    :func:`backend.auth.middleware.verify_project_ownership`.
    """
    try:
        project = await project_service.get_project(project_id)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_OPAQUE_PROJECT_NOT_FOUND,
        )
    verify_project_ownership(project.owner_id, owner_id)
    return project


def _require_image_generation(
    backend: ImageGenerationBackend = Depends(get_image_backend),
) -> ImageGenerationBackend:
    """Gate that rejects requests when image generation is unavailable.

    Returns the bound backend on success so handlers that need it can
    declare this dependency once. Raises 503 with the generic
    :data:`_OPAQUE_UNAVAILABLE` detail when either the bound backend is
    the disabled fallback or the process-level capability flag has been
    flipped off (Requirement 5.5).
    """
    if isinstance(backend, DisabledImageBackend) or not capability_state.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_OPAQUE_UNAVAILABLE,
        )
    return backend


# ---------------------------------------------------------------------------
# Exception → HTTP status mapping
# ---------------------------------------------------------------------------
#
# Concrete handlers added in Tasks 9.2 / 10.1 / 11.1 / 12.1 wrap their
# JobManager / ProjectService calls in ``try / except`` blocks and feed
# any caught exception into ``_map_exception_to_http``. Centralizing the
# mapping here keeps the operator-opaque detail strings in one place and
# makes the response-safety property test (Task 13.2) easy to write.
#
# The mapping is intentionally exhaustive for the exception types the
# JobManager and ProjectService raise in the image-jobs flow. Any
# unmapped exception is propagated to FastAPI's default 500 handler.

_STATUS_BY_EXCEPTION: tuple[tuple[type[Exception], int, str], ...] = (
    (ImageJobConcurrencyError, status.HTTP_429_TOO_MANY_REQUESTS, _OPAQUE_CONCURRENCY),
    (ImageJobInvalidStateError, status.HTTP_409_CONFLICT, _OPAQUE_INVALID_STATE),
    (ImageJobCandidateNotFoundError, status.HTTP_422_UNPROCESSABLE_ENTITY, _OPAQUE_CANDIDATE_NOT_FOUND),
    (ImageJobValidationError, status.HTTP_422_UNPROCESSABLE_ENTITY, _OPAQUE_VALIDATION),
    (ImageJobNotFoundError, status.HTTP_404_NOT_FOUND, _OPAQUE_JOB_NOT_FOUND),
    (ImageGenerationDisabledError, status.HTTP_503_SERVICE_UNAVAILABLE, _OPAQUE_UNAVAILABLE),
    (ProjectNotFoundError, status.HTTP_404_NOT_FOUND, _OPAQUE_PROJECT_NOT_FOUND),
    (VersionConflictError, status.HTTP_409_CONFLICT, _OPAQUE_VERSION_CONFLICT),
)


def _map_exception_to_http(exc: Exception) -> HTTPException:
    """Translate a domain exception into an :class:`HTTPException`.

    The returned exception always carries a generic, operator-opaque
    ``detail``. The original exception's message is **not** copied into
    the detail — provider error text could carry sensitive content, and
    Property 1 in the design forbids leaking it to clients.
    """
    for exc_type, http_status, detail in _STATUS_BY_EXCEPTION:
        if isinstance(exc, exc_type):
            return HTTPException(status_code=http_status, detail=detail)
    # Unknown exception: re-raise so FastAPI returns a 500. We don't
    # swallow it into a generic HTTPException because that would mask
    # programmer errors during development.
    raise exc


__all__ = [
    "router",
    "_load_owned_project",
    "_require_image_generation",
    "_map_exception_to_http",
    "ImageJobSubmitRequest",
    "ImageJobSubmitResponse",
    "ApplyCandidateRequest",
    "submit_image_job",
    "attach_reference_image",
    "get_image_job",
    "apply_candidate",
]


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ImageJobSubmitRequest(BaseModel):
    """Request body for ``POST /projects/{project_id}/image-jobs``.

    ``image_count`` defaults to 1 to honor Requirement 4.2 (omitting the
    field selects single-candidate generation). The numeric range
    enforcement (Requirement 4.1, 4.3) lives in the service layer so the
    bound ``MAX_IMAGES_PER_JOB`` setting drives the cap rather than a
    field constraint baked into the schema; the JobManager raises
    :class:`ImageJobValidationError` which the shared exception mapper
    converts to 422.

    The schema is intentionally generic — no provider names, no env var
    names. The 422 errors raised by the JobManager carry the same
    operator-opaque ``_OPAQUE_VALIDATION`` detail used elsewhere in this
    module.
    """

    prompt: str
    image_count: int = 1
    target: GenerationTarget


class ImageJobSubmitResponse(BaseModel):
    """Response body for ``POST /projects/{project_id}/image-jobs``.

    Mirrors the design's "Response 202" example: only the ``job_id`` and
    initial ``status`` fields. The ``status`` is always ``"pending"`` at
    submit time — the worker hasn't started yet. Subsequent transitions
    (``running`` / ``succeeded`` / ``failed``) are observable via the
    status route added in Task 11.1.
    """

    job_id: str
    status: str


class ApplyCandidateRequest(BaseModel):
    """Request body for
    ``POST /projects/{project_id}/image-jobs/{job_id}/apply``.

    ``version`` is the project's current ``version`` value, used by
    :meth:`ProjectService.update_project` for the existing optimistic
    concurrency check. A stale value surfaces as 409 via
    ``VersionConflictError`` and the shared exception mapper.

    Validation of the candidate id (must belong to the named job) and
    of the job's lifecycle state (must be ``succeeded``) lives in the
    JobManager so the same error semantics apply to any future caller.
    """

    candidate_id: str
    version: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ImageJobSubmitResponse,
)
async def submit_image_job(
    project_id: str,
    body: ImageJobSubmitRequest,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    job_manager: JobManager = Depends(get_job_manager),
    _backend: ImageGenerationBackend = Depends(_require_image_generation),
) -> ImageJobSubmitResponse:
    """Submit a new image-generation job for the project.

    Returns 202 with ``{job_id, status}`` on acceptance. The handler:

    1. Short-circuits with 503 via :func:`_require_image_generation` when
       the bound backend is the disabled fallback or the process-level
       capability has been flipped off (Requirement 5.5, 8.4).
    2. Loads the project and verifies ownership; non-owners receive 403
       and unknown projects receive 404 (Requirement 6.1, 6.2).
    3. Hands control to :meth:`JobManager.submit`, which validates
       ``image_count`` against ``MAX_IMAGES_PER_JOB`` (Requirement 4.1,
       4.3), validates the ``target`` indices against the project's
       subtitles (Requirement 3.2, 3.3, 3.4), and enforces the per-user
       concurrency cap (Requirement 7.2).
    4. Maps any domain exception via :func:`_map_exception_to_http` so
       every error response carries the canonical operator-opaque
       detail string. The structured ``error_code`` shape used by the
       project-title routes is **not** reused here — image-job errors
       are conveyed as ordinary FastAPI ``{"detail": "..."}`` payloads
       (Property 1 / Requirement 5.4).
    """
    project = await _load_owned_project(project_id, owner_id, project_service)

    try:
        job = await job_manager.submit(
            owner_id,
            project,
            prompt=body.prompt,
            image_count=body.image_count,
            target=body.target,
        )
    except HTTPException:
        # Should never happen — the JobManager raises domain exceptions,
        # not HTTPException — but if a future refactor inverts that we
        # want the original HTTPException to surface untouched rather
        # than be swallowed by ``_map_exception_to_http``'s re-raise.
        raise
    except Exception as exc:
        raise _map_exception_to_http(exc)

    return ImageJobSubmitResponse(job_id=job.id, status=job.status)


# ---------------------------------------------------------------------------
# Reference upload constants
# ---------------------------------------------------------------------------
#
# Both the Content-Type and the filename extension must indicate PNG or
# JPEG (Requirement 9.3). We accept the same two MIME types and the three
# extensions used by the existing project-background upload path so the
# reference upload validator behaves identically from the user's
# perspective. Unlike the project-background upload, **both** checks must
# pass (no fallback) — the design and Property 2 pin this stricter rule.

_REFERENCE_ALLOWED_TYPES = {"image/png", "image/jpeg"}
_REFERENCE_ALLOWED_EXTS = (".png", ".jpg", ".jpeg")


@router.post("/{job_id}/reference")
async def attach_reference_image(
    project_id: str,
    job_id: str,
    file: UploadFile = File(...),
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    job_manager: JobManager = Depends(get_job_manager),
    storage: StorageBackend = Depends(get_storage),
    app_settings: Settings = Depends(get_settings),
) -> dict:
    """Attach an optional reference image to a pending image-generation job.

    Validates the upload, persists the bytes via ``StorageBackend`` under
    ``imgjobs/{job_id}/reference.{ext}``, and records the resulting
    filename on the in-memory job through
    :meth:`JobManager.attach_reference`.

    Validation rules (Requirements 2.2, 2.3, 9.3):

    * ``Content-Type`` MUST be in :data:`_REFERENCE_ALLOWED_TYPES` AND the
      filename extension MUST be in :data:`_REFERENCE_ALLOWED_EXTS`. Both
      checks must pass; failing either is a 422. This is stricter than
      the existing ``/projects/{id}/background`` upload (which falls back
      from one to the other) because Requirement 9.3 explicitly mandates
      the conjunction.
    * Body size MUST be ≤ ``MAX_REFERENCE_IMAGE_SIZE_MB``. Failing this
      is a 413.

    State rules:

    * The job must exist and belong to the caller. Unknown jobs and jobs
      owned by someone else surface as 404 (Requirement 6.3).
    * The job must still be in ``pending`` state. Once the worker has
      started, the reference image is too late to influence generation,
      so we reject with 409 (mapped from
      :class:`ImageJobInvalidStateError`).

    Every error ``detail`` is operator-opaque — no provider names, no
    environment variable names, no setup hints — matching Requirement
    9.5 and Property 1 from the design.
    """
    # Ownership / not-found check on the project itself. ``_load_owned_project``
    # already maps ``ProjectNotFoundError`` to 404 with a generic detail,
    # so a request to a project the caller does not own surfaces as 403
    # via ``verify_project_ownership`` while a missing project surfaces
    # as 404. Both are operator-opaque.
    await _load_owned_project(project_id, owner_id, project_service)

    # Content-type AND extension validation. The conjunction (rather than
    # the fallback used by the project-background upload) is mandated by
    # Requirement 9.3 and Property 2. The 422 detail intentionally avoids
    # echoing the rejected values back — Property 1 forbids leaking
    # sensitive content via responses, and a malicious filename could
    # carry control characters or operator-relevant strings.
    ct = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    ct_ok = ct in _REFERENCE_ALLOWED_TYPES
    ext_ok = name.endswith(_REFERENCE_ALLOWED_EXTS)
    if not (ct_ok and ext_ok):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only PNG and JPEG reference images are accepted",
        )

    # Read the body into memory and enforce the size cap. Reference
    # images are bounded by ``MAX_REFERENCE_IMAGE_SIZE_MB`` (default 50
    # MB), which matches the existing ``/projects/{id}/background``
    # upload bound, so memory pressure is the same as for a project
    # background upload. Streaming the size check would let us reject
    # earlier, but ``StorageBackend.save_file`` already takes an async
    # iterator and the existing background route uses the same
    # read-then-check pattern; matching that pattern keeps the two
    # upload paths consistent for reviewers.
    max_bytes = app_settings.MAX_REFERENCE_IMAGE_SIZE_MB * 1024 * 1024
    data = await file.read()
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Reference image exceeds "
                f"{app_settings.MAX_REFERENCE_IMAGE_SIZE_MB} MB"
            ),
        )

    # Pick a stable extension for the persisted file. The validation
    # above guarantees the filename ends with one of the allowed
    # extensions, so the suffix lookup below is safe. We normalize
    # ``.jpeg`` → ``.jpg`` so the stored filename uses a single
    # canonical form per MIME type, making cleanup logic simpler.
    if name.endswith(".png"):
        ext = "png"
    else:
        ext = "jpg"
    reference_filename = f"imgjobs/{job_id}/reference.{ext}"

    async def _chunks(payload: bytes = data):
        yield payload

    await storage.save_file(project_id, reference_filename, _chunks())

    try:
        await job_manager.attach_reference(
            owner_id, job_id, reference_filename
        )
    except HTTPException:
        raise
    except Exception as exc:
        # ``ImageJobNotFoundError`` → 404 and ``ImageJobInvalidStateError``
        # → 409 are both handled by the shared mapper, which carries the
        # operator-opaque detail strings. Any other exception is
        # programmer error and propagates as a 500.
        raise _map_exception_to_http(exc)

    return {"detail": "Reference image attached"}


@router.get(
    "/{job_id}",
    response_model=GenerationJob,
)
async def get_image_job(
    project_id: str,
    job_id: str,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> GenerationJob:
    """Return the current state of an image-generation job.

    Owner-only. Returns the :class:`GenerationJob` Pydantic instance via
    Pydantic serialization, so any downstream transition observed on the
    in-memory job (``pending`` → ``running`` → ``succeeded`` | ``failed``)
    is reflected in subsequent reads (Requirements 5.2, 5.3).

    For ``failed`` jobs the ``error_message`` is the generic
    operator-opaque string set by the JobManager — provider error
    details and stack traces never reach the client (Requirement 5.4 /
    Property 1).

    Ownership is enforced before the job lookup: the project itself is
    loaded via :func:`_load_owned_project` so a non-Owner caller for a
    real project is rejected with 403, and an unknown project is rejected
    with 404, both with the canonical operator-opaque detail strings.
    Once the project's ownership is confirmed, the JobManager is
    consulted via :meth:`JobManager.get`, which raises
    :class:`ImageJobNotFoundError` when the job id is unknown OR when
    the caller is not the job's owner. The shared exception mapper
    converts that exception to 404 (Requirement 6.3).
    """
    await _load_owned_project(project_id, owner_id, project_service)

    try:
        return await job_manager.get(owner_id, job_id)
    except HTTPException:
        # Defense in depth: should never happen because the JobManager
        # raises domain exceptions, not HTTPException, but if a future
        # refactor inverts that we want the original HTTPException to
        # surface untouched rather than be swallowed by the mapper's
        # re-raise of unknown exceptions.
        raise
    except Exception as exc:
        raise _map_exception_to_http(exc)


@router.post(
    "/{job_id}/apply",
    response_model=ProjectState,
)
async def apply_candidate(
    project_id: str,
    job_id: str,
    body: ApplyCandidateRequest,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    job_manager: JobManager = Depends(get_job_manager),
) -> ProjectState:
    """Apply a chosen candidate image to the project's background fields.

    The handler is a thin wrapper around :meth:`JobManager.apply_candidate`,
    which performs the actual mutation and persistence:

    * ``whole_video`` target — sets ``project.background_image`` to the
      chosen candidate's URL and leaves ``section_backgrounds`` untouched
      so existing per-section assignments survive (Requirement 3.5).
    * ``section`` target — upserts a
      :class:`~backend.models.project.SectionBackground` keyed on
      ``(start_index, end_index)`` and leaves ``background_image``
      untouched so the whole-video fallback stays in place
      (Requirement 3.6).

    Persistence flows through :meth:`ProjectService.update_project` so the
    optimistic concurrency check on ``version`` runs in the canonical
    path. A stale ``version`` surfaces as 409 via
    :class:`VersionConflictError` mapped by the shared exception mapper.

    Error codes (all via :func:`_map_exception_to_http`):

    * 404 — unknown project, or unknown job for this caller (the
      JobManager raises :class:`ImageJobNotFoundError` both when the
      ``job_id`` is unknown and when the caller is not the job's owner,
      so cross-tenant probing surfaces as 404 rather than confirming a
      job exists; ownership of the project itself is checked first via
      :func:`_load_owned_project` and rejects with 403).
    * 409 — job is not in ``succeeded`` state
      (:class:`ImageJobInvalidStateError`) or the supplied ``version``
      doesn't match the stored project (:class:`VersionConflictError`).
    * 422 — ``candidate_id`` does not belong to this job
      (:class:`ImageJobCandidateNotFoundError`).

    Note: the design's API-surface table calls out "Apply with bad
    ``candidate_id`` | 422" and reuses the existing 409 mapping for stale
    ``version``. The "job not in succeeded state" precondition is mapped
    to 409 here because that's how :class:`ImageJobInvalidStateError`
    flows through the shared mapper — the same status used for the
    "reference attached after job started" sibling case in the design.
    """
    # Ownership / not-found check on the project itself. The JobManager's
    # apply_candidate also re-validates ownership against the job, but
    # going through ``_load_owned_project`` first keeps the response
    # codes consistent with the other routes in this module: 403 for a
    # non-Owner caller targeting an existing project, 404 for an unknown
    # project, both with operator-opaque detail strings.
    await _load_owned_project(project_id, owner_id, project_service)

    try:
        return await job_manager.apply_candidate(
            owner_id,
            job_id,
            candidate_id=body.candidate_id,
            version=body.version,
        )
    except HTTPException:
        # Defense in depth — the JobManager raises domain exceptions, not
        # HTTPException. If a future refactor inverts that we want the
        # original HTTPException to surface untouched.
        raise
    except Exception as exc:
        raise _map_exception_to_http(exc)
