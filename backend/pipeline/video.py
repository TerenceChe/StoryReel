"""Video assembly module — combines image, audio, and subtitles using moviepy + Pillow.

Supports two modes:
1. Legacy SRT-based rendering (for CLI backward compatibility)
2. ProjectState-based rendering with per-subtitle position and style
"""

import textwrap

import numpy as np
from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
)
from PIL import Image, ImageDraw, ImageFont

from backend.models.subtitle import SubtitleSegment

# Target video resolution
VIDEO_WIDTH = 1792
VIDEO_HEIGHT = 1024

# Font fallback chain: Noto Sans CJK SC → PingFang → system default
_FONT_FALLBACK_PATHS: dict[str, list[str]] = {
    "Noto Sans CJK SC": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/local/share/fonts/NotoSansCJK-Regular.ttc",
    ],
    "PingFang": [
        "/System/Library/Fonts/PingFang.ttc",
    ],
    "__system__": [
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}

# Ordered list of font families to try
_FONT_FALLBACK_ORDER = ["Noto Sans CJK SC", "PingFang", "__system__"]


def _resolve_font(font_family: str, font_size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Resolve a font family name to a Pillow font object.

    Tries the requested font_family first, then falls through the fallback
    chain: Noto Sans CJK SC → PingFang → system default → Pillow default.
    """
    # Build search order: requested family first, then fallbacks
    families_to_try = [font_family] + [
        f for f in _FONT_FALLBACK_ORDER if f != font_family
    ]

    for family in families_to_try:
        paths = _FONT_FALLBACK_PATHS.get(family, [])
        for path in paths:
            try:
                return ImageFont.truetype(path, font_size_px)
            except (OSError, IOError):
                continue

    # Last resort: Pillow built-in bitmap font
    return ImageFont.load_default()


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """Convert a hex color string (#RRGGBB) to an RGBA tuple."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return (255, 255, 255, alpha)
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b, alpha)


def _render_text_frame(
    text: str,
    width: int,
    height: int,
    *,
    x_norm: float = 0.5,
    y_norm: float = 0.85,
    font_size_norm: float = 0.047,
    font_color: str = "#FFFFFF",
    outline_color: str = "#000000",
    font_family: str = "Noto Sans CJK SC",
) -> np.ndarray:
    """Render Chinese text onto a transparent RGBA image using Pillow.

    Positions and font size are given as normalized values (0-1 fractions of
    the video dimensions) and converted to pixel coordinates using the
    provided width/height.

    Args:
        text: The subtitle text to render.
        width: Frame width in pixels.
        height: Frame height in pixels.
        x_norm: Horizontal center position (0-1, fraction of width).
        y_norm: Vertical center position (0-1, fraction of height).
        font_size_norm: Font size as fraction of video height.
        font_color: Hex color for the text fill.
        outline_color: Hex color for the text outline.
        font_family: Preferred font family name.
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font_size_px = max(1, int(font_size_norm * height))
    font = _resolve_font(font_family, font_size_px)

    # Wrap long lines
    wrapped = textwrap.fill(text, width=20)

    # Measure text bounding box
    bbox = draw.textbbox((0, 0), wrapped, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Convert normalized position to pixel coordinates (position is the center)
    x = int(x_norm * width - text_w / 2)
    y = int(y_norm * height - text_h / 2)

    # Clamp to frame boundaries
    x = max(0, min(x, width - text_w))
    y = max(0, min(y, height - text_h))

    fill_rgba = _hex_to_rgba(font_color)
    outline_rgba = _hex_to_rgba(outline_color)

    # Draw outline
    outline_width = max(1, font_size_px // 16)
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            draw.text((x + dx, y + dy), wrapped, font=font, fill=outline_rgba)

    # Draw text on top
    draw.text((x, y), wrapped, font=font, fill=fill_rgba)

    return np.array(img)


def create_video_with_subtitles(
    image_path: str | None,
    audio_path: str,
    subtitles: list[SubtitleSegment],
    output_path: str,
    *,
    width: int = VIDEO_WIDTH,
    height: int = VIDEO_HEIGHT,
) -> None:
    """Combine background image, audio, and subtitles into a video.

    Args:
        image_path: Path to background image, or None for solid black.
        audio_path: Path to the narration audio file.
        subtitles: List of SubtitleSegment objects with position/style info.
        output_path: Where to write the output MP4.
        width: Video width in pixels (default 1792).
        height: Video height in pixels (default 1024).
    """
    audio = AudioFileClip(audio_path)
    duration = audio.duration

    # Background: image or solid black
    if image_path:
        bg = ImageClip(image_path).with_duration(duration).resized((width, height))
    else:
        black_frame = np.zeros((height, width, 3), dtype=np.uint8)
        bg = ImageClip(black_frame).with_duration(duration)

    # Create subtitle overlay clips from structured data
    subtitle_clips = []
    for seg in subtitles:
        seg_duration = seg.end_time - seg.start_time
        if seg_duration <= 0:
            continue

        frame = _render_text_frame(
            seg.text,
            width,
            height,
            x_norm=seg.position.x,
            y_norm=seg.position.y,
            font_size_norm=seg.style.font_size,
            font_color=seg.style.font_color,
            outline_color=seg.style.outline_color,
            font_family=seg.style.font_family,
        )
        clip = (
            ImageClip(frame, is_mask=False)
            .with_duration(seg_duration)
            .with_start(seg.start_time)
        )
        subtitle_clips.append(clip)

    final = CompositeVideoClip([bg] + subtitle_clips, size=(width, height))
    final = final.with_audio(audio).with_duration(duration)

    final.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        logger="bar",
    )
    print(f"Video created: {output_path}")


# ---------------------------------------------------------------------------
# Legacy SRT-based rendering (for CLI backward compatibility)
# ---------------------------------------------------------------------------

def parse_srt(srt_path: str) -> list[dict]:
    """Parse an SRT file into a list of {start, end, text} dicts (times in seconds)."""
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    segments = []
    blocks = content.strip().split("\n\n")
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        time_line = lines[1]
        text = " ".join(lines[2:])
        start_str, end_str = time_line.split(" --> ")
        segments.append({
            "start": _srt_to_seconds(start_str.strip()),
            "end": _srt_to_seconds(end_str.strip()),
            "text": text,
        })
    return segments


def _srt_to_seconds(ts: str) -> float:
    """Convert SRT timestamp (HH:MM:SS,mmm) to seconds."""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def create_video_from_srt(
    image_path: str, audio_path: str, srt_path: str, output_path: str
) -> None:
    """Legacy entry point: create video from an SRT file.

    Converts SRT segments to SubtitleSegment objects with default
    position/style and delegates to create_video_with_subtitles.
    """
    import uuid
    from backend.models.subtitle import Position, SubtitleStyle

    raw_segments = parse_srt(srt_path)
    subtitle_segments = [
        SubtitleSegment(
            id=uuid.uuid4().hex,
            text=seg["text"],
            start_time=seg["start"],
            end_time=seg["end"],
            position=Position(x=0.5, y=0.85),
            style=SubtitleStyle(),
        )
        for seg in raw_segments
    ]
    create_video_with_subtitles(image_path, audio_path, subtitle_segments, output_path)
