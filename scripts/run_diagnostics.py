from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_command(label: str, cmd: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    output = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode == 0:
        return True, f"[PASS] {label}"
    detail = output.splitlines()[-1] if output else f"exit={proc.returncode}"
    return False, f"[FAIL] {label}: {detail}"


def run_api_checks() -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    with tempfile.TemporaryDirectory(prefix="crediclip-diagnostics-") as tmpdir:
        os.environ["JOBS_DB_PATH"] = str(Path(tmpdir) / "jobs.db")

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            for path in ["/", "/dashboard", "/api/queue/stats"]:
                resp = client.get(path)
                results.append((resp.status_code == 200, f"{path} -> {resp.status_code}"))

            analyze_payload = {
                "url": "https://www.tiktok.com/@demo/video/1234567890",
                "caption": "This AI generated clip promises guaranteed return investing.",
                "transcript": "This AI generated clip promises guaranteed return investing with no risk.",
            }
            resp = client.post("/api/analyze", json=analyze_payload)
            ok = resp.status_code == 200
            if ok:
                data = resp.json()
                ok = (
                    data.get("platform") == "tiktok"
                    and isinstance(data.get("credibility_score"), (int, float))
                    and len(data.get("flags", [])) == 6
                )
            results.append((ok, f"/api/analyze -> {resp.status_code}"))

            create_resp = client.post(
                "/api/jobs",
                json={"url": "https://www.youtube.com/shorts/diag12345678"},
            )
            create_ok = create_resp.status_code == 200
            job_id = create_resp.json().get("id") if create_ok else None
            results.append((create_ok and bool(job_id), f"/api/jobs create -> {create_resp.status_code}"))

            duplicate_create_resp = client.post(
                "/api/jobs",
                json={"url": "https://www.youtube.com/shorts/diag12345678"},
            )
            duplicate_create_ok = (
                duplicate_create_resp.status_code == 200
                and duplicate_create_resp.json().get("id") == job_id
                and duplicate_create_resp.json().get("status") == "queued"
            )
            results.append(
                (
                    duplicate_create_ok,
                    f"/api/jobs duplicate queued reuse -> {duplicate_create_resp.status_code}",
                )
            )

            claim_resp = client.post("/api/jobs/claim", json={"worker_id": "diagnostic-worker"})
            claim_ok = claim_resp.status_code == 200 and claim_resp.json().get("job", {}).get("id") == job_id
            results.append((claim_ok, f"/api/jobs/claim -> {claim_resp.status_code}"))

            yt_job_resp = client.post(
                "/api/jobs",
                json={"url": "https://www.youtube.com/shorts/filterdiag123"},
            )
            ig_job_resp = client.post(
                "/api/jobs",
                json={"url": "https://www.instagram.com/reel/filterdiag123/"},
            )
            filter_claim_resp = client.post(
                "/api/jobs/claim",
                json={
                    "worker_id": "diagnostic-filter-worker",
                    "exclude_platforms": ["youtube_shorts"],
                },
            )
            filter_claim_job = filter_claim_resp.json().get("job") if filter_claim_resp.status_code == 200 else {}
            filter_ok = (
                yt_job_resp.status_code == 200
                and ig_job_resp.status_code == 200
                and filter_claim_resp.status_code == 200
                and filter_claim_job.get("url") == "https://www.instagram.com/reel/filterdiag123/"
            )
            results.append((filter_ok, f"/api/jobs/claim platform filter -> {filter_claim_job.get('url')}"))

            filter_job_id = filter_claim_job.get("id")
            yt_job_id = yt_job_resp.json().get("id") if yt_job_resp.status_code == 200 else None
            if filter_job_id:
                client.post(f"/api/jobs/{filter_job_id}/fail", json={"error": "diagnostic_filter_cleanup"})
            if yt_job_id:
                client.post(f"/api/jobs/{yt_job_id}/fail", json={"error": "diagnostic_filter_cleanup"})

            complete_payload = {
                "caption": "caption text",
                "transcript": "transcript text",
                "ingest_notes": ["diagnostic"],
                "debug_notes": ["diagnostic"],
                "result": {
                    "platform": "youtube_shorts",
                    "credibility_score": 42,
                    "flags": [
                        {"type": "misinformation", "level": "low", "score": 10, "rationale": "diagnostic"},
                        {"type": "scam", "level": "low", "score": 10, "rationale": "diagnostic"},
                        {"type": "manipulation", "level": "low", "score": 10, "rationale": "diagnostic"},
                        {"type": "uncertainty", "level": "low", "score": 10, "rationale": "diagnostic"},
                        {"type": "generation_origin", "level": "low", "score": 10, "rationale": "diagnostic"},
                        {"type": "evidence_quality", "level": "low", "score": 10, "rationale": "diagnostic"},
                    ],
                    "claim_assessments": [],
                    "component_scores": {
                        "misinformation": 10,
                        "scam": 10,
                        "manipulation": 10,
                        "uncertainty": 10,
                        "generation_origin": 10,
                        "evidence_quality": 10,
                    },
                    "evidence_coverage": {
                        "total_tokens": 2,
                        "caption_tokens": 1,
                        "transcript_tokens": 1,
                        "ocr_tokens": 0,
                        "asr_tokens": 0,
                        "level": "low",
                        "transcript_present": True,
                        "ocr_present": False,
                        "asr_present": False,
                    },
                    "notes": ["diagnostic"],
                },
            }
            complete_resp = client.post(f"/api/jobs/{job_id}/complete", json=complete_payload)
            complete_ok = complete_resp.status_code == 200 and complete_resp.json().get("status") == "completed"
            results.append((complete_ok, f"/api/jobs/{{id}}/complete -> {complete_resp.status_code}"))

            completed_reuse_resp = client.post(
                "/api/jobs",
                json={"url": "https://www.youtube.com/shorts/diag12345678"},
            )
            completed_reuse_ok = (
                completed_reuse_resp.status_code == 200
                and completed_reuse_resp.json().get("id") == job_id
                and completed_reuse_resp.json().get("status") == "completed"
            )
            results.append(
                (
                    completed_reuse_ok,
                    f"/api/jobs recent completed reuse -> {completed_reuse_resp.status_code}",
                )
            )

            stats_resp = client.get("/api/queue/stats")
            stats = stats_resp.json() if stats_resp.status_code == 200 else {}
            counts = stats.get("counts", {})
            stats_ok = (
                stats_resp.status_code == 200
                and counts.get("queued") == 0
                and counts.get("processing") == 0
                and counts.get("completed") == 1
            )
            results.append((stats_ok, f"/api/queue/stats isolated counts -> {counts}"))

            scoring_cases = {
                "human_neutral": {
                    "url": "https://www.tiktok.com/@demo/video/1111111111",
                    "caption": "A wildlife educator explains how octopuses camouflage in coral reefs.",
                    "transcript": "Octopuses change color using chromatophores and can blend into reefs to avoid predators.",
                },
                "scammy": {
                    "url": "https://www.tiktok.com/@demo/video/2222222222",
                    "caption": "Limited time crypto giveaway. Guaranteed returns if you act now and DM me.",
                    "transcript": "This investment doubles your money with no risk. Send payment now.",
                },
                "sparse_evidence": {
                    "url": "https://www.tiktok.com/@demo/video/3333333333",
                    "caption": "Amazing discovery.",
                    "transcript": "",
                },
            }
            scores: dict[str, float] = {}
            for label, payload in scoring_cases.items():
                resp = client.post("/api/analyze", json=payload)
                ok = resp.status_code == 200 and isinstance(resp.json().get("credibility_score"), (int, float))
                if ok:
                    scores[label] = float(resp.json()["credibility_score"])
                results.append((ok, f"/api/analyze scoring case {label} -> {resp.status_code}"))

            consistency_ok = (
                scores.get("human_neutral", 0.0) > scores.get("scammy", 100.0)
                and 45.0 <= scores.get("sparse_evidence", 0.0) <= 60.0
            )
            results.append((consistency_ok, f"scoring consistency -> {scores}"))

            worker_evidence_payload = {
                "url": "https://www.instagram.com/reel/diagworker123/",
                "caption": "A narrated demo reel about city transit planning.",
                "transcript": "The narrator explains how protected bike lanes reduce traffic injuries.",
                "ingest_evidence": {
                    "total_tokens": 120,
                    "caption_tokens": 18,
                    "transcript_tokens": 72,
                    "ocr_tokens": 12,
                    "asr_tokens": 60,
                    "level": "high",
                    "transcript_present": True,
                    "ocr_present": True,
                    "asr_present": True,
                },
            }
            worker_evidence_resp = client.post("/api/analyze", json=worker_evidence_payload)
            worker_evidence_data = worker_evidence_resp.json() if worker_evidence_resp.status_code == 200 else {}
            worker_evidence = worker_evidence_data.get("evidence_coverage") or {}
            worker_evidence_ok = (
                worker_evidence_resp.status_code == 200
                and worker_evidence.get("asr_tokens") == 60
                and worker_evidence.get("ocr_tokens") == 12
                and worker_evidence.get("asr_present") is True
                and worker_evidence.get("ocr_present") is True
            )
            results.append(
                (
                    worker_evidence_ok,
                    f"worker ingest_evidence preservation -> {worker_evidence}",
                )
            )

            from app.services.instagram_ingestion import (
                _looks_like_instagram_app_shell,
                extract_instagram_metadata_from_html,
            )

            instagram_html = """
            <html>
              <head>
                <title>Travel Reel</title>
                <meta property="og:title" content="Hidden beach travel reel" />
                <meta property="og:description" content="A quick guide to a hidden beach in Portugal." />
                <meta property="instapp:owner_user_name" content="travel.demo" />
                <script type="application/ld+json">
                  {"@type":"VideoObject","description":"A quick guide to a hidden beach in Portugal.","author":{"name":"travel.demo"}}
                </script>
              </head>
            </html>
            """
            parsed = extract_instagram_metadata_from_html(instagram_html)
            instagram_ok = (
                parsed.get("title") == "Hidden beach travel reel"
                and "hidden beach in Portugal" in (parsed.get("description") or "")
                and parsed.get("author") == "travel.demo"
            )
            results.append((instagram_ok, f"instagram metadata parser -> {parsed}"))

            instagram_json_html = r"""
            <html>
              <head>
                <title>Instagram</title>
                <script>
                  window.__additionalDataLoaded('/reel/demo/', {"graphql":{"shortcode_media":{
                    "owner":{"username":"science.demo"},
                    "edge_media_to_caption":{"edges":[{"node":{"text":"A narrated reel explaining how jellyfish pulse through ocean currents."}}]},
                    "accessibility_caption":"Image may contain: jellyfish floating in blue water"
                  }}});
                </script>
              </head>
              <body>
                <blockquote>Instagram post fallback text.</blockquote>
              </body>
            </html>
            """
            parsed_json = extract_instagram_metadata_from_html(instagram_json_html)
            instagram_json_ok = (
                parsed_json.get("author") == "science.demo"
                and "jellyfish pulse through ocean currents" in (parsed_json.get("description") or "")
            )
            results.append((instagram_json_ok, f"instagram json parser -> {parsed_json}"))

            instagram_shell_html = """
            <html>
              <head><title>Instagram</title></head>
              <body>
                <script type="application/json">
                  {"route":{"pageID":"httpErrorPage"}}
                </script>
              </body>
            </html>
            """
            shell_ok = _looks_like_instagram_app_shell(instagram_shell_html)
            results.append((shell_ok, "instagram app-shell detection"))

            from app.services.tiktok_ingestion import (
                _normalize_tiktok_media_url,
                _tiktok_playwright_cookies,
                _looks_like_tiktok_app_shell,
                extract_tiktok_metadata_from_html,
            )

            tiktok_html = """
            <html>
              <head>
                <title>City transit explainer | TikTok</title>
                <meta property="og:title" content="City transit explainer" />
                <meta property="og:description" content="A short explainer about bus lanes and safer intersections." />
                <script type="application/ld+json">
                  {"@type":"VideoObject","description":"A short explainer about bus lanes and safer intersections.","author":{"name":"urban.demo"}}
                </script>
              </head>
            </html>
            """
            parsed_tiktok = extract_tiktok_metadata_from_html(tiktok_html)
            tiktok_ok = (
                parsed_tiktok.get("title") == "City transit explainer"
                and "bus lanes and safer intersections" in (parsed_tiktok.get("description") or "")
                and parsed_tiktok.get("author") == "urban.demo"
            )
            results.append((tiktok_ok, f"tiktok metadata parser -> {parsed_tiktok}"))

            tiktok_shell_html = """
            <html>
              <head><title>TikTok - Make Your Day</title></head>
              <body></body>
            </html>
            """
            tiktok_shell_ok = _looks_like_tiktok_app_shell(tiktok_shell_html)
            results.append((tiktok_shell_ok, "tiktok app-shell detection"))

            tiktok_media_ok = (
                _normalize_tiktok_media_url("//v16.tiktokcdn.com/video/tos/useast/test.mp4")
                == "https://v16.tiktokcdn.com/video/tos/useast/test.mp4"
            )
            results.append((tiktok_media_ok, "tiktok media-url normalization"))

            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as cookie_file:
                cookie_file.write(
                    ".tiktok.com\tTRUE\t/\tTRUE\t1760000000\tsessionid\tdemo\n"
                    ".example.com\tTRUE\t/\tTRUE\t1760000000\tother\tvalue\n"
                )
                cookie_path = cookie_file.name
            tiktok_cookie_domains = _tiktok_playwright_cookies(cookie_path)
            tiktok_cookie_ok = len(tiktok_cookie_domains) == 1 and tiktok_cookie_domains[0]["name"] == "sessionid"
            results.append((tiktok_cookie_ok, "tiktok cookie filter"))

            from app.services.claim_checker import assess_claims
            from app.services.llm_claims import llm_provider_label
            from app.services.ingestion import _openai_transcription_enabled, _openai_transcription_model

            prior_mode = os.environ.get("CLAIM_LLM_MODE")
            prior_key = os.environ.get("GROQ_API_KEY")
            os.environ["CLAIM_LLM_MODE"] = "groq"
            os.environ["GROQ_API_KEY"] = "diagnostic-key"
            try:
                gated_claims, gated_notes = asyncio.run(
                    assess_claims(
                        ["The ocean fact is definitely true."],
                        source_text="Tiny caption only",
                        transcript_present=False,
                        evidence_level="low",
                    )
                )
            finally:
                if prior_mode is None:
                    os.environ.pop("CLAIM_LLM_MODE", None)
                else:
                    os.environ["CLAIM_LLM_MODE"] = prior_mode
                if prior_key is None:
                    os.environ.pop("GROQ_API_KEY", None)
                else:
                    os.environ["GROQ_API_KEY"] = prior_key

            groq_gate_ok = (
                len(gated_claims) == 1
                and any("Groq claim verification skipped" in note for note in gated_notes)
            )
            results.append((groq_gate_ok, f"groq sparse-evidence gate -> {gated_notes}"))

            openai_mode = os.environ.get("CLAIM_LLM_MODE")
            openai_key = os.environ.get("OPENAI_API_KEY")
            openai_platforms = os.environ.get("OPENAI_CLAIM_ALLOWED_PLATFORMS")
            openai_min_tokens = os.environ.get("OPENAI_CLAIM_MIN_TOKENS")
            openai_min_level = os.environ.get("OPENAI_CLAIM_MIN_EVIDENCE_LEVEL")
            os.environ["CLAIM_LLM_MODE"] = "openai"
            os.environ["OPENAI_API_KEY"] = "diagnostic-openai-key"
            os.environ["OPENAI_CLAIM_ALLOWED_PLATFORMS"] = "instagram"
            os.environ["OPENAI_CLAIM_MIN_TOKENS"] = "60"
            os.environ["OPENAI_CLAIM_MIN_EVIDENCE_LEVEL"] = "medium"
            try:
                openai_provider_ok = llm_provider_label() == "openai"
                openai_gate_claims, openai_gate_notes = asyncio.run(
                    assess_claims(
                        ["This is definitely true."],
                        source_text="Dense enough transcript text to satisfy the token threshold for testing only.",
                        transcript_present=True,
                        evidence_level="high",
                        platform="tiktok",
                    )
                )
            finally:
                if openai_mode is None:
                    os.environ.pop("CLAIM_LLM_MODE", None)
                else:
                    os.environ["CLAIM_LLM_MODE"] = openai_mode
                if openai_key is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = openai_key
                if openai_platforms is None:
                    os.environ.pop("OPENAI_CLAIM_ALLOWED_PLATFORMS", None)
                else:
                    os.environ["OPENAI_CLAIM_ALLOWED_PLATFORMS"] = openai_platforms
                if openai_min_tokens is None:
                    os.environ.pop("OPENAI_CLAIM_MIN_TOKENS", None)
                else:
                    os.environ["OPENAI_CLAIM_MIN_TOKENS"] = openai_min_tokens
                if openai_min_level is None:
                    os.environ.pop("OPENAI_CLAIM_MIN_EVIDENCE_LEVEL", None)
                else:
                    os.environ["OPENAI_CLAIM_MIN_EVIDENCE_LEVEL"] = openai_min_level
            results.append((openai_provider_ok, "openai provider selection"))
            openai_gate_ok = any("OpenAI claim verification skipped" in note for note in openai_gate_notes)
            results.append((openai_gate_ok, f"openai platform/evidence gate -> {openai_gate_notes}"))

            transcription_provider = os.environ.get("TRANSCRIPTION_PROVIDER")
            transcription_key = os.environ.get("OPENAI_API_KEY")
            transcription_model = os.environ.get("OPENAI_TRANSCRIPTION_MODEL")
            os.environ["TRANSCRIPTION_PROVIDER"] = "openai"
            os.environ["OPENAI_API_KEY"] = "diagnostic-openai-key"
            os.environ["OPENAI_TRANSCRIPTION_MODEL"] = "gpt-4o-mini-transcribe"
            try:
                transcription_ok = _openai_transcription_enabled() and _openai_transcription_model() == "gpt-4o-mini-transcribe"
            finally:
                if transcription_provider is None:
                    os.environ.pop("TRANSCRIPTION_PROVIDER", None)
                else:
                    os.environ["TRANSCRIPTION_PROVIDER"] = transcription_provider
                if transcription_key is None:
                    os.environ.pop("OPENAI_API_KEY", None)
                else:
                    os.environ["OPENAI_API_KEY"] = transcription_key
                if transcription_model is None:
                    os.environ.pop("OPENAI_TRANSCRIPTION_MODEL", None)
                else:
                    os.environ["OPENAI_TRANSCRIPTION_MODEL"] = transcription_model
            results.append((transcription_ok, "openai transcription provider selection"))

    return results


def main() -> int:
    os.chdir(ROOT)
    checks: list[tuple[bool, str]] = []

    checks.append(run_command("Python compileall", [sys.executable, "-m", "compileall", "app", "scripts"]))
    checks.append(run_command("pip check", [sys.executable, "-m", "pip", "check"]))
    checks.append(run_command("node --check app.js", ["node", "--check", "app/static/app.js"]))
    checks.append(run_command("node --check dashboard.js", ["node", "--check", "app/static/dashboard.js"]))
    checks.extend(run_api_checks())

    for ok, message in checks:
        prefix = "[PASS]" if ok else "[FAIL]"
        if message.startswith("[PASS]") or message.startswith("[FAIL]"):
            print(message)
        else:
            print(f"{prefix} {message}")

    failures = [message for ok, message in checks if not ok]
    print(f"Summary: {len(checks) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
