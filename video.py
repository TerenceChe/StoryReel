"""Video assembly module — combines image, audio, and subtitles using moviepy + Pillow."""

import textwrap

import numpy as np
from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
)
from PIL import Image, ImageDraw, ImageFont


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


def _render_text_frame(text: str, width: int, height: int) -> np.ndarray:
    """Render Chinese text onto a transparent RGBA image using Pillow."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Try to use a CJK font, fall back to default
    font_size = 48
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", font_size)
    except (OSError, IOError):
        try:
            font = ImageFont.truetype("/System/Library/Fonts/STHeiti Light.ttc", font_size)
        except (OSError, IOError):
            font = ImageFont.load_default()

    # Wrap long lines
    wrapped = textwrap.fill(text, width=20)

    # Get text bounding box for centering
    bbox = draw.textbbox((0, 0), wrapped, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    x = (width - text_w) // 2
    y = height - text_h - 80

    # Draw black outline
    for dx in range(-3, 4):
        for dy in range(-3, 4):
            draw.text((x + dx, y + dy), wrapped, font=font, fill=(0, 0, 0, 255))

    # Draw white text on top
    draw.text((x, y), wrapped, font=font, fill=(255, 255, 255, 255))

    return np.array(img)


def create_video_with_subtitles(
    image_path: str, audio_path: str, srt_path: str, output_path: str
):
    """Combine image, audio, and burned-in subtitles into a final video."""
    segments = parse_srt(srt_path)
    audio = AudioFileClip(audio_path)
    duration = audio.duration

    # Background image stretched to full duration
    bg = ImageClip(image_path).with_duration(duration).resized((1792, 1024))

    # Create subtitle overlay clips
    subtitle_clips = []
    for seg in segments:
        text = seg["text"]
        start = seg["start"]
        end = seg["end"]
        seg_duration = end - start

        # Render text frame once, then use it as an ImageClip
        frame = _render_text_frame(text, 1792, 1024)
        clip = (
            ImageClip(frame, is_mask=False)
            .with_duration(seg_duration)
            .with_start(start)
        )
        subtitle_clips.append(clip)

    # Composite everything together
    final = CompositeVideoClip([bg] + subtitle_clips, size=(1792, 1024))
    final = final.with_audio(audio).with_duration(duration)

    final.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        logger="bar",
    )
    print(f"Video created: {output_path}")
