"""Subtitle-related data models."""

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
