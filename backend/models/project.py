"""Project state data models."""

from typing import Literal

from pydantic import BaseModel

from backend.models.subtitle import SubtitleSegment


class PipelineProgress(BaseModel):
    stage: Literal["narration", "subtitles", "assembly", "complete", "error"]
    message: str


class ProjectState(BaseModel):
    id: str
    owner_id: str
    title: str
    story_text: str
    voice: str = "zh-CN-XiaoxiaoNeural"
    status: Literal["pending", "processing", "ready", "exporting", "exported", "error"] = "pending"
    version: int = 1
    pipeline_progress: PipelineProgress
    subtitles: list[SubtitleSegment] = []
    background_image: str | None = None
    video_url: str | None = None
    audio_url: str | None = None
    audio_duration: float | None = None
    export_url: str | None = None
    created_at: str
    updated_at: str
