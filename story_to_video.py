#!/usr/bin/env python3
"""
Story-to-Video Tool
Takes a Chinese story as input and generates a video with AI narration,
a background image, and burned-in TikTok-style subtitles.
No API keys required — everything runs locally or via free services.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

from image_gen import generate_black_image
from narration import generate_narration
from subtitles import build_srt, generate_timestamps
from video import create_video_with_subtitles


def main():
    parser = argparse.ArgumentParser(description="Story-to-Video Tool")
    parser.add_argument("input", help="Path to story text file (.txt)")
    parser.add_argument(
        "-o", "--output", default="output.mp4",
        help="Output video path (default: output.mp4)",
    )
    args = parser.parse_args()

    # Read the story
    story_path = Path(args.input)
    if not story_path.exists():
        print(f"Error: file not found {story_path}")
        sys.exit(1)

    story = story_path.read_text(encoding="utf-8").strip()
    if not story:
        print("Error: story file is empty")
        sys.exit(1)

    print(f"Story loaded ({len(story)} characters)")

    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1. Generate placeholder image
        print("Generating black background image...")
        image_path = os.path.join(tmp_dir, "scene.png")
        generate_black_image(image_path)

        # 2. Generate narration (edge-tts, free, no API key)
        print("Generating Chinese narration...")
        audio_path = os.path.join(tmp_dir, "narration.mp3")
        generate_narration(story, audio_path)

        # 3. Generate subtitles using local Whisper
        print("Generating subtitle timestamps (local Whisper)...")
        segments = generate_timestamps(audio_path)
        srt_content = build_srt(segments)
        srt_path = os.path.join(tmp_dir, "subtitles.srt")
        Path(srt_path).write_text(srt_content, encoding="utf-8")
        print(f"Generated {len(segments)} subtitle segments")

        # 4. Assemble final video
        print("Assembling video with subtitles...")
        create_video_with_subtitles(image_path, audio_path, srt_path, args.output)

    print("Done!")


if __name__ == "__main__":
    main()
