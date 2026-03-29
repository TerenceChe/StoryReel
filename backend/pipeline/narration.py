"""Narration module — generates Chinese voice narration using edge-tts."""

import asyncio

import edge_tts
from mutagen.mp3 import MP3


async def _generate(story: str, output_path: str, voice: str):
    """Async helper to generate narration."""
    communicate = edge_tts.Communicate(story, voice)
    await communicate.save(output_path)


def generate_narration(
    story: str,
    output_path: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
) -> tuple[str, float]:
    """Generate Chinese narration using edge-tts and return (path, duration).

    Available Chinese voices:
        - zh-CN-XiaoxiaoNeural (female, default)
        - zh-CN-YunxiNeural (male)
        - zh-CN-YunjianNeural (male, narrator style)

    Returns:
        Tuple of (output_path, audio_duration_in_seconds).
    """
    asyncio.run(_generate(story, output_path, voice))
    duration = _get_audio_duration(output_path)
    print(f"Narration saved: {output_path} ({duration:.2f}s)")
    return output_path, duration


def _get_audio_duration(audio_path: str) -> float:
    """Get the duration of an MP3 file in seconds."""
    audio = MP3(audio_path)
    return audio.info.length
