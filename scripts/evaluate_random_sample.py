#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run_api(api_url: str, video_id: str, timeout: int = 35) -> dict:
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
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        return {"video_id": video_id, "url": url, "error": p.stderr.strip() or f"curl exit {p.returncode}"}

    try:
        data = json.loads(p.stdout)
    except Exception:
        return {"video_id": video_id, "url": url, "error": "invalid_json", "raw": p.stdout[:200]}

    gen = next((f for f in data.get("flags", []) if f.get("type") == "generation_origin"), {})
    claim0 = (data.get("claim_assessments") or [{}])[0]
    notes = data.get("notes", [])
    return {
        "video_id": video_id,
        "url": url,
        "credibility_score": data.get("credibility_score"),
        "generation_origin_level": gen.get("level"),
        "generation_origin_score": gen.get("score"),
        "generation_origin_rationale": gen.get("rationale"),
        "top_claim_status": claim0.get("status"),
        "top_claim_confidence": claim0.get("confidence"),
        "trained_override": any("Generation origin calibrated by labeled training sample" in n for n in notes),
        "openrouter_used": any("Claim verification used OpenRouter LLM." in n for n in notes),
        "notes": " | ".join(notes[:6]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate random sample of trained AI Shorts IDs against live API")
    parser.add_argument("--labels", default="app/data/generation_labels.json")
    parser.add_argument("--api-url", default="https://crediclip-axraza-msba.fly.dev/api/analyze")
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260225)
    parser.add_argument("--outdir", default="reports")
    args = parser.parse_args()

    labels = json.loads(Path(args.labels).read_text())
    video_ids = list(labels.keys())
    if args.n > len(video_ids):
        raise ValueError(f"Requested n={args.n} but only {len(video_ids)} labels available")

    random.seed(args.seed)
    sample_ids = random.sample(video_ids, args.n)

    rows = [run_api(args.api_url, vid) for vid in sample_ids]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    json_path = outdir / f"validation_{args.n}_{ts}.json"
    csv_path = outdir / f"validation_{args.n}_{ts}.csv"

    summary = {
        "timestamp_utc": ts,
        "n": args.n,
        "seed": args.seed,
        "api_url": args.api_url,
        "sample_ids": sample_ids,
        "results": rows,
    }
    json_path.write_text(json.dumps(summary, indent=2) + "\n")

    fieldnames = sorted({k for r in rows for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    ok = [r for r in rows if "error" not in r]
    high = [r for r in ok if r.get("generation_origin_level") == "high"]
    not_enough = [r for r in ok if r.get("top_claim_status") == "not_enough_evidence"]

    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV:  {csv_path}")
    print(f"Success: {len(ok)}/{len(rows)}")
    print(f"generation_origin=high: {len(high)}/{len(ok) if ok else 0}")
    print(f"top_claim_status=not_enough_evidence: {len(not_enough)}/{len(ok) if ok else 0}")


if __name__ == "__main__":
    main()
