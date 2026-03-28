"""Image generation module — creates a placeholder black image for the video."""

import subprocess


def generate_black_image(output_path: str, width: int = 1792, height: int = 1024):
    """Generate a solid black image using ffmpeg."""
    subprocess.run(
        [
            "ffmpeg",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:d=1",
            "-frames:v", "1",
            "-y",
            output_path,
        ],
        check=True,
        capture_output=True,
    )
    print(f"Image saved: {output_path}")
