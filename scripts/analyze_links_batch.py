#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


def run_api(api_url: str, url: str, timeout: int = 45) -> dict:
    clean_url = re.sub(r"\\([?=&/])", r"\1", url.strip())
    payload = json.dumps({"url": clean_url})
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
        return {"url": clean_url, "error": p.stderr.strip() or f"curl exit {p.returncode}"}

    try:
        data = json.loads(p.stdout)
    except Exception:
        return {"url": clean_url, "error": "invalid_json", "raw": p.stdout[:300]}

    flags = {f.get("type"): f for f in data.get("flags", [])}
    gen = flags.get("generation_origin", {})
    claim = (data.get("claim_assessments") or [{}])[0]
    evidence = data.get("evidence_coverage") or {}
    components = data.get("component_scores") or {}

    misinformation = _safe_float(components.get("misinformation"))
    scam = _safe_float(components.get("scam"))
    manipulation = _safe_float(components.get("manipulation"))
    uncertainty = _safe_float(components.get("uncertainty"))
    evidence_quality = _safe_float(components.get("evidence_quality"))
    credibility = _safe_float(data.get("credibility_score"))

    weighted_misinformation = (0.34 * misinformation) if misinformation is not None else None
    weighted_scam = (0.24 * scam) if scam is not None else None
    weighted_manipulation = (0.18 * manipulation) if manipulation is not None else None
    weighted_uncertainty = (0.12 * uncertainty) if uncertainty is not None else None
    weighted_evidence_quality = (0.12 * evidence_quality) if evidence_quality is not None else None
    weighted_total_penalty = None
    reconstructed_credibility = None
    if None not in (
        weighted_misinformation,
        weighted_scam,
        weighted_manipulation,
        weighted_uncertainty,
        weighted_evidence_quality,
    ):
        weighted_total_penalty = (
            weighted_misinformation
            + weighted_scam
            + weighted_manipulation
            + weighted_uncertainty
            + weighted_evidence_quality
        )
        reconstructed_credibility = 100.0 - weighted_total_penalty

    return {
        "url": clean_url,
        "credibility_score": data.get("credibility_score"),
        "generation_origin_level": gen.get("level"),
        "generation_origin_score": gen.get("score"),
        "generation_origin_rationale": gen.get("rationale"),
        "misinformation_score": components.get("misinformation"),
        "scam_score": components.get("scam"),
        "manipulation_score": components.get("manipulation"),
        "uncertainty_score": components.get("uncertainty"),
        "evidence_quality_score": components.get("evidence_quality"),
        "weighted_misinformation_penalty": round(weighted_misinformation, 4)
        if weighted_misinformation is not None
        else None,
        "weighted_scam_penalty": round(weighted_scam, 4) if weighted_scam is not None else None,
        "weighted_manipulation_penalty": round(weighted_manipulation, 4)
        if weighted_manipulation is not None
        else None,
        "weighted_uncertainty_penalty": round(weighted_uncertainty, 4)
        if weighted_uncertainty is not None
        else None,
        "weighted_evidence_quality_penalty": round(weighted_evidence_quality, 4)
        if weighted_evidence_quality is not None
        else None,
        "weighted_total_penalty": round(weighted_total_penalty, 4) if weighted_total_penalty is not None else None,
        "reconstructed_credibility_score": round(reconstructed_credibility, 2)
        if reconstructed_credibility is not None
        else None,
        "credibility_vs_reconstructed_delta": round(credibility - reconstructed_credibility, 4)
        if (credibility is not None and reconstructed_credibility is not None)
        else None,
        "generation_origin_excluded_from_credibility": True,
        "evidence_level": evidence.get("level"),
        "evidence_total_tokens": evidence.get("total_tokens"),
        "evidence_caption_tokens": evidence.get("caption_tokens"),
        "evidence_transcript_tokens": evidence.get("transcript_tokens"),
        "evidence_ocr_tokens": evidence.get("ocr_tokens"),
        "evidence_asr_tokens": evidence.get("asr_tokens"),
        "top_claim_status": claim.get("status"),
        "top_claim_confidence": claim.get("confidence"),
        "notes": " | ".join((data.get("notes") or [])[:6]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a batch of video links via /api/analyze")
    parser.add_argument("--api-url", default="https://crediclip-axraza-msba.fly.dev/api/analyze")
    parser.add_argument("--links-file", required=True, help="Text file with one URL per line")
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    links = [ln.strip() for ln in Path(args.links_file).read_text().splitlines()]
    links = [u for u in links if u and not u.startswith("#")]
    if not links:
        raise ValueError("No links found in links file.")

    workers = max(1, int(args.concurrency))
    rows: list[dict] = [None for _ in links]  # type: ignore[list-item]
    if workers == 1:
        rows = [run_api(args.api_url, u, timeout=args.timeout) for u in links]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {
                pool.submit(run_api, args.api_url, url, args.timeout): idx
                for idx, url in enumerate(links)
            }
            done = 0
            for future in as_completed(future_map):
                idx = future_map[future]
                rows[idx] = future.result()
                done += 1
                if done % 50 == 0 or done == len(links):
                    print(f"progress {done}/{len(links)}", flush=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / f"links_batch_{len(rows)}_{ts}.json"
    csv_path = outdir / f"links_batch_{len(rows)}_{ts}.csv"

    json_path.write_text(json.dumps({"api_url": args.api_url, "results": rows}, indent=2) + "\n")

    fields = sorted({k for r in rows for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if "error" not in r]
    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV:  {csv_path}")
    print(f"Success: {len(ok)}/{len(rows)}")


if __name__ == "__main__":
    main()
