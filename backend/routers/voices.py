"""Voices endpoint — public (no authentication required).

GET /voices — return list of available Chinese edge-tts voices.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["voices"])

# Available Chinese edge-tts voices.  This is a curated list; the full
# edge-tts catalogue is much larger but we only expose Chinese voices
# relevant to the story-video-editor use case.
AVAILABLE_VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "Xiaoxiao (Female)", "language": "zh-CN"},
    {"id": "zh-CN-YunxiNeural", "name": "Yunxi (Male)", "language": "zh-CN"},
    {"id": "zh-CN-YunjianNeural", "name": "Yunjian (Male)", "language": "zh-CN"},
]


@router.get("/voices")
async def list_voices() -> list[dict]:
    """Return available edge-tts voices. No authentication required."""
    return AVAILABLE_VOICES
