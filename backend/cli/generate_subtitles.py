#!/usr/bin/env python3
"""
Subtitle Burner
Extracts audio from a video, generates Chinese subtitles using OpenAI Whisper,
and burns them directly into the video TikTok-style.
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def extract_audio(video_path: str, audio_path: str):
    """Extract audio track from video using ffmpeg."""
    subprocess.run(
        ["ffmpeg", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-y", audio_path],
        check=True,
        capture_output=True,
    )
    print(f"Audio extracted: {audio_path}")


def generate_srt(client: OpenAI, audio_path: str) -> str:
    """Transcribe audio and return SRT-formatted subtitles."""
    with open(audio_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="zh",
            response_format="srt",
        )
    return transcript


def burn_subtitles(video_path: str, srt_path: str, output_path: str):
    """Burn SRT subtitles into the video with TikTok-style formatting."""
    # Escape special characters in the path for ffmpeg filter syntax
    # ffmpeg subtitles filter uses : and \ as special chars
    escaped_srt = srt_path.replace("\\", "\\\\").replace(":", "\\:")

    # TikTok-style: bold, white text with black outline, centered near bottom
    style = (
        "FontName=Arial,"
        "FontSize=22,"
        "PrimaryColour=&H00FFFFFF,"  # white
        "OutlineColour=&H00000000,"  # black outline
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=0,"
        "Alignment=2,"  # bottom center
        "MarginV=40"
    )

    subprocess.run(
        [
            "ffmpeg",
            "-i", video_path,
            "-vf", f"subtitles={escaped_srt}:force_style='{style}'",
            "-c:a", "copy",
            "-y",
            output_path,
        ],
        check=True,
    )
    print(f"Video with subtitles saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Burn subtitles into a video TikTok-style")
    parser.add_argument("input", help="Path to video file")
    parser.add_argument("-o", "--output", default=None, help="Output video path (default: input_subtitled.mp4)")
    args = parser.parse_args()

    video_path = Path(args.input)
    if not video_path.exists():
        print(f"Error: file not found {video_path}")
        sys.exit(1)

    output_path = args.output or str(video_path.with_stem(video_path.stem + "_subtitled"))

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        print("Error: please set OPENAI_API_KEY in your .env file")
        sys.exit(1)

    client = OpenAI(api_key=openai_key)

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Extract audio
        print("Extracting audio from video...")
        audio_path = os.path.join(tmp_dir, "audio.mp3")
        extract_audio(str(video_path), audio_path)

        # Generate subtitles
        print("Transcribing audio with Whisper...")
        srt_content = generate_srt(client, audio_path)

        # Save SRT to temp file (needed by ffmpeg subtitles filter)
        srt_path = os.path.join(tmp_dir, "subs.srt")
        Path(srt_path).write_text(srt_content, encoding="utf-8")
        print(f"Generated {srt_content.count(chr(10))} lines of subtitles")

        # Burn subtitles into video
        print("Burning subtitles into video...")
        burn_subtitles(str(video_path), srt_path, output_path)

    print("Done!")


if __name__ == "__main__":
    main()
