#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import AnalyzeRequest
from app.services.pipeline import analyze_video


def _parse_analysis_confidence(notes: list[str]) -> float | None:
    for note in notes or []:
        match = re.search(r"Analysis-confidence score: ([0-9]+(?:\\.[0-9]+)?)", note)
        if match:
            return float(match.group(1))
    return None


def _run_one(url: str, timeout_sec: int) -> dict:
    clean_url = url.strip()
    if not clean_url:
        return {"url": clean_url, "error": "empty_url"}

    try:
        result = asyncio.run(asyncio.wait_for(analyze_video(AnalyzeRequest(url=clean_url)), timeout=max(30, timeout_sec)))
    except asyncio.TimeoutError:
        return {"url": clean_url, "error": f"timeout:{timeout_sec}s"}
    except Exception as exc:
        return {"url": clean_url, "error": f"{type(exc).__name__}: {exc}"}

    flags = {flag.type: flag for flag in result.flags}
    claim0 = result.claim_assessments[0] if result.claim_assessments else None
    notes = list(result.notes or [])

    return {
        "url": clean_url,
        "platform": result.platform,
        "credibility_score": result.credibility_score,
        "analysis_confidence": _parse_analysis_confidence(notes),
        "generation_origin_level": flags.get("generation_origin").level if flags.get("generation_origin") else None,
        "generation_origin_score": flags.get("generation_origin").score if flags.get("generation_origin") else None,
        "misinformation_score": result.component_scores.get("misinformation"),
        "scam_score": result.component_scores.get("scam"),
        "manipulation_score": result.component_scores.get("manipulation"),
        "uncertainty_score": result.component_scores.get("uncertainty"),
        "evidence_quality_score": result.component_scores.get("evidence_quality"),
        "evidence_level": result.evidence_coverage.level,
        "evidence_total_tokens": result.evidence_coverage.total_tokens,
        "evidence_caption_tokens": result.evidence_coverage.caption_tokens,
        "evidence_transcript_tokens": result.evidence_coverage.transcript_tokens,
        "evidence_ocr_tokens": result.evidence_coverage.ocr_tokens,
        "evidence_asr_tokens": result.evidence_coverage.asr_tokens,
        "top_claim_status": claim0.status if claim0 else None,
        "top_claim_confidence": claim0.confidence if claim0 else None,
        "top_claim_rationale": claim0.rationale if claim0 else None,
        "notes": " | ".join(notes[:8]),
    }


def _average(rows: list[dict], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _count_if(rows: list[dict], predicate) -> int:
    return sum(1 for row in rows if predicate(row))


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a local batch of video links with the current app code.")
    parser.add_argument("--links-file", required=True, help="Text file with one URL per line")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--outdir", default="reports")
    args = parser.parse_args()

    urls = [line.strip() for line in Path(args.links_file).read_text().splitlines()]
    urls = [url for url in urls if url and not url.startswith("#")]
    if not urls:
        raise ValueError("No links found in links file.")

    rows: list[dict | None] = [None] * len(urls)
    workers = max(1, int(args.workers))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_run_one, url, args.timeout_sec): idx for idx, url in enumerate(urls)}
        done = 0
        for future in as_completed(future_map):
            idx = future_map[future]
            rows[idx] = future.result()
            done += 1
            if done % 50 == 0 or done == len(urls):
                print(f"progress {done}/{len(urls)}", flush=True)

    completed_rows = [row for row in rows if row is not None]
    ok_rows = [row for row in completed_rows if "error" not in row]
    error_rows = [row for row in completed_rows if "error" in row]

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / f"user_yt_batch_{len(completed_rows)}_{ts}.csv"
    json_path = outdir / f"user_yt_batch_{len(completed_rows)}_{ts}.json"
    summary_path = outdir / f"user_yt_batch_{len(completed_rows)}_{ts}_summary.json"

    json_path.write_text(json.dumps({"results": completed_rows}, indent=2) + "\n")
    fieldnames = sorted({key for row in completed_rows for key in row.keys()})
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(completed_rows)

    summary = {
        "tested": len(completed_rows),
        "success": len(ok_rows),
        "errors": len(error_rows),
        "avg_credibility_score": _average(ok_rows, "credibility_score"),
        "avg_analysis_confidence": _average(ok_rows, "analysis_confidence"),
        "avg_evidence_total_tokens": _average(ok_rows, "evidence_total_tokens"),
        "avg_misinformation_score": _average(ok_rows, "misinformation_score"),
        "avg_scam_score": _average(ok_rows, "scam_score"),
        "avg_manipulation_score": _average(ok_rows, "manipulation_score"),
        "avg_uncertainty_score": _average(ok_rows, "uncertainty_score"),
        "avg_evidence_quality_score": _average(ok_rows, "evidence_quality_score"),
        "evidence_level_counts": {
            "low": _count_if(ok_rows, lambda row: row.get("evidence_level") == "low"),
            "medium": _count_if(ok_rows, lambda row: row.get("evidence_level") == "medium"),
            "high": _count_if(ok_rows, lambda row: row.get("evidence_level") == "high"),
        },
        "top_claim_status_counts": {
            "supported": _count_if(ok_rows, lambda row: row.get("top_claim_status") == "supported"),
            "refuted": _count_if(ok_rows, lambda row: row.get("top_claim_status") == "refuted"),
            "not_enough_evidence": _count_if(
                ok_rows, lambda row: row.get("top_claim_status") == "not_enough_evidence"
            ),
            "missing": _count_if(ok_rows, lambda row: not row.get("top_claim_status")),
        },
        "score_bands": {
            "lt_45": _count_if(ok_rows, lambda row: float(row.get("credibility_score", 0)) < 45),
            "45_to_60": _count_if(
                ok_rows, lambda row: 45 <= float(row.get("credibility_score", 0)) < 60
            ),
            "60_to_75": _count_if(
                ok_rows, lambda row: 60 <= float(row.get("credibility_score", 0)) < 75
            ),
            "ge_75": _count_if(ok_rows, lambda row: float(row.get("credibility_score", 0)) >= 75),
        },
        "artifacts": {
            "csv": str(csv_path),
            "json": str(json_path),
            "summary": str(summary_path),
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
