"""Project CRUD, SSE, and export API endpoints.

Provides:
- POST   /projects                      — create project, validate text, start pipeline
- GET    /projects                      — list user's projects (summaries)
- GET    /projects/{id}                 — get full project state
- PUT    /projects/{id}                 — update project state (optimistic concurrency)
- DELETE /projects/{id}                 — delete project and files
- GET    /projects/{id}/status          — SSE stream for pipeline progress
- POST   /projects/{id}/export          — trigger async export, return 202
- GET    /projects/{id}/export/status   — SSE stream for export progress
- GET    /projects/{id}/export/download — stream exported MP4 file
- POST   /projects/{id}/retry           — retry from failed stage
"""

from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, field_validator
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile, File, status
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from backend.auth.middleware import get_owner_id, verify_project_ownership
from backend.config import Settings
from backend.dependencies import get_pipeline_service, get_project_service, get_settings, get_storage
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.base import StorageBackend
from backend.services.pipeline_service import (
    ConcurrencyLimitError,
    InvalidRetryError,
    PipelineService,
)
from backend.services.project_service import (
    ProjectLimitExceededError,
    ProjectNotFoundError,
    ProjectService,
    TimingValidationError,
    VersionConflictError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    story_text: str
    voice: str = "zh-CN-XiaoxiaoNeural"
    title: str | None = None

    @field_validator("story_text")
    @classmethod
    def story_text_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Story text must not be empty or whitespace-only")
        return v


class ProjectSummary(BaseModel):
    id: str
    title: str
    status: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest,
    background_tasks: BackgroundTasks,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    pipeline_service: PipelineService = Depends(get_pipeline_service),
) -> ProjectState:
    """Create a new project and kick off the pipeline in the background."""
    try:
        project = await project_service.create_project(
            story_text=body.story_text,
            owner_id=owner_id,
            voice=body.voice,
            title=body.title,
        )
    except ProjectLimitExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )

    background_tasks.add_task(pipeline_service.run_pipeline, project.id)
    return project


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
) -> list[dict]:
    """Return summaries for all projects owned by the authenticated user."""
    return await project_service.list_projects(owner_id)


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
) -> ProjectState:
    """Return the full project state."""
    project = await _load_owned_project(project_id, owner_id, project_service)
    return project


@router.put("/{project_id}")
async def update_project(
    project_id: str,
    body: ProjectState,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
) -> ProjectState:
    """Update project state with optimistic concurrency check."""
    # Verify ownership on the *stored* project first
    await _load_owned_project(project_id, owner_id, project_service)

    try:
        updated = await project_service.update_project(project_id, body)
    except VersionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except TimingValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    return updated


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
) -> None:
    """Delete a project and all its files."""
    await _load_owned_project(project_id, owner_id, project_service)
    await project_service.delete_project(project_id)


# ---------------------------------------------------------------------------
# SSE: Pipeline status
# ---------------------------------------------------------------------------


@router.get("/{project_id}/status")
async def pipeline_status_sse(
    project_id: str,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
) -> EventSourceResponse:
    """SSE stream for pipeline progress.

    - On connect: sends current pipeline stage immediately.
    - During processing: sends an event on each stage change.
    - Keepalive: comment-only ping every 15 seconds.
    - On complete/error: sends final event and closes.
    """
    project = await _load_owned_project(project_id, owner_id, project_service)

    async def _event_generator():
        # Send current state immediately on connect
        state = project
        yield _sse_progress_event(state.pipeline_progress)

        # If already terminal, close right away
        if state.pipeline_progress.stage in ("complete", "error"):
            return

        last_stage = state.pipeline_progress.stage

        while True:
            await asyncio.sleep(1)
            try:
                state = await project_service.get_project(project_id)
            except ProjectNotFoundError:
                return

            current_stage = state.pipeline_progress.stage

            if current_stage != last_stage:
                yield _sse_progress_event(state.pipeline_progress)
                last_stage = current_stage

                if current_stage in ("complete", "error"):
                    return

    return EventSourceResponse(
        _event_generator(),
        ping=15,
        ping_message_factory=lambda: "",  # comment-only keepalive
    )


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------


@router.post("/{project_id}/export", status_code=status.HTTP_202_ACCEPTED)
async def trigger_export(
    project_id: str,
    background_tasks: BackgroundTasks,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    pipeline_service: PipelineService = Depends(get_pipeline_service),
) -> dict:
    """Trigger async video export. Returns 202 Accepted."""
    await _load_owned_project(project_id, owner_id, project_service)

    try:
        background_tasks.add_task(pipeline_service.export_video, project_id)
    except ConcurrencyLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )

    return {"detail": "Export started", "project_id": project_id}


