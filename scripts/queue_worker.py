#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import AnalyzeRequest
from app.services.debug_state import get_debug_notes, reset_debug_notes
from app.services.instagram_ingestion import enrich_from_instagram
from app.services.tiktok_ingestion import enrich_from_tiktok
from app.services.ingestion import enrich_from_youtube
from app.services.pipeline import analyze_video, infer_platform, normalize_input_url


async def _ingest_with_debug(
    url: str, worker_mode: bool
) -> tuple[str, str, list[str], list[str], dict]:
    reset_debug_notes()
    normalized_url, _ = normalize_input_url(url)
    platform = infer_platform(normalized_url)
    if platform == "youtube_shorts":
        caption, transcript, ingest_notes, ingest_evidence = await enrich_from_youtube(
            normalized_url,
            worker_mode=worker_mode,
            include_evidence=True,
        )
    elif platform == "instagram":
        caption, transcript, ingest_notes, ingest_evidence = await enrich_from_instagram(
            normalized_url,
            worker_mode=worker_mode,
            include_evidence=True,
        )
    elif platform == "tiktok":
        caption, transcript, ingest_notes, ingest_evidence = await enrich_from_tiktok(
            normalized_url,
            worker_mode=worker_mode,
            include_evidence=True,
        )
    else:
        caption, transcript, ingest_notes, ingest_evidence = (
            "",
            "",
            ["Worker ingestion is not implemented for this platform."],
            {},
        )
    debug_notes = get_debug_notes()
    return caption, transcript, ingest_notes, debug_notes, ingest_evidence


async def _analyze_with_debug(request: AnalyzeRequest):
    reset_debug_notes()
    result = await analyze_video(request)
    debug_notes = get_debug_notes()
    return result, debug_notes


