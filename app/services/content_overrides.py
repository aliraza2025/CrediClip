from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from app.services.ingestion import extract_youtube_video_id


OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "data" / "manual_content_overrides.json"


def _load_overrides() -> dict[str, dict]:
    if not OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(OVERRIDES_PATH.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def content_override_key(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path or ""

    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        video_id = extract_youtube_video_id(url)
        if video_id:
            return f"youtube_shorts:{video_id}"
        return None

    if host in {"tiktok.com", "www.tiktok.com"}:
        match = re.search(r"/video/(\d+)", path)
        if match:
            return f"tiktok:{match.group(1)}"
        return None

    if host in {"instagram.com", "www.instagram.com"}:
        match = re.search(r"/(?:reel|p)/([A-Za-z0-9_-]+)", path)
        if match:
            return f"instagram:{match.group(1)}"
        return None

    return None


def apply_curated_content_override(
    url: str,
    component_scores: dict[str, float],
    credibility_score: float,
) -> tuple[dict[str, float], float, list[str]]:
    key = content_override_key(url)
    if not key:
        return component_scores, credibility_score, []

    override = _load_overrides().get(key)
    if not override:
        return component_scores, credibility_score, []

    updated = dict(component_scores)
    notes: list[str] = []

    for field in [
        "misinformation",
        "scam",
        "manipulation",
        "uncertainty",
        "generation_origin",
        "evidence_quality",
    ]:
        if field in override:
            updated[field] = float(override[field])

    if "credibility_cap" in override:
        credibility_score = min(float(credibility_score), float(override["credibility_cap"]))
    if "credibility_floor" in override:
        credibility_score = max(float(credibility_score), float(override["credibility_floor"]))

    category = str(override.get("category", "")).strip()
    if category:
        notes.append(f"Applied curated content override for demo category: {category}.")
    rationale = str(override.get("note", "")).strip()
    if rationale:
        notes.append(rationale)

    return updated, credibility_score, notes


def has_curated_content_override(url: str) -> bool:
    key = content_override_key(url)
    if not key:
        return False
    return key in _load_overrides()
