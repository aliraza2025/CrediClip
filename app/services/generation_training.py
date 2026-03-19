from __future__ import annotations

import json
from pathlib import Path

from app.services.ingestion import extract_youtube_video_id


LABELS_PATH = Path(__file__).resolve().parent.parent / "data" / "generation_labels.json"
CALIBRATOR_PATH = Path(__file__).resolve().parent.parent / "data" / "generation_calibrator.json"


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


def _load_calibrator() -> dict | None:
    if not CALIBRATOR_PATH.exists():
        return None
    try:
        data = json.loads(CALIBRATOR_PATH.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    required = {"feature_order", "weights", "bias", "means", "stds"}
    if not required.issubset(set(data.keys())):
        return None
    return data


def _sigmoid(z: float) -> float:
    if z < -35:
        return 0.0
    if z > 35:
        return 1.0
    return 1.0 / (1.0 + pow(2.718281828459045, -z))


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _apply_calibrator(base_score: float, feature_values: dict[str, float]) -> tuple[float, str | None]:
    model = _load_calibrator()
    if not model:
        return base_score, None
    try:
        order = [str(x) for x in model["feature_order"]]
        weights = [float(x) for x in model["weights"]]
        means = [float(x) for x in model["means"]]
        stds = [float(x) for x in model["stds"]]
        bias = float(model["bias"])
        blend = float(model.get("blend", 0.70))
    except Exception:
        return base_score, None

    if not (len(order) == len(weights) == len(means) == len(stds)):
        return base_score, None

    z = bias
    for i, key in enumerate(order):
        x = float(feature_values.get(key, 0.0))
        std = stds[i] if abs(stds[i]) > 1e-6 else 1.0
        xn = (x - means[i]) / std
        z += weights[i] * xn

    calibrated_prob = _sigmoid(z)
    calibrated_score = calibrated_prob * 100.0
    blend = max(0.0, min(1.0, blend))
    blended = _clamp((1.0 - blend) * float(base_score) + blend * calibrated_score)
    return blended, "Generation origin score calibrated by trained logistic model."


def apply_generation_training_override(
    url: str,
    base_score: float,
    feature_values: dict[str, float] | None = None,
) -> tuple[float, str | None]:
    video_id = extract_youtube_video_id(url)
    if not video_id:
        if feature_values:
            return _apply_calibrator(base_score, feature_values)
        return base_score, None

    labels = _load_labels()
    label = labels.get(video_id)
    if label == "ai_generated":
        return max(base_score, 92.0), "Generation origin calibrated by labeled training sample: AI-generated."
    if label == "human_generated":
        return min(base_score, 12.0), "Generation origin calibrated by labeled training sample: human-generated."
    if feature_values:
        return _apply_calibrator(base_score, feature_values)
    return base_score, None
