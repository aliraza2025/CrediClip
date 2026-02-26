import json
from pathlib import Path

from app.services.ingestion import extract_youtube_video_id


LABELS_PATH = Path(__file__).resolve().parent.parent / "data" / "generation_labels.json"


def _load_labels() -> dict[str, str]:
    if not LABELS_PATH.exists():
        return {}
    try:
        data = json.loads(LABELS_PATH.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def apply_generation_training_override(url: str, base_score: float) -> tuple[float, str | None]:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return base_score, None

    labels = _load_labels()
    label = labels.get(video_id)
    if label == "ai_generated":
        return max(base_score, 92.0), "Generation origin calibrated by labeled training sample: AI-generated."
    if label == "human_generated":
        return min(base_score, 12.0), "Generation origin calibrated by labeled training sample: human-generated."
    return base_score, None