def _endpoint(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _claim_job(client: httpx.Client, api_base: str, worker_id: str) -> dict | None:
    payload = {"worker_id": worker_id}
    if getattr(_claim_job, "include_platforms", None):
        payload["include_platforms"] = list(getattr(_claim_job, "include_platforms"))
    if getattr(_claim_job, "exclude_platforms", None):
        payload["exclude_platforms"] = list(getattr(_claim_job, "exclude_platforms"))
    resp = client.post(_endpoint(api_base, "/api/jobs/claim"), json=payload)
    resp.raise_for_status()
    payload = resp.json()
    return payload.get("job")


def _post_artifacts(
    client: httpx.Client,
    api_base: str,
    job_id: str,
    caption: str,
    transcript: str,
    ingest_notes: list[str],
    debug_notes: list[str],
    ingest_evidence: dict,
) -> None:
    resp = client.post(
        _endpoint(api_base, f"/api/jobs/{job_id}/artifacts"),
        json={
            "caption": caption,
            "transcript": transcript,
            "ingest_notes": ingest_notes,
            "debug_notes": debug_notes,
            "ingest_evidence": ingest_evidence,
        },
    )
    resp.raise_for_status()


def _post_complete_result(
    client: httpx.Client,
    api_base: str,
    job_id: str,
    caption: str,
    transcript: str,
    ingest_notes: list[str],
    debug_notes: list[str],
    result: dict,
) -> None:
    resp = client.post(
        _endpoint(api_base, f"/api/jobs/{job_id}/complete"),
        json={
            "caption": caption,
            "transcript": transcript,
            "ingest_notes": ingest_notes,
            "debug_notes": debug_notes,
            "result": result,
        },
    )
    resp.raise_for_status()


def _fail_job(client: httpx.Client, api_base: str, job_id: str, error: str) -> None:
    try:
        client.post(_endpoint(api_base, f"/api/jobs/{job_id}/fail"), json={"error": error})
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Claim queued CrediClip jobs, ingest artifacts locally, upload results")
    parser.add_argument("--api-base", default="https://crediclip-axraza-msba.fly.dev")
    parser.add_argument("--worker-id", default="oracle-worker-1")
    parser.add_argument(
        "--ingest-mode",
        choices=["basic", "rich"],
        default="rich",
        help="basic=existing ingestion, rich=download video + whisper + frame scan + source-check",
    )
    parser.add_argument(
        "--analysis-mode",
        choices=["local", "server"],
        default="local",
        help="local=run full analysis on worker VM (uses local LLM), server=send artifacts and let API analyze",
    )
    parser.add_argument(
        "--ingest-timeout-sec",
        type=int,
        default=180,
        help="Max seconds allowed for one job ingestion before failing the job.",
    )
    parser.add_argument(
        "--analysis-timeout-sec",
        type=int,
        default=180,
        help="Max seconds allowed for one job analysis before failing the job.",
    )
    parser.add_argument("--poll-sec", type=float, default=3.0)
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument(
        "--include-platforms",
        default="",
        help="Comma-separated platforms this worker should claim (youtube_shorts,instagram,tiktok).",
    )
    parser.add_argument(
        "--exclude-platforms",
        default="",
        help="Comma-separated platforms this worker should avoid claiming.",
    )
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    args = parser.parse_args()

    _claim_job.include_platforms = [
        part.strip().lower() for part in args.include_platforms.split(",") if part.strip()
    ]
    _claim_job.exclude_platforms = [
        part.strip().lower() for part in args.exclude_platforms.split(",") if part.strip()
    ]

    processed = 0
    with httpx.Client(timeout=args.timeout_sec) as client:
        while True:
            try:
                job = _claim_job(client, args.api_base, args.worker_id)
            except Exception as exc:
                print(f"[worker] claim error: {type(exc).__name__}")
                time.sleep(args.poll_sec)
                continue

            if not job:
                if args.once:
                    print("[worker] no queued jobs")
                    return
                time.sleep(args.poll_sec)
                continue

            job_id = str(job.get("id"))
            url = str(job.get("url"))
            print(f"[worker] claimed job={job_id} url={url}")
            try:
                caption, transcript, ingest_notes, ingest_debug_notes, ingest_evidence = asyncio.run(
                    asyncio.wait_for(
                        _ingest_with_debug(url, worker_mode=(args.ingest_mode == "rich")),
                        timeout=max(30, args.ingest_timeout_sec),
                    )
                )

                if args.analysis_mode == "local":
                    result, analysis_debug_notes = asyncio.run(
                        asyncio.wait_for(
                            _analyze_with_debug(
                                AnalyzeRequest(
                                    url=url,
                                    caption=caption,
                                    transcript=transcript,
                                    ingest_evidence=ingest_evidence,
                                )
                            ),
                            timeout=max(30, args.analysis_timeout_sec),
                        )
                    )
                    merged_debug = ingest_debug_notes + [
                        n for n in analysis_debug_notes if n not in ingest_debug_notes
                    ]
                    try:
                        _post_complete_result(
                            client=client,
                            api_base=args.api_base,
                            job_id=job_id,
                            caption=caption,
                            transcript=transcript,
                            ingest_notes=ingest_notes,
                            debug_notes=merged_debug,
                            result=result.model_dump(),
                        )
                    except httpx.HTTPStatusError as exc:
                        # Backward-compatible fallback for servers without /complete endpoint.
                        if exc.response.status_code == 404:
                            print("[worker] /complete endpoint not found; falling back to server-side analysis mode.")
                            _post_artifacts(
                                client=client,
                            api_base=args.api_base,
                            job_id=job_id,
                            caption=caption,
                            transcript=transcript,
                            ingest_notes=ingest_notes,
                            debug_notes=ingest_debug_notes,
                            ingest_evidence=ingest_evidence,
                        )
                        else:
                            raise
                else:
                    _post_artifacts(
                        client=client,
                        api_base=args.api_base,
                        job_id=job_id,
                        caption=caption,
                        transcript=transcript,
                        ingest_notes=ingest_notes,
                        debug_notes=ingest_debug_notes,
                        ingest_evidence=ingest_evidence,
                    )
                print(
                    f"[worker] completed job={job_id} "
                    f"caption_chars={len(caption)} transcript_chars={len(transcript)}"
                )
            except asyncio.TimeoutError:
                _fail_job(client, args.api_base, job_id, "worker_timeout")
                print(f"[worker] failed job={job_id}: TimeoutError")
            except Exception as exc:
                _fail_job(client, args.api_base, job_id, f"worker_error:{type(exc).__name__}")
                print(f"[worker] failed job={job_id}: {type(exc).__name__}")

            processed += 1
            if args.once and processed >= 1:
                return


if __name__ == "__main__":
    main()
