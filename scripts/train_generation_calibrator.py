#!/usr/bin/env python3
"""Train a generation-origin score calibrator from evaluation CSV + ground-truth labels.

This script fits a lightweight logistic model (numpy-only) and exports a JSON model
used at runtime to better map heuristic generation scores to empirical probabilities.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from urllib.parse import urlparse

import numpy as np


FEATURE_ORDER = [
    "generation_origin_score",
    "manipulation_score",
    "uncertainty_score",
    "evidence_quality_score",
    "top_claim_confidence",
    "top_claim_not_enough",
    "credibility_inverse",
]


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(str(value).strip())
    except Exception:
        return default


def _extract_video_id(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")
    if host == "youtu.be" and path:
        return path.split("/")[0]
    if "youtube.com" in host and path.startswith("shorts/"):
        parts = path.split("/")
        if len(parts) > 1:
            return parts[1]
    return ""


def _sigmoid(z: np.ndarray) -> np.ndarray:
    zc = np.clip(z, -35, 35)
    return 1.0 / (1.0 + np.exp(-zc))


def _binary_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    tp = float(np.sum((y_true == 1) & (y_pred == 1)))
    tn = float(np.sum((y_true == 0) & (y_pred == 0)))
    fp = float(np.sum((y_true == 0) & (y_pred == 1)))
    fn = float(np.sum((y_true == 1) & (y_pred == 0)))
    eps = 1e-9
    precision = tp / (tp + fp + eps)
    recall = tp / (tp + fn + eps)
    f1 = 2 * precision * recall / (precision + recall + eps)
    acc = (tp + tn) / max(1.0, tp + tn + fp + fn)
    specificity = tn / (tn + fp + eps)
    return {
        "accuracy": round(acc, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "specificity": round(specificity, 4),
        "tp": int(tp),
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
    }


def _best_threshold(y_true: np.ndarray, probs: np.ndarray) -> tuple[float, dict[str, float]]:
    best_t = 0.5
    best_metrics: dict[str, float] | None = None
    for t in np.linspace(0.2, 0.8, 61):
        pred = (probs >= t).astype(int)
        m = _binary_metrics(y_true, pred)
        if best_metrics is None or m["f1"] > best_metrics["f1"]:
            best_t = float(t)
            best_metrics = m
    assert best_metrics is not None
    return best_t, best_metrics


def _collect_rows(report_paths: list[Path], labels: dict[str, str]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for report in report_paths:
        with report.open() as f:
            reader = csv.DictReader(f)
            for r in reader:
                if (r.get("error") or "").strip():
                    continue
                vid = (r.get("video_id") or "").strip()
                if not vid:
                    vid = _extract_video_id((r.get("url") or "").strip())
                truth_raw = labels.get(vid)
                if truth_raw not in {"ai_generated", "human_generated"}:
                    continue
                if (r.get("generation_origin_score") or "").strip() == "":
                    continue
                y = 1.0 if truth_raw == "ai_generated" else 0.0
                claim_status = (r.get("top_claim_status") or "").strip().lower()

                cred = _safe_float(r.get("credibility_score"), 75.0)
                gen = _safe_float(r.get("generation_origin_score"), 0.0)
                mani = _safe_float(r.get("manipulation_score"), 0.0)
                uncert = _safe_float(r.get("uncertainty_score"), 60.0)
                evidence_q = _safe_float(r.get("evidence_quality_score"), 70.0)
                claim_conf = _safe_float(r.get("top_claim_confidence"), 0.5)

                feats = {
                    "generation_origin_score": gen,
                    "manipulation_score": mani,
                    "uncertainty_score": uncert,
                    "evidence_quality_score": evidence_q,
                    "top_claim_confidence": claim_conf,
                    "top_claim_not_enough": 1.0 if claim_status == "not_enough_evidence" else 0.0,
                    "credibility_inverse": max(0.0, min(100.0, 100.0 - cred)),
                    "y": y,
                }
                rows.append(feats)
    return rows


def _fit_logistic(X: np.ndarray, y: np.ndarray, epochs: int, lr: float, l2: float) -> tuple[np.ndarray, float]:
    n, d = X.shape
    w = np.zeros(d, dtype=float)
    b = 0.0
    for _ in range(epochs):
        z = X @ w + b
        p = _sigmoid(z)
        err = p - y
        grad_w = (X.T @ err) / n + l2 * w
        grad_b = float(np.mean(err))
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def main() -> None:
    parser = argparse.ArgumentParser(description="Train generation-origin calibrator from validation CSV(s).")
    parser.add_argument("--labels", default="app/data/eval_mixed_labels.json")
    parser.add_argument(
        "--reports-glob",
        default="reports/validation_*.csv",
        help="Glob of evaluation CSV reports (from evaluate_random_sample.py).",
    )
    parser.add_argument("--epochs", type=int, default=1200)
    parser.add_argument("--lr", type=float, default=0.08)
    parser.add_argument("--l2", type=float, default=0.002)
    parser.add_argument("--blend", type=float, default=0.70, help="Blend factor vs base generation score (0..1).")
    parser.add_argument("--output", default="app/data/generation_calibrator.json")
    args = parser.parse_args()

    labels = json.loads(Path(args.labels).read_text())
    report_paths = sorted(Path(".").glob(args.reports_glob))
    if not report_paths:
        raise FileNotFoundError(f"No report CSV found for glob: {args.reports_glob}")

    data_rows = _collect_rows(report_paths, labels)
    if len(data_rows) < 20:
        raise ValueError(f"Not enough labeled rows to fit calibrator: {len(data_rows)}")

    X = np.array([[float(r[k]) for k in FEATURE_ORDER] for r in data_rows], dtype=float)
    y = np.array([float(r["y"]) for r in data_rows], dtype=float)

    means = X.mean(axis=0)
    stds = X.std(axis=0)
    stds = np.where(stds < 1e-6, 1.0, stds)
    Xn = (X - means) / stds

    w, b = _fit_logistic(Xn, y, epochs=args.epochs, lr=args.lr, l2=args.l2)
    probs = _sigmoid(Xn @ w + b)

    base_pred = (X[:, 0] >= 50.0).astype(int)
    base_metrics = _binary_metrics(y.astype(int), base_pred)
    t_star, best_metrics = _best_threshold(y.astype(int), probs)

    payload = {
        "feature_order": FEATURE_ORDER,
        "weights": [float(x) for x in w.tolist()],
        "bias": float(b),
        "means": [float(x) for x in means.tolist()],
        "stds": [float(x) for x in stds.tolist()],
        "blend": float(max(0.0, min(1.0, args.blend))),
        "recommended_threshold": round(float(t_star), 3),
        "training_rows": int(len(data_rows)),
        "class_balance": {
            "ai_generated": int(np.sum(y == 1.0)),
            "human_generated": int(np.sum(y == 0.0)),
        },
        "metrics": {
            "base_rule_generation_score_ge_50": base_metrics,
            "calibrated_probability_rule": best_metrics,
        },
        "reports_used": [str(p) for p in report_paths],
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"Saved calibrator: {out}")
    print(f"Rows used: {len(data_rows)}")
    print(
        "Class balance:",
        payload["class_balance"]["ai_generated"],
        "AI /",
        payload["class_balance"]["human_generated"],
        "human",
    )
    print("Base metrics:", base_metrics)
    print(f"Calibrated threshold: {payload['recommended_threshold']}")
    print("Calibrated metrics:", best_metrics)


if __name__ == "__main__":
    main()
