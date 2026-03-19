#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import random
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def run_api(api_url: str, video_id: str, timeout: int) -> dict:
    url = f"https://youtube.com/shorts/{video_id}"
    payload = json.dumps({"url": url})
    cmd = [
        "curl",
        "-sS",
        "-m",
        str(timeout),
        "-X",
        "POST",
        api_url,
        "-H",
        "Content-Type: application/json",
        "-d",
        payload,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return {"video_id": video_id, "url": url, "error": proc.stderr.strip() or f"curl exit {proc.returncode}"}

    try:
        data = json.loads(proc.stdout)
    except Exception:
        return {"video_id": video_id, "url": url, "error": "invalid_json", "raw": proc.stdout[:240]}

    claim0 = (data.get("claim_assessments") or [{}])[0]
    evidence = data.get("evidence_coverage") or {}
    notes = data.get("notes") or []
    return {
        "video_id": video_id,
        "url": url,
        "credibility_score": data.get("credibility_score"),
        "top_claim_status": claim0.get("status"),
        "evidence_level": evidence.get("level"),
        "evidence_total_tokens": evidence.get("total_tokens"),
        "evidence_caption_tokens": evidence.get("caption_tokens"),
        "evidence_transcript_tokens": evidence.get("transcript_tokens"),
        "evidence_ocr_tokens": evidence.get("ocr_tokens"),
        "evidence_asr_tokens": evidence.get("asr_tokens"),
        "notes": " | ".join(notes[:6]),
    }


def _sample_video_ids(labels: dict, n: int, seed: int, balanced: bool) -> list[str]:
    random.seed(seed)
    if not balanced:
        ids = list(labels.keys())
        if n > len(ids):
            raise ValueError(f"Requested n={n} but labels only contain {len(ids)} IDs")
        return random.sample(ids, n)

    by_label: dict[str, list[str]] = {}
    for vid, label in labels.items():
        by_label.setdefault(str(label), []).append(str(vid))
    classes = sorted(by_label.keys())
    if not classes:
        raise ValueError("No labels found.")

    base = n // len(classes)
    rem = n % len(classes)
    sample: list[str] = []
    for i, cls in enumerate(classes):
        pool = by_label.get(cls, [])
        need = base + (1 if i < rem else 0)
        if need > len(pool):
            raise ValueError(f"Class '{cls}' has only {len(pool)} IDs, cannot sample {need}.")
        sample.extend(random.sample(pool, need))
    random.shuffle(sample)
    return sample


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase-1 readiness benchmark gates for YouTube ingestion/scoring.")
    parser.add_argument("--labels", default="app/data/eval_mixed_labels.json")
    parser.add_argument("--api-url", default="https://crediclip-axraza-msba.fly.dev/api/analyze")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260303)
    parser.add_argument("--balanced", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--min-evidence-tokens", type=int, default=20)
    parser.add_argument("--min-ingestion-success", type=float, default=0.85)
    parser.add_argument("--max-not-enough-rate", type=float, default=0.40)
    parser.add_argument("--min-score-stddev", type=float, default=8.0)
    parser.add_argument("--min-queue-reliability", type=float, default=0.95)
    parser.add_argument("--queue-total", type=int, default=0)
    parser.add_argument("--queue-completed", type=int, default=0)
    args = parser.parse_args()

    labels = json.loads(Path(args.labels).read_text())
    sample_ids = _sample_video_ids(labels=labels, n=args.n, seed=args.seed, balanced=args.balanced)
    workers = max(1, int(args.concurrency))
    rows: list[dict] = [None for _ in sample_ids]  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(run_api, api_url=args.api_url, video_id=vid, timeout=args.timeout): idx
            for idx, vid in enumerate(sample_ids)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                rows[idx] = future.result()
            except Exception as exc:
                vid = sample_ids[idx]
                rows[idx] = {
                    "video_id": vid,
                    "url": f"https://youtube.com/shorts/{vid}",
                    "error": f"worker_exception:{type(exc).__name__}",
                }

    ok_rows = [r for r in rows if "error" not in r]
    ingestion_ok_rows = []
    for r in ok_rows:
        cap_toks = _safe_int(r.get("evidence_caption_tokens"))
        total_toks = _safe_int(r.get("evidence_total_tokens"))
        if cap_toks > 0 and total_toks >= args.min_evidence_tokens:
            ingestion_ok_rows.append(r)

    tested = len(rows)
    ingestion_success_rate = (len(ingestion_ok_rows) / tested) if tested else 0.0
    not_enough_count = sum(1 for r in ingestion_ok_rows if (r.get("top_claim_status") or "") == "not_enough_evidence")
    not_enough_rate = (not_enough_count / len(ingestion_ok_rows)) if ingestion_ok_rows else 1.0

    score_values = []
    for r in ingestion_ok_rows:
        try:
            score_values.append(float(r.get("credibility_score")))
        except (TypeError, ValueError):
            continue
    score_stddev = statistics.pstdev(score_values) if len(score_values) >= 2 else 0.0

    queue_reliability = None
    if args.queue_total > 0:
        queue_reliability = max(0.0, min(1.0, args.queue_completed / args.queue_total))

    gates = {
        "ingestion_success_rate": {
            "value": ingestion_success_rate,
            "threshold": args.min_ingestion_success,
            "pass": ingestion_success_rate >= args.min_ingestion_success,
        },
        "not_enough_evidence_rate": {
            "value": not_enough_rate,
            "threshold": args.max_not_enough_rate,
            "pass": not_enough_rate <= args.max_not_enough_rate,
        },
        "score_stddev": {
            "value": score_stddev,
            "threshold": args.min_score_stddev,
            "pass": score_stddev >= args.min_score_stddev,
        },
        "queue_reliability": {
            "value": queue_reliability,
            "threshold": args.min_queue_reliability,
            "pass": (queue_reliability is not None and queue_reliability >= args.min_queue_reliability),
            "evaluated": queue_reliability is not None,
        },
    }

    core_pass = all(gates[k]["pass"] for k in ("ingestion_success_rate", "not_enough_evidence_rate", "score_stddev"))
    overall_pass = core_pass and bool(gates["queue_reliability"]["pass"])

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / f"phase1_benchmark_{args.n}_{ts}.csv"
    json_path = outdir / f"phase1_benchmark_{args.n}_{ts}.json"

    fieldnames = sorted({k for row in rows for k in row.keys()})
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "timestamp_utc": ts,
        "api_url": args.api_url,
        "labels": args.labels,
        "n": args.n,
        "seed": args.seed,
        "balanced": args.balanced,
        "gates": gates,
        "counts": {
            "tested": tested,
            "ok_rows": len(ok_rows),
            "ingestion_ok_rows": len(ingestion_ok_rows),
            "not_enough_count": not_enough_count,
        },
        "result": {
            "core_pass": core_pass,
            "overall_pass": overall_pass,
            "queue_gate_evaluated": bool(gates["queue_reliability"]["evaluated"]),
        },
        "artifacts": {
            "csv": str(csv_path),
            "json": str(json_path),
        },
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"Saved CSV:  {csv_path}")
    print(f"Saved JSON: {json_path}")
    print(f"Rows tested: {tested}, API ok: {len(ok_rows)}, ingestion ok: {len(ingestion_ok_rows)}")
    print(
        f"Gate ingestion_success_rate: {ingestion_success_rate:.3f} "
        f"(threshold >= {args.min_ingestion_success:.3f}) pass={gates['ingestion_success_rate']['pass']}"
    )
    print(
        f"Gate not_enough_evidence_rate: {not_enough_rate:.3f} "
        f"(threshold <= {args.max_not_enough_rate:.3f}) pass={gates['not_enough_evidence_rate']['pass']}"
    )
    print(
        f"Gate score_stddev: {score_stddev:.3f} "
        f"(threshold >= {args.min_score_stddev:.3f}) pass={gates['score_stddev']['pass']}"
    )
    if queue_reliability is None:
        print("Gate queue_reliability: not evaluated (set --queue-total/--queue-completed to evaluate)")
    else:
        print(
            f"Gate queue_reliability: {queue_reliability:.3f} "
            f"(threshold >= {args.min_queue_reliability:.3f}) pass={gates['queue_reliability']['pass']}"
        )
    print(f"Result core_pass={core_pass} overall_pass={overall_pass}")


if __name__ == "__main__":
    main()
