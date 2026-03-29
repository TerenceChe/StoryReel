"""Pipeline orchestration service.

Coordinates narration → subtitles → video assembly, tracks progress for SSE
streaming, and enforces per-user concurrency limits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from backend.config import Settings
from backend.models.project import PipelineProgress, ProjectState
from backend.persistence.base import StorageBackend
from backend.pipeline.image_gen import generate_black_image
from backend.pipeline.narration import generate_narration
from backend.pipeline.subtitles import build_subtitle_segments, generate_timestamps
from backend.pipeline.video import create_video_with_subtitles
from backend.services.project_service import ProjectService

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Raised when a pipeline operation fails."""


class ConcurrencyLimitError(PipelineError):
    """Raised when a user exceeds MAX_CONCURRENT_PIPELINES_PER_USER."""


class InvalidRetryError(PipelineError):
    """Raised when retry is attempted on a non-error project."""


class PipelineService:
    """Orchestrates the story-to-video pipeline and video export."""

    def __init__(
        self,
        storage: StorageBackend,
        project_service: ProjectService,
        settings: Settings,
    ) -> None:
        self.storage = storage
        self.project_service = project_service
        self.settings = settings
        # Track running pipelines per user: owner_id -> set of project_ids
        self._running: dict[str, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Concurrency tracking
    # ------------------------------------------------------------------

    async def _acquire_slot(self, owner_id: str, project_id: str) -> None:
        """Reserve a pipeline slot for the user. Raises ConcurrencyLimitError
        if the user already has MAX_CONCURRENT_PIPELINES_PER_USER running."""
        async with self._lock:
            running = self._running[owner_id]
            if len(running) >= self.settings.MAX_CONCURRENT_PIPELINES_PER_USER:
                raise ConcurrencyLimitError(
                    f"User {owner_id} already has "
                    f"{len(running)} pipelines running "
                    f"(max {self.settings.MAX_CONCURRENT_PIPELINES_PER_USER})"
                )
            running.add(project_id)

    async def _release_slot(self, owner_id: str, project_id: str) -> None:
        async with self._lock:
            self._running[owner_id].discard(project_id)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    async def _update_progress(
        self,
        project_id: str,
        stage: str,
        message: str,
        *,
        extra: dict | None = None,
    ) -> ProjectState:
        """Load current state, update progress fields, and save."""
        state = await self.project_service.get_project(project_id)
        state.pipeline_progress = PipelineProgress(stage=stage, message=message)
        if extra:
            for key, value in extra.items():
                setattr(state, key, value)
        state.updated_at = datetime.now(timezone.utc).isoformat()
        # Bypass version check — pipeline is the sole writer during processing
        await self.project_service._save_state(state)
        return state

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    async def run_pipeline(
        self,
        project_id: str,
        on_progress: Callable[[PipelineProgress], None] | None = None,
    ) -> None:
        """Run the full pipeline: narration → subtitles → preview video.

        This is designed to be called as a background task. It updates the
        project state at each stage so SSE consumers can track progress.
        """
        state = await self.project_service.get_project(project_id)
        owner_id = state.owner_id

        await self._acquire_slot(owner_id, project_id)
        try:
            await self._run_pipeline_stages(
                project_id, state, on_progress, start_from="narration"
            )
        except Exception as exc:
            logger.exception("Pipeline failed for project %s", project_id)
            await self._update_progress(
                project_id,
                "error",
                str(exc),
                extra={"status": "error"},
            )
            if on_progress:
                on_progress(PipelineProgress(stage="error", message=str(exc)))
        finally:
            await self._release_slot(owner_id, project_id)

    async def _run_pipeline_stages(
        self,
        project_id: str,
        state: ProjectState,
        on_progress: Callable[[PipelineProgress], None] | None,
        start_from: str,
    ) -> None:
        """Execute pipeline stages starting from *start_from*."""
        stages = ["narration", "subtitles", "assembly"]
        start_idx = stages.index(start_from) if start_from in stages else 0

        # We use a temp dir for intermediate files, then persist via storage
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path: str | None = None
            audio_duration: float | None = state.audio_duration

            # If resuming past narration, we need the audio file locally
            if start_idx > 0:
                audio_path = await self._download_audio(project_id, tmp_dir)

            for stage in stages[start_idx:]:
                if stage == "narration":
                    audio_path, audio_duration = await self._stage_narration(
                        project_id, state, tmp_dir, on_progress
                    )
                elif stage == "subtitles":
                    await self._stage_subtitles(
                        project_id, audio_path, tmp_dir, on_progress  # type: ignore[arg-type]
                    )
                elif stage == "assembly":
                    await self._stage_assembly(
                        project_id, audio_path, tmp_dir, on_progress  # type: ignore[arg-type]
                    )

        # Mark complete
        await self._update_progress(
            project_id,
            "complete",
            "Pipeline finished",
            extra={"status": "ready"},
        )
        if on_progress:
            on_progress(PipelineProgress(stage="complete", message="Pipeline finished"))

    async def _download_audio(self, project_id: str, tmp_dir: str) -> str:
        """Download the project's narration audio to a local temp file."""
        local_path = os.path.join(tmp_dir, "narration.mp3")
        stream = await self.storage.load_file(project_id, "narration.mp3")
        with open(local_path, "wb") as f:
            async for chunk in stream:
                f.write(chunk)
        return local_path

    # ------------------------------------------------------------------
    # Individual stages
    # ------------------------------------------------------------------

    async def _stage_narration(
        self,
        project_id: str,
        state: ProjectState,
        tmp_dir: str,
        on_progress: Callable[[PipelineProgress], None] | None,
    ) -> tuple[str, float]:
        """Generate narration audio."""
        progress = PipelineProgress(stage="narration", message="Generating narration…")
        await self._update_progress(
            project_id, "narration", progress.message, extra={"status": "processing"}
        )
        if on_progress:
            on_progress(progress)

        audio_path = os.path.join(tmp_dir, "narration.mp3")

        # Run CPU-bound work in a thread
        loop = asyncio.get_event_loop()
        _, duration = await loop.run_in_executor(
            None, generate_narration, state.story_text, audio_path, state.voice
        )

        # Persist audio via storage backend
        await self.storage.save_file_from_path(project_id, "narration.mp3", audio_path)

        # Update state with audio info
        audio_url = await self.storage.get_file_url(project_id, "narration.mp3")
        await self._update_progress(
            project_id,
            "narration",
            "Narration complete",
            extra={"audio_url": audio_url, "audio_duration": duration},
        )

        return audio_path, duration

    async def _stage_subtitles(
        self,
        project_id: str,
        audio_path: str,
        tmp_dir: str,
        on_progress: Callable[[PipelineProgress], None] | None,
    ) -> None:
        """Generate subtitle timestamps from audio."""
        progress = PipelineProgress(stage="subtitles", message="Generating subtitles…")
        await self._update_progress(project_id, "subtitles", progress.message)
        if on_progress:
            on_progress(progress)

        loop = asyncio.get_event_loop()
        raw_segments = await loop.run_in_executor(
            None, generate_timestamps, audio_path
        )
        subtitle_segments = build_subtitle_segments(raw_segments)

        # Persist subtitles in project state
        state = await self.project_service.get_project(project_id)
        state.subtitles = subtitle_segments
        state.updated_at = datetime.now(timezone.utc).isoformat()
        await self.project_service._save_state(state)

    async def _stage_assembly(
        self,
        project_id: str,
        audio_path: str,
        tmp_dir: str,
        on_progress: Callable[[PipelineProgress], None] | None,
    ) -> None:
        """Assemble preview video from audio + subtitles + background."""
        progress = PipelineProgress(stage="assembly", message="Assembling video…")
        await self._update_progress(project_id, "assembly", progress.message)
        if on_progress:
            on_progress(progress)

        state = await self.project_service.get_project(project_id)

        # Resolve background image
        bg_path: str | None = None
        if state.background_image:
            bg_path = os.path.join(tmp_dir, "background.png")
            stream = await self.storage.load_file(project_id, "background.png")
            with open(bg_path, "wb") as f:
                async for chunk in stream:
                    f.write(chunk)
        else:
            # Generate a black placeholder image
            bg_path = os.path.join(tmp_dir, "black.png")
            generate_black_image(bg_path)

        output_path = os.path.join(tmp_dir, "preview.mp4")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            create_video_with_subtitles,
            bg_path,
            audio_path,
            state.subtitles,
            output_path,
        )

        # Persist video via storage
        await self.storage.save_file_from_path(project_id, "preview.mp4", output_path)
        video_url = await self.storage.get_file_url(project_id, "preview.mp4")
        await self._update_progress(
            project_id,
            "assembly",
            "Assembly complete",
            extra={"video_url": video_url},
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    async def export_video(
        self,
        project_id: str,
        on_progress: Callable[[PipelineProgress], None] | None = None,
    ) -> str:
        """Re-render video using the current (edited) project state.

        Returns the storage URL of the exported MP4.
        """
        state = await self.project_service.get_project(project_id)
        owner_id = state.owner_id

        await self._acquire_slot(owner_id, project_id)
        try:
            await self._update_progress(
                project_id,
                "assembly",
                "Exporting video…",
                extra={"status": "exporting"},
            )
            if on_progress:
                on_progress(PipelineProgress(stage="assembly", message="Exporting video…"))

            with tempfile.TemporaryDirectory() as tmp_dir:
                # Download audio
                audio_path = await self._download_audio(project_id, tmp_dir)

                # Resolve background
                bg_path: str | None = None
                if state.background_image:
                    bg_path = os.path.join(tmp_dir, "background.png")
                    stream = await self.storage.load_file(project_id, "background.png")
                    with open(bg_path, "wb") as f:
                        async for chunk in stream:
                            f.write(chunk)
                else:
                    bg_path = os.path.join(tmp_dir, "black.png")
                    generate_black_image(bg_path)

                output_path = os.path.join(tmp_dir, "export.mp4")

                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    create_video_with_subtitles,
                    bg_path,
                    audio_path,
                    state.subtitles,
                    output_path,
                )

                await self.storage.save_file_from_path(
                    project_id, "export.mp4", output_path
                )

            export_url = await self.storage.get_file_url(project_id, "export.mp4")
            await self._update_progress(
                project_id,
                "complete",
                "Export finished",
                extra={"status": "exported", "export_url": export_url},
            )
            if on_progress:
                on_progress(PipelineProgress(stage="complete", message="Export finished"))

            return export_url

        except Exception as exc:
            logger.exception("Export failed for project %s", project_id)
            await self._update_progress(
                project_id,
                "error",
                str(exc),
                extra={"status": "error"},
            )
            if on_progress:
                on_progress(PipelineProgress(stage="error", message=str(exc)))
            raise
        finally:
            await self._release_slot(owner_id, project_id)

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    async def retry_pipeline(
        self,
        project_id: str,
        on_progress: Callable[[PipelineProgress], None] | None = None,
    ) -> None:
        """Resume the pipeline from the failed stage.

        Only valid when the project status is "error". Returns 422 otherwise.

        Retry behavior:
        - narration failed → rerun from scratch
        - subtitles failed → reuse audio, rerun subtitles + assembly
        - assembly failed  → reuse audio + subtitles, rerun assembly
        """
        state = await self.project_service.get_project(project_id)

        if state.status != "error":
            raise InvalidRetryError(
                f"Cannot retry project {project_id}: status is '{state.status}', "
                f"expected 'error'"
            )

        failed_stage = state.pipeline_progress.stage
        owner_id = state.owner_id

        # Determine where to resume
        if failed_stage == "error":
            # Generic error — check what artifacts exist to decide
            if state.audio_url and state.subtitles:
                resume_from = "assembly"
            elif state.audio_url:
                resume_from = "subtitles"
            else:
                resume_from = "narration"
        elif failed_stage in ("narration", "subtitles", "assembly"):
            resume_from = failed_stage
        else:
            resume_from = "narration"

        await self._acquire_slot(owner_id, project_id)
        try:
            # Reset status to processing
            await self._update_progress(
                project_id,
                resume_from,
                f"Retrying from {resume_from}…",
                extra={"status": "processing"},
            )

            await self._run_pipeline_stages(
                project_id, state, on_progress, start_from=resume_from
            )
        except Exception as exc:
            logger.exception("Retry failed for project %s", project_id)
            await self._update_progress(
                project_id,
                "error",
                str(exc),
                extra={"status": "error"},
            )
            if on_progress:
                on_progress(PipelineProgress(stage="error", message=str(exc)))
        finally:
            await self._release_slot(owner_id, project_id)