@router.get("/{project_id}/export/status")
async def export_status_sse(
    project_id: str,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
) -> EventSourceResponse:
    """SSE stream for export progress.

    Same lifecycle as pipeline status SSE: sends current state on connect,
    events on stage changes, comment-only keepalive every 15s, closes on
    complete/error.
    """
    project = await _load_owned_project(project_id, owner_id, project_service)

    async def _event_generator():
        state = project
        yield _sse_progress_event(state.pipeline_progress)

        if state.status in ("exported", "error", "ready"):
            # Already done or not exporting — send and close
            return

        last_stage = state.pipeline_progress.stage

        while True:
            await asyncio.sleep(1)
            try:
                state = await project_service.get_project(project_id)
            except ProjectNotFoundError:
                return

            current_stage = state.pipeline_progress.stage

            if current_stage != last_stage:
                yield _sse_progress_event(state.pipeline_progress)
                last_stage = current_stage

                if current_stage in ("complete", "error"):
                    return

            # Also close if status flipped to exported
            if state.status == "exported":
                if current_stage != "complete":
                    yield _sse_progress_event(state.pipeline_progress)
                return

    return EventSourceResponse(
        _event_generator(),
        ping=15,
        ping_message_factory=lambda: "",
    )


@router.get("/{project_id}/export/download")
async def download_export(
    project_id: str,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    pipeline_service: PipelineService = Depends(get_pipeline_service),
) -> StreamingResponse:
    """Stream the exported MP4 file."""
    project = await _load_owned_project(project_id, owner_id, project_service)

    if not project.export_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No export available for this project",
        )

    try:
        stream = await pipeline_service.storage.load_file(project_id, "export.mp4")
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Export file not found",
        )

    return StreamingResponse(
        stream,
        media_type="video/mp4",
        headers={
            "Content-Disposition": f'attachment; filename="{project_id}_export.mp4"',
        },
    )


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


@router.post("/{project_id}/retry")
async def retry_pipeline(
    project_id: str,
    background_tasks: BackgroundTasks,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    pipeline_service: PipelineService = Depends(get_pipeline_service),
) -> dict:
    """Retry pipeline from the failed stage.

    Only valid when project status is "error" — returns 422 otherwise.
    """
    project = await _load_owned_project(project_id, owner_id, project_service)

    if project.status != "error":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot retry: project status is '{project.status}', expected 'error'",
        )

    try:
        background_tasks.add_task(pipeline_service.retry_pipeline, project_id)
    except ConcurrencyLimitError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        )

    return {"detail": "Retry started", "project_id": project_id}


# ---------------------------------------------------------------------------
# Media & Upload
# ---------------------------------------------------------------------------

_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg"}


@router.get("/{project_id}/media/{filename}")
async def serve_media(
    project_id: str,
    filename: str,
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    storage: StorageBackend = Depends(get_storage),
) -> StreamingResponse:
    """Serve a media file from the project directory.

    Rejects filenames containing path traversal characters (.., /, \\).
    """
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename",
        )

    await _load_owned_project(project_id, owner_id, project_service)

    try:
        stream = await storage.load_file(project_id, filename)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {filename} not found",
        )

    # Guess content type from extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    media_types = {
        "mp4": "video/mp4",
        "mp3": "audio/mpeg",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "srt": "text/plain",
    }
    content_type = media_types.get(ext, "application/octet-stream")

    return StreamingResponse(stream, media_type=content_type)


@router.post("/{project_id}/background")
async def upload_background(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    owner_id: str = Depends(get_owner_id),
    project_service: ProjectService = Depends(get_project_service),
    storage: StorageBackend = Depends(get_storage),
    app_settings: Settings = Depends(get_settings),
) -> dict:
    """Upload a custom background image (PNG/JPG only).

    Validates format and enforces MAX_UPLOAD_SIZE_MB limit.
    """
    await _load_owned_project(project_id, owner_id, project_service)

    # Validate content type
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file format: {file.content_type}. Only PNG and JPG are accepted.",
        )

    # Read file and check size
    max_bytes = app_settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    data = await file.read()
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum upload size is {app_settings.MAX_UPLOAD_SIZE_MB} MB.",
        )

    # Save via storage backend
    async def _chunks():
        yield data

    await storage.save_file(project_id, "background.png", _chunks())

    # Update project state
    state = await project_service.get_project(project_id)
    bg_url = await storage.get_file_url(project_id, "background.png")
    state.background_image = bg_url
    await project_service._save_state(state)

    return {"detail": "Background image uploaded", "background_image": bg_url}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_progress_event(progress: PipelineProgress) -> dict:
    """Format a PipelineProgress as an SSE event dict."""
    return {
        "event": progress.stage,
        "data": progress.model_dump_json(),
    }


async def _load_owned_project(
    project_id: str,
    owner_id: str,
    project_service: ProjectService,
) -> ProjectState:
    """Load a project and verify the caller owns it. Raises appropriate
    HTTP exceptions on not-found or forbidden."""
    try:
        project = await project_service.get_project(project_id)
    except ProjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    verify_project_ownership(project.owner_id, owner_id)
    return project
