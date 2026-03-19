from __future__ import annotations

import os

import httpx


async def optional_aiornot_scan(url: str) -> dict[str, float] | None:
    """Best-effort integration hook for AI or Not API.

    If env vars are not provided or the request fails, return None and continue
    with heuristic-only scoring.
    """
    api_key = os.getenv("AIORNOT_API_KEY")
    endpoint = os.getenv("AIORNOT_VIDEO_ENDPOINT")

    if not api_key or not endpoint:
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"url": url}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return None

    video_risk = float(data.get("video_ai_probability", 0.0))
    audio_risk = float(data.get("audio_ai_probability", 0.0))
    return {"video_risk": video_risk, "audio_risk": audio_risk}
