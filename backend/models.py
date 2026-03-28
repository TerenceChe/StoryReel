"""Pydantic data models for the Story Video Editor."""

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, model_validator


class Position(BaseModel):
    x: float  # 0-1 normalized
    y: float  # 0-1 normalized


class SubtitleStyle(BaseModel):
    font_size: float = 0.047  # normalized (fraction of video height, ~48px at 1024h)
    font_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    font_family: str = "Noto Sans CJK SC"


class SubtitleSegment(BaseModel):
    id: str
    text: str
    start_time: float
    end_time: float
    position: Position
    style: SubtitleStyle

    @model_validator(mode="after")
    def validate_timing(self) -> "SubtitleSegment":
        if self.start_time >= self.end_time:
            raise ValueError(
                f"start_time ({self.start_time}) must be less than end_time ({self.end_time})"
            )
        return self


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


class ImageGenerationBackend(ABC):
    """Abstract interface for AI image generation providers."""

    @abstractmethod
    async def generate_single(self, prompt: str) -> bytes:
        """Generate a single image from a text prompt."""
        ...

    @abstractmethod
    async def generate_sectioned(self, prompts: list[str]) -> list[bytes]:
        """Generate images for multiple story sections."""
        ...
