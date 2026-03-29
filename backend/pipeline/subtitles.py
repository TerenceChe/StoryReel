"""Subtitle module — generates timed subtitles using local Whisper model.

Returns structured SubtitleSegment data with UUIDs, default positions, and
styles suitable for the web editor.
"""

import uuid

import whisper

from backend.models.subtitle import Position, SubtitleSegment, SubtitleStyle

# Default subtitle position: centered horizontally, near bottom
DEFAULT_POSITION = Position(x=0.5, y=0.85)

# Default subtitle style
DEFAULT_STYLE = SubtitleStyle()


def generate_timestamps(audio_path: str, model_size: str = "base") -> list[dict]:
    """Use local Whisper model to get segment-level timestamps from audio."""
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, language="zh")
    return result["segments"]


def build_subtitle_segments(raw_segments: list[dict]) -> list[SubtitleSegment]:
    """Convert raw Whisper segments into structured SubtitleSegment objects.

    Each segment gets a unique UUID, default position (centered, near bottom),
    and default style (white text, black outline, Noto Sans CJK SC).
    """
    segments: list[SubtitleSegment] = []
    for seg in raw_segments:
        text = seg["text"].strip()
        if not text:
            continue
        segments.append(
            SubtitleSegment(
                id=uuid.uuid4().hex,
                text=text,
                start_time=seg["start"],
                end_time=seg["end"],
                position=DEFAULT_POSITION.model_copy(),
                style=DEFAULT_STYLE.model_copy(),
            )
        )
    return segments


# ---------------------------------------------------------------------------
# Legacy SRT helpers (kept for CLI backward compatibility)
# ---------------------------------------------------------------------------

def build_srt(segments: list[dict]) -> str:
    """Build SRT subtitle content from Whisper segments."""
    srt_lines = []
    for i, seg in enumerate(segments, 1):
        start = _format_timestamp(seg["start"])
        end = _format_timestamp(seg["end"])
        text = seg["text"].strip()
        srt_lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(srt_lines)


def _format_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format (HH:MM:SS,mmm)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
