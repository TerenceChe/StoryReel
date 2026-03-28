"""Narration module — generates Chinese voice narration using edge-tts."""

import asyncio

import edge_tts


async def _generate(story: str, output_path: str, voice: str):
    """Async helper to generate narration."""
    communicate = edge_tts.Communicate(story, voice)
    await communicate.save(output_path)


def generate_narration(story: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> str:
    """Generate Chinese narration using edge-tts (free, no API key needed).

    Available Chinese voices:
        - zh-CN-XiaoxiaoNeural (female, default)
        - zh-CN-YunxiNeural (male)
        - zh-CN-YunjianNeural (male, narrator style)
    """
    asyncio.run(_generate(story, output_path, voice))
    print(f"Narration saved: {output_path}")
    return output_path
