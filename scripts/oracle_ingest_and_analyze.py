#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.debug_state import get_debug_notes, reset_debug_notes
from app.services.ingestion import enrich_from_youtube


def extract_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if host == "youtu.be" and path:
        return path.split("/")[0]

    if "youtube.com" in host:
        if path.startswith("shorts/"):
            parts = path.split("/")
            if len(parts) > 1 and parts[1]:
                return parts[1]
        if path == "watch":
            vals = parse_qs(parsed.query).get("v")
            if vals:
                return vals[0]
    return None


def normalize_youtube_url(url: str) -> str:
    cleaned = re.sub(r"\\([?=&/])", r"\1", (url or "").strip())
    vid = extract_video_id(cleaned)
    if not vid:
        return cleaned
    return f"https://www.youtube.com/shorts/{vid}"


async def process_one(api_url: str, url: str, timeout_sec: int, skip_no_text: bool) -> dict:
    normalized_url = normalize_youtube_url(url)
    reset_debug_notes()

    caption, transcript, ingest_notes = await enrich_from_youtube(normalized_url, worker_mode=True)
    debug_notes = get_debug_notes()

    if skip_no_text and not caption.strip() and not transcript.strip():
        return {
            "url": normalized_url,
            "error": "no_ingested_text",
            "ingest_notes": " | ".join(ingest_notes[:8]),
            "debug_notes": " | ".join(debug_notes[:8]),
        }

    payload = {
        "url": normalized_url,
        "caption": caption,
        "transcript": transcript,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(api_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {
            "url": normalized_url,
            "error": f"api_error:{type(exc).__name__}",
            "caption_chars": len(caption),
            "transcript_chars": len(transcript),
            "ingest_notes": " | ".join(ingest_notes[:8]),
            "debug_notes": " | ".join(debug_notes[:8]),
        }

    flags = {f.get("type"): f for f in data.get("flags", [])}
    gen = flags.get("generation_origin", {})
    claim = (data.get("claim_assessments") or [{}])[0]
    evidence = data.get("evidence_coverage") or {}

    return {
        "url": normalized_url,
        "credibility_score": data.get("credibility_score"),
        "generation_origin_level": gen.get("level"),
        "generation_origin_score": gen.get("score"),
        "evidence_level": evidence.get("level"),
        "evidence_total_tokens": evidence.get("total_tokens"),
        "evidence_caption_tokens": evidence.get("caption_tokens"),
        "evidence_transcript_tokens": evidence.get("transcript_tokens"),
        "evidence_ocr_tokens": evidence.get("ocr_tokens"),
        "evidence_asr_tokens": evidence.get("asr_tokens"),
        "top_claim_status": claim.get("status"),
        "top_claim_confidence": claim.get("confidence"),
        "caption_chars": len(caption),
        "transcript_chars": len(transcript),
        "api_notes": " | ".join((data.get("notes") or [])[:8]),
        "ingest_notes": " | ".join(ingest_notes[:8]),
        "debug_notes": " | ".join(debug_notes[:8]),
    }


async def run_batch(
    api_url: str,
    links: list[str],
    timeout_sec: int,
    concurrency: int,
    skip_no_text: bool,
) -> list[dict]:
    sem = asyncio.Semaphore(max(1, concurrency))
    out: list[dict] = [{} for _ in links]

    async def _runner(i: int, link: str) -> None:
        async with sem:
            out[i] = await process_one(
                api_url=api_url,
                url=link,
                timeout_sec=timeout_sec,
                skip_no_text=skip_no_text,
            )

    tasks = [asyncio.create_task(_runner(i, u)) for i, u in enumerate(links)]
    await asyncio.gather(*tasks)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run ingestion locally (Oracle/PC) and send extracted caption/transcript to CrediClip API"
    )
    parser.add_argument("--links-file", required=True, help="Text file, one YouTube Shorts URL per line")
    parser.add_argument("--api-url", default="https://crediclip-axraza-msba.fly.dev/api/analyze")
    parser.add_argument("--outdir", default="reports")
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--skip-no-text", action="store_true")
    args = parser.parse_args()

    links = [ln.strip() for ln in Path(args.links_file).read_text().splitlines()]
    links = [u for u in links if u and not u.startswith("#")]
    if not links:
        raise ValueError("No links found in links file.")

    rows = asyncio.run(
        run_batch(
            api_url=args.api_url,
            links=links,
            timeout_sec=args.timeout_sec,
            concurrency=args.concurrency,
            skip_no_text=args.skip_no_text,
        )
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    json_path = outdir / f"oracle_worker_{len(rows)}_{ts}.json"
    csv_path = outdir / f"oracle_worker_{len(rows)}_{ts}.csv"

    json_path.write_text(json.dumps({"api_url": args.api_url, "results": rows}, indent=2) + "\n")
    fieldnames = sorted({k for r in rows for k in r.keys()})
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    ok = [r for r in rows if "error" not in r]
    with_text = [r for r in rows if r.get("caption_chars", 0) or r.get("transcript_chars", 0)]
    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV:  {csv_path}")
    print(f"Success: {len(ok)}/{len(rows)}")
    print(f"Rows with ingested text: {len(with_text)}/{len(rows)}")


if __name__ == "__main__":
    main()
