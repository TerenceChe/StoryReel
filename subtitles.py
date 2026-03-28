"""Subtitle module — generates timed SRT subtitles using local Whisper model."""

import whisper


def generate_timestamps(audio_path: str, model_size: str = "base") -> list[dict]:
    """Use local Whisper model to get segment-level timestamps from audio."""
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, language="zh")
    return result["segments"]


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
