#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class BinaryMetrics:
    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def specificity(self) -> float:
        d = self.tn + self.fp
        return self.tn / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        d = p + r
        return (2 * p * r) / d if d else 0.0


def _compute_metrics(y_true: list[int], y_pred: list[int]) -> BinaryMetrics:
    tp = fp = tn = fn = 0
    for t, p in zip(y_true, y_pred):
        if t == 1 and p == 1:
            tp += 1
        elif t == 0 and p == 1:
            fp += 1
        elif t == 0 and p == 0:
            tn += 1
        else:
            fn += 1
    return BinaryMetrics(tp=tp, fp=fp, tn=tn, fn=fn)


def _find_best_threshold(y_true: list[int], scores: list[float]) -> tuple[int, BinaryMetrics]:
    best_t = 50
    best_m = _compute_metrics(y_true, [1 if s >= best_t else 0 for s in scores])
    best_key = (best_m.f1, best_m.recall, best_m.precision)
    for t in range(0, 101):
        pred = [1 if s >= t else 0 for s in scores]
        m = _compute_metrics(y_true, pred)
        key = (m.f1, m.recall, m.precision)
        if key > best_key:
            best_t = t
            best_m = m
            best_key = key
    return best_t, best_m


def _latest_report_csv(report_glob: str) -> Path:
    candidates = sorted(Path(".").glob(report_glob), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No files matched --report-glob={report_glob}")
    return candidates[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate generation-origin metrics + threshold suggestion")
    parser.add_argument("--report", default="", help="CSV report path (from evaluate_random_sample.py)")
    parser.add_argument("--report-glob", default="reports/validation_*.csv", help="Fallback glob to auto-pick latest CSV")
    parser.add_argument("--labels", default="app/data/generation_labels.json", help="Ground-truth label map (video_id -> label)")
    parser.add_argument("--positive-label", default="ai_generated")
    parser.add_argument("--outdir", default="reports")
    args = parser.parse_args()

    report_path = Path(args.report) if args.report else _latest_report_csv(args.report_glob)
    labels = json.loads(Path(args.labels).read_text())

    with report_path.open() as f:
        rows = list(csv.DictReader(f))

    y_true: list[int] = []
    scores: list[float] = []
    level_pred: list[int] = []
    unresolved_truth = 0

    ok_rows = 0
    not_enough_evidence = 0
    for r in rows:
        if r.get("error"):
            continue
        ok_rows += 1
        if (r.get("top_claim_status") or "").strip() == "not_enough_evidence":
            not_enough_evidence += 1

        vid = (r.get("video_id") or "").strip()
        truth_raw = labels.get(vid)
        if truth_raw is None:
            unresolved_truth += 1
            continue

        y_true.append(1 if str(truth_raw).strip().lower() == args.positive_label.lower() else 0)
        score = _safe_float(r.get("generation_origin_score"), default=0.0)
        scores.append(max(0.0, min(100.0, score)))
        level_pred.append(1 if (r.get("generation_origin_level") or "").strip().lower() == "high" else 0)

    positives = sum(y_true)
    negatives = len(y_true) - positives

    current = _compute_metrics(y_true, level_pred) if y_true else BinaryMetrics(0, 0, 0, 0)
    best_t, best_m = _find_best_threshold(y_true, scores) if y_true else (50, BinaryMetrics(0, 0, 0, 0))

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    outpath = outdir / f"calibration_{ts}.json"

    payload = {
        "timestamp_utc": ts,
        "report_path": str(report_path),
        "rows_total": len(rows),
        "rows_ok": ok_rows,
        "rows_with_truth": len(y_true),
        "rows_without_truth": unresolved_truth,
        "truth_distribution": {"positive": positives, "negative": negatives},
        "claim_status": {
            "not_enough_evidence_count": not_enough_evidence,
            "not_enough_evidence_rate": (not_enough_evidence / ok_rows) if ok_rows else 0.0,
        },
        "current_rule": {
            "name": "generation_origin_level == high",
            "accuracy": current.accuracy,
            "precision": current.precision,
            "recall": current.recall,
            "specificity": current.specificity,
            "f1": current.f1,
            "confusion": {"tp": current.tp, "fp": current.fp, "tn": current.tn, "fn": current.fn},
        },
        "recommended_threshold_rule": {
            "name": "generation_origin_score >= threshold",
            "threshold": best_t,
            "accuracy": best_m.accuracy,
            "precision": best_m.precision,
            "recall": best_m.recall,
            "specificity": best_m.specificity,
            "f1": best_m.f1,
            "confusion": {"tp": best_m.tp, "fp": best_m.fp, "tn": best_m.tn, "fn": best_m.fn},
        },
        "warning": None,
    }

    if len(y_true) == 0:
        payload["warning"] = "No rows had ground-truth labels; metrics are not meaningful."
    elif positives == 0 or negatives == 0:
        payload["warning"] = (
            "Ground truth contains only one class in this report; collect mixed AI + human samples for reliable thresholding."
        )

    outpath.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"Report CSV: {report_path}")
    print(f"Rows ok: {ok_rows}/{len(rows)}")
    print(f"Rows with truth labels: {len(y_true)} (missing truth: {unresolved_truth})")
    print(f"Truth classes: positive={positives}, negative={negatives}")
    print(f"Top-claim not_enough_evidence rate: {not_enough_evidence}/{ok_rows} ({(not_enough_evidence / ok_rows * 100) if ok_rows else 0:.1f}%)")
    print("")
    print("Current rule (level == high):")
    print(
        f"  accuracy={current.accuracy:.3f} precision={current.precision:.3f} "
        f"recall={current.recall:.3f} f1={current.f1:.3f} specificity={current.specificity:.3f}"
    )
    print(f"  confusion tp={current.tp} fp={current.fp} tn={current.tn} fn={current.fn}")
    print("")
    print(f"Recommended threshold (score >= {best_t}):")
    print(
        f"  accuracy={best_m.accuracy:.3f} precision={best_m.precision:.3f} "
        f"recall={best_m.recall:.3f} f1={best_m.f1:.3f} specificity={best_m.specificity:.3f}"
    )
    print(f"  confusion tp={best_m.tp} fp={best_m.fp} tn={best_m.tn} fn={best_m.fn}")
    if payload["warning"]:
        print(f"Warning: {payload['warning']}")
    print(f"Saved calibration JSON: {outpath}")


if __name__ == "__main__":
    main()
