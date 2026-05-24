"""Project CRUD service with optimistic concurrency and timing validation."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.config import Settings
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.base import StorageBackend
from backend.services import title_validator

PROJECT_STATE_FILENAME = "state.json"


class ProjectServiceError(Exception):
    """Base exception for ProjectService errors."""


class ProjectNotFoundError(ProjectServiceError):
    """Raised when a project does not exist."""


class VersionConflictError(ProjectServiceError):
    """Raised when an optimistic concurrency version check fails."""


class ProjectLimitExceededError(ProjectServiceError):
    """Raised when a user has reached MAX_PROJECTS_PER_USER."""


class TimingValidationError(ProjectServiceError):
    """Raised when subtitle timing violates audio_duration bounds."""


class TitleConflictError(ProjectServiceError):
    """Raised when a candidate title duplicates an existing owner-scoped title.

    The validator-layer ``TitleValidationError`` carries an ``error_code`` of
    ``title_duplicate`` for the duplicate case and is the canonical exception
    raised by ``check_uniqueness``. ``TitleConflictError`` exists as a
    ``ProjectServiceError``-typed alias so service-layer callers can catch
    duplicate-key situations via the standard service error hierarchy when
    they need to distinguish them from unrelated validator errors.
    """


class ProjectService:
    """Manages project CRUD operations backed by a StorageBackend."""

    def __init__(self, storage: StorageBackend, settings: Settings) -> None:
        self.storage = storage
        self.settings = settings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _save_state(self, state: ProjectState) -> None:
        data = state.model_dump_json(indent=2).encode()

        async def _chunks():
            yield data

        await self.storage.save_file(state.id, PROJECT_STATE_FILENAME, _chunks())

    async def _load_state(self, project_id: str) -> ProjectState:
        try:
            stream = await self.storage.load_file(project_id, PROJECT_STATE_FILENAME)
            chunks: list[bytes] = []
            async for chunk in stream:
                chunks.append(chunk)
            raw = b"".join(chunks)
            return ProjectState.model_validate_json(raw)
        except FileNotFoundError:
            raise ProjectNotFoundError(f"Project {project_id} not found")

    async def _count_user_projects(self, owner_id: str) -> int:
        """Count projects owned by *owner_id* by scanning storage.

        This walks the projects directory and loads each state file.  For a
        local-filesystem backend with a small number of projects per user this
        is perfectly fine.  A future database-backed implementation would
        replace this with a query.
        """
        base = Path(self.storage.base_dir) if hasattr(self.storage, "base_dir") else None
        if base is None:
            return 0

        projects_dir = base / "projects"
        if not projects_dir.exists():
            return 0

        count = 0
        for entry in os.listdir(projects_dir):
            state_path = projects_dir / entry / PROJECT_STATE_FILENAME
            if state_path.exists():
                try:
                    state = ProjectState.model_validate_json(state_path.read_bytes())
                    if state.owner_id == owner_id:
                        count += 1
                except Exception:
                    continue
        return count

    async def _list_owner_titles(self, owner_id: str) -> list[tuple[str, str]]:
        """Return ``[(project_id, stored_title)]`` for every project owned by *owner_id*.

        Mirrors the directory walk used by ``_count_user_projects`` and
        ``list_projects``. Used by the title validator to enforce per-owner
        uniqueness without leaking storage details into the validator. A
        future database backend would replace this with a single query
        scoped by ``owner_id``.
        """
        base = Path(self.storage.base_dir) if hasattr(self.storage, "base_dir") else None
        if base is None:
            return []

        projects_dir = base / "projects"
        if not projects_dir.exists():
            return []

        siblings: list[tuple[str, str]] = []
        for entry in os.listdir(projects_dir):
            state_path = projects_dir / entry / PROJECT_STATE_FILENAME
            if state_path.exists():
                try:
                    state = ProjectState.model_validate_json(state_path.read_bytes())
                    if state.owner_id == owner_id:
                        siblings.append((state.id, state.title))
                except Exception:
                    continue
        return siblings

    @staticmethod
    def _validate_timing_bounds(state: ProjectState) -> None:
        """Validate subtitle timing against audio_duration when known."""
        audio_dur = state.audio_duration
        if audio_dur is None:
            return

        for seg in state.subtitles:
            if seg.start_time < 0:
                raise TimingValidationError(
                    f"Subtitle {seg.id}: start_time ({seg.start_time}) must be >= 0"
                )
            if seg.end_time > audio_dur:
                raise TimingValidationError(
                    f"Subtitle {seg.id}: end_time ({seg.end_time}) exceeds "
                    f"audio_duration ({audio_dur})"
                )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_project(
        self,
        story_text: str,
        owner_id: str,
        title: str,
        voice: str = "zh-CN-XiaoxiaoNeural",
    ) -> ProjectState:
        """Create a new project.

        ``title`` is required (Requirement 1.1). The value is normalized via
        ``title_validator.validate_shape`` (Requirement 4) and checked against
        the owner's existing project titles for uniqueness (Requirement 3).
        The ``MAX_PROJECTS_PER_USER`` guard runs first so an over-limit owner
        is rejected before any title work. The trimmed title is persisted.
        """
        count = await self._count_user_projects(owner_id)
        if count >= self.settings.MAX_PROJECTS_PER_USER:
            raise ProjectLimitExceededError(
                f"User {owner_id} has reached the maximum of "
                f"{self.settings.MAX_PROJECTS_PER_USER} projects"
            )

        trimmed_title = title_validator.validate_shape(title)
        siblings = await self._list_owner_titles(owner_id)
        title_validator.check_uniqueness(
            trimmed_title, self_project_id=None, siblings=siblings
        )

        now = datetime.now(timezone.utc).isoformat()
        project_id = uuid.uuid4().hex

        state = ProjectState(
            id=project_id,
            owner_id=owner_id,
            title=trimmed_title,
            story_text=story_text,
            voice=voice,
            status="pending",
            version=1,
            pipeline_progress=PipelineProgress(stage="narration", message="Queued"),
            created_at=now,
            updated_at=now,
        )

        await self._save_state(state)
        return state

    async def get_project(self, project_id: str) -> ProjectState:
        """Load and return a project by ID."""
        return await self._load_state(project_id)

    async def update_project(
        self, project_id: str, incoming: ProjectState
    ) -> ProjectState:
        """Update project state with optimistic concurrency check.

        * The incoming version must match the stored version.
        * Subtitle timing is validated against audio_duration when known.
        * If the incoming title's trim-and-casefold key differs from the
          stored title's key, the title is re-validated for shape and
          owner-scoped uniqueness (Requirements 1.4, 3.2, 3.3, 4.x). When
          the keys match the stored title is preserved as-is so pre-existing
          titles that would no longer pass ``validate_shape`` are not
          silently rewritten (Requirement 5.4).
        * The trimmed title is always persisted on the way out.
        * On success the version is incremented and the state is persisted.
        """
        current = await self._load_state(project_id)

        if incoming.version != current.version:
            raise VersionConflictError(
                f"Version conflict: expected {current.version}, got {incoming.version}"
            )

        # Validate timing bounds
        self._validate_timing_bounds(incoming)

        stored_key = title_validator.title_key(
            title_validator.normalize(current.title)
        )
        incoming_key = title_validator.title_key(
            title_validator.normalize(incoming.title)
        )
        if incoming_key != stored_key:
            trimmed_title = title_validator.validate_shape(incoming.title)
            siblings = await self._list_owner_titles(current.owner_id)
            title_validator.check_uniqueness(
                trimmed_title,
                self_project_id=project_id,
                siblings=siblings,
            )
            incoming.title = trimmed_title
        else:
            # Keys match: preserve the existing stored title byte-for-byte.
            incoming.title = current.title

        incoming.version = current.version + 1
        incoming.updated_at = datetime.now(timezone.utc).isoformat()

        await self._save_state(incoming)
        return incoming

    async def rename_title(
        self,
        project_id: str,
        owner_id: str,
        candidate: str,
        expected_version: int,
    ) -> ProjectState:
        """Rename a project's title.

        Loads the current state, verifies ownership (mismatched ``owner_id``
        raises ``ProjectNotFoundError`` so the router maps to 404 without
        leaking existence to non-owners), checks the optimistic-concurrency
        version, then runs the candidate title through ``validate_shape``
        and owner-scoped ``check_uniqueness`` (excluding ``project_id``
        itself so a no-op rename succeeds — Requirement 3.4).

        On success the trimmed title is persisted, ``version`` is
        incremented (Requirement 6.4), and ``updated_at`` is set to the
        current UTC time (Requirement 6.3).
        """
        current = await self._load_state(project_id)

        if current.owner_id != owner_id:
            # Treat ownership mismatch as not-found so the router returns
            # 404 instead of revealing the project's existence.
            raise ProjectNotFoundError(f"Project {project_id} not found")

        if current.version != expected_version:
            raise VersionConflictError(
                f"Version conflict: expected {current.version}, got {expected_version}"
            )

        trimmed_title = title_validator.validate_shape(candidate)
        siblings = await self._list_owner_titles(owner_id)
        title_validator.check_uniqueness(
            trimmed_title,
            self_project_id=project_id,
            siblings=siblings,
        )

        current.title = trimmed_title
        current.version += 1
        current.updated_at = datetime.now(timezone.utc).isoformat()

        await self._save_state(current)
        return current

    async def delete_project(self, project_id: str) -> None:
        """Delete a project and all its files."""
        # Verify it exists first
        await self._load_state(project_id)
        await self.storage.delete_project(project_id)

    async def list_projects(self, owner_id: str) -> list[dict]:
        """Return summary dicts for all projects owned by *owner_id*.

        Summaries include: id, title, status, created_at, updated_at.
        """
        base = Path(self.storage.base_dir) if hasattr(self.storage, "base_dir") else None
        if base is None:
            return []

        projects_dir = base / "projects"
        if not projects_dir.exists():
            return []

        summaries: list[dict] = []
        for entry in os.listdir(projects_dir):
            state_path = projects_dir / entry / PROJECT_STATE_FILENAME
            if state_path.exists():
                try:
                    state = ProjectState.model_validate_json(state_path.read_bytes())
                    if state.owner_id == owner_id:
                        summaries.append(
                            {
                                "id": state.id,
                                "title": state.title,
                                "status": state.status,
                                "version": state.version,
                                "created_at": state.created_at,
                                "updated_at": state.updated_at,
                            }
                        )
                except Exception:
                    continue
        return summaries
