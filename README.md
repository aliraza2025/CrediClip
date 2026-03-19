# CrediClip MVP

CrediClip is an AI credibility scoring prototype for TikTok, Instagram, and YouTube Shorts videos.
This MVP accepts a public URL and returns:
- Credibility score (0-100)
- Risk flags (misinformation, scam, manipulation, uncertainty)
- Claim-level assessments
- Evidence coverage breakdown (`caption/transcript/ocr/asr` token counts + level)

Link-only behavior:
- YouTube Shorts: supported (auto metadata + transcript ingestion)
- Instagram: supported via queue-backed worker ingest
- TikTok: supported via queue-backed worker ingest (Phase 3 baseline)

## Stack
- FastAPI backend
- Vanilla HTML/CSS/JS frontend
- Open-source scoring pipeline with retrieval-based claim verification and optional external deepfake API integration

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

Local prerequisites for ASR/OCR fallback:
- `ffmpeg`
- `tesseract`

## Diagnostics

Run the local diagnostic suite from project root:

```bash
source .venv/bin/activate
python scripts/run_diagnostics.py
```

This checks:
- Python bytecode compilation for `app/` and `scripts/`
- dependency consistency via `pip check`
- frontend JS syntax for the analyzer and dashboard bundles
- FastAPI smoke routes and a full isolated queue workflow using a temporary `JOBS_DB_PATH`

The diagnostic runner does not write to `app/data/jobs.db`.

## API

### `POST /api/analyze`

Example payload:

```json
{
  "url": "https://www.tiktok.com/@sample/video/123",
  "caption": "Limited time crypto giveaway. Guaranteed return!",
  "transcript": "Act now and DM me for guaranteed return."
}
```

Link-only YouTube Shorts example:

```json
{
  "url": "https://www.youtube.com/shorts/VIDEO_ID"
}
```

Response includes an `evidence_coverage` object:
- `total_tokens`
- `caption_tokens`
- `transcript_tokens`
- `ocr_tokens`
- `asr_tokens`
- `level` (`low`/`medium`/`high`)

## Queue Architecture (v2)

CrediClip now supports asynchronous job processing:

1. Client submits URL -> `POST /api/jobs`
2. Worker claims queued job -> `POST /api/jobs/claim`
3. Worker ingests caption/transcript/OCR locally (rich mode can also do frame scan + source-check) and uploads artifacts -> `POST /api/jobs/{id}/artifacts`
4. Server computes final scoring and stores result
5. Client checks status/result -> `GET /api/jobs/{id}`

Production YouTube Shorts can also use this worker flow behind the synchronous `POST /api/analyze` route:
- set `YOUTUBE_ANALYZE_VIA_QUEUE=1`
- keep the Fly web node light with `INGEST_MODE=light` and `ENABLE_HEAVY_INGESTION=0`
- let the Oracle worker perform rich ingest and upload artifacts for final scoring

Production Instagram link-only analysis can use the same worker flow:
- set `INSTAGRAM_ANALYZE_VIA_QUEUE=1`
- keep the Fly web node light
- let the Oracle worker perform browser-rendered ingest and upload artifacts for final scoring

Production TikTok link-only analysis can use the same worker flow:
- set `TIKTOK_ANALYZE_VIA_QUEUE=1`
- keep the Fly web node light
- let the Oracle worker perform browser-rendered ingest and upload artifacts for final scoring
- use `TIKTOK_COOKIE_FILE` on the worker if public TikTok pages are thin or login-gated

Production queue persistence on Fly:
- set `JOBS_DB_PATH=/data/jobs.db`
- mount a Fly volume at `/data`
- keep queue state on the mounted volume so long benchmark runs survive deploys/restarts

This separates:
- Control plane (Fly app: scoring + API + report state)
- Data plane (Oracle/local worker: ingestion/extraction)

### Fly Free-Tier Memory Guard (recommended)

To prevent `OOM: uvicorn killed` on Fly free/small RAM machines:

- Keep heavy ingestion off the web API node:
  - `ENABLE_HEAVY_INGESTION=0`
- Limit concurrent in-process analysis:
  - `ANALYZE_MAX_CONCURRENCY=1`
- Add hard analysis timeout:
  - `ANALYZE_TIMEOUT_SEC=180`

Heavy media extraction (video download, Whisper ASR, frame OCR, browser-rendered fallback) should run on Oracle worker in `--ingest-mode rich`.
Recommended production split:
- web app: request intake, queue orchestration, scoring from provided artifacts
- worker: rich media ingestion and platform-specific extraction

### Endpoints

- `POST /api/jobs`
- `GET /api/jobs?status=queued|processing|completed|failed&limit=50`
- `GET /api/queue/stats`
- `POST /api/jobs/claim`
- `POST /api/jobs/{job_id}/artifacts`
- `POST /api/jobs/{job_id}/fail`
- `GET /api/jobs/{job_id}`

Live dashboard page:
- `/dashboard`

### Batch submit URLs to queue

```bash
python scripts/submit_jobs_batch.py \
  --links-file /tmp/links20.txt \
  --api-base "https://crediclip-axraza-msba.fly.dev"
```

### Run queue worker (Oracle/local)

```bash
PYTHONPATH="$PWD" python scripts/queue_worker.py \
  --api-base "https://crediclip-axraza-msba.fly.dev" \
  --worker-id "oracle-worker-1" \
  --ingest-mode "rich" \
  --analysis-mode "local"
```

Single-job mode:

```bash
PYTHONPATH="$PWD" python scripts/queue_worker.py \
  --api-base "https://crediclip-axraza-msba.fly.dev" \
  --worker-id "oracle-worker-1" \
  --ingest-mode "rich" \
  --analysis-mode "local" \
  --once
```

## Phase-1 Readiness Benchmark

Run the fixed benchmark pack before moving to Instagram phase:

```bash
python scripts/evaluate_phase1_readiness.py \
  --labels app/data/eval_mixed_labels.json \
  --api-url "https://crediclip-axraza-msba.fly.dev/api/analyze" \
  --n 100 \
  --seed 20260303 \
  --balanced
```

Optional queue reliability gate inputs (if you have queue run totals):

```bash
python scripts/evaluate_phase1_readiness.py \
  --labels app/data/eval_mixed_labels.json \
  --balanced --n 100 \
  --queue-total 100 \
  --queue-completed 97
```

## Optional AI or Not Integration

If you have API access, copy `.env.example` to `.env` and set:
- `AIORNOT_API_KEY`
- `AIORNOT_VIDEO_ENDPOINT`

When not configured, the app uses heuristic-only manipulation scoring.

## Open-Source Verification

Claim verification is fully open-source in this version:
- Retrieve relevant evidence chunks from trusted-source corpus
- Apply lexical/heuristic support-refute checks with citations
- Return not_enough_evidence when support is weak

## Optional OpenRouter LLM (Free Tier)

You can optionally enable OpenRouter for stronger claim reasoning while keeping free/open alternatives:
- Set `OPENROUTER_API_KEY`
- Set `OPENROUTER_MODEL` (example free model: `meta-llama/llama-3.1-8b-instruct:free`)

When OpenRouter is enabled, claim verification uses OpenRouter first, then falls back to open-source heuristics if OpenRouter fails.

## Optional OpenAI LLM

You can also switch claim verification to OpenAI when you want stronger hosted reasoning without using VM RAM:
- Set `OPENAI_API_KEY`
- Set `CLAIM_LLM_MODE=openai`
- Optionally set `OPENAI_MODEL` (recommended default: `gpt-5-mini`)
- Optional overrides:
  - `OPENAI_BASE_URL`
  - `OPENAI_TIMEOUT_SEC`
  - `OPENAI_MAX_RETRIES`
  - `OPENAI_CLAIM_ALLOWED_PLATFORMS`
  - `OPENAI_CLAIM_MIN_TOKENS`
  - `OPENAI_CLAIM_MIN_EVIDENCE_LEVEL`

Current behavior:
- OpenAI is only used for claim assessment JSON, not for the final credibility score
- low-evidence cases are still gated and fall back to heuristics
- deterministic scoring remains in app code
- recommended production default is a platform-aware gate:
  - `instagram`: `60` tokens + `medium`
  - `tiktok`: `50` tokens + `medium`
  - `youtube_shorts`: `20` tokens + `low`
- recommended production default is `TRANSCRIPTION_PROVIDER=auto`

## Auto-Ingestion (YouTube Shorts)

For YouTube Shorts URL-only requests, the app attempts to auto-ingest:
- Title/description metadata using `yt-dlp`
- Transcript using `youtube-transcript-api`
- Subtitle-track parsing fallback from `yt-dlp` caption tracks (`vtt`/`json3`)
- oEmbed metadata fallback
- YouTube watch-page metadata scraping fallback
- Local ASR fallback with `faster-whisper` on downloaded audio
- Frame OCR fallback (`ffmpeg` sampling + `pytesseract`) to capture on-screen text
- Thumbnail metadata fallback from deterministic YouTube image URLs

For VM workers in rich mode (`--ingest-mode rich`), ingestion additionally does:
- Download source video locally on the VM
- Run local Whisper ASR directly from downloaded media
- Run frame-by-frame OCR and visual stability checks
- Add source-alignment note against trusted retrieval corpus

For full local-LLM claim analysis on VM:
- Set worker `--analysis-mode local`
- Run Ollama locally and set `OLLAMA_MODEL` in VM environment

Current limitation:
- Some videos may still block downloads/transcripts from server-side IPs.

## Instagram Phase 2 (current scope)

Instagram link-only analysis is now worker-backed in production when:
- `INSTAGRAM_ANALYZE_VIA_QUEUE=1` is set on Fly

Current Instagram ingest path:
- public page fetch and parser fallback
- browser-rendered worker fallback with Playwright
- rendered-text extraction from page body/article regions
- screenshot OCR fallback on rendered browser frames
- optional worker media-download hook for ASR/frame scan when media URL is accessible
- same evidence contract (`caption`, `transcript`, `notes`, `evidence`)

Current Instagram status from the Phase 2 benchmark:
- worker-backed ingest materially improved evidence coverage
- public HTML alone is usually a generic app-shell/error route
- the remaining blocker for richer media/audio extraction is authenticated Instagram worker cookies

Current Instagram limitation:
- browser-rendered text extraction works
- media/audio download is not reliable without authenticated Instagram cookies on the worker
- local Whisper ASR is installed on Oracle, but `asr_tokens` will stay `0` until worker media access succeeds

Required Fly secrets for queue-backed Instagram:

```bash
flyctl secrets set -a crediclip-axraza-msba \
  INSTAGRAM_ANALYZE_VIA_QUEUE=1 \
  INSTAGRAM_ANALYZE_QUEUE_WAIT_SEC=300 \
  INSTAGRAM_ANALYZE_QUEUE_POLL_SEC=1.5
```

Recommended Oracle worker env for Instagram:

```bash
INGEST_MODE=rich
ANALYSIS_MODE=server
INSTAGRAM_BROWSER_TIMEOUT_SEC=35
INSTAGRAM_BROWSER_HEADLESS=1
INSTAGRAM_BROWSER_OCR_SAMPLES=3
INSTAGRAM_COOKIE_FILE=/home/ubuntu/instagram_cookies.txt
```

If you have a Netscape-format Instagram cookie export, you can also use:
- `INSTAGRAM_COOKIES_B64`

Without `INSTAGRAM_COOKIE_FILE` or `INSTAGRAM_COOKIES_B64`, the worker will still do browser-rendered text extraction, but richer media/audio access may remain blocked.

Auth status can now be checked or updated on the worker with:

```bash
sudo python3 oracle/manage_worker_platform_auth.py --platform instagram --show-status
sudo python3 oracle/manage_worker_platform_auth.py --platform youtube --show-status
```

To activate a new cookie export already present on the worker:

```bash
export INSTAGRAM_COOKIE_FILE=/home/ubuntu/instagram_cookies.txt
sudo python3 oracle/manage_worker_platform_auth.py --platform instagram --set-from-env --restart
```

To upload a local Netscape cookie export to Oracle and activate it in one step:

```bash
python3 scripts/install_worker_platform_cookie.py \
  --platform instagram \
  --cookie-file /path/to/instagram_cookies.txt
```

Recommended Oracle worker env for TikTok:

```bash
INGEST_MODE=rich
ANALYSIS_MODE=server
TIKTOK_COOKIE_FILE=/home/ubuntu/tiktok_cookies.txt
TIKTOK_BROWSER_TIMEOUT_SEC=35
TIKTOK_BROWSER_HEADLESS=1
TIKTOK_BROWSER_OCR_SAMPLES=3
```

TikTok Phase 3 baseline currently uses:
- public metadata fetch
- browser-rendered page recovery
- screenshot OCR
- optional worker media download + local Whisper ASR when a direct media URL is available

### Browser-Rendered YouTube Fallback (No Google API)

CrediClip supports an optional Playwright fallback on the Oracle worker:
- Opens YouTube watch page in a real browser context
- Re-reads metadata/caption tracks from rendered player state
- Uses worker cookies/session if configured

Enable on worker env (`/etc/default/crediclip-worker`):

```bash
BROWSER_INGESTION_ENABLED=1
BROWSER_INGESTION_HEADLESS=1
BROWSER_INGESTION_TIMEOUT_SEC=30
```

Install once on Oracle worker:

```bash
cd ~/CrediClip
source .venv/bin/activate
bash scripts/install_playwright_worker.sh
sudo systemctl restart crediclip-worker
```

### Official YouTube Data API Metadata Fallback

CrediClip can use official YouTube Data API v3 (videos endpoint) as a metadata fallback when yt-dlp is blocked.

Required env:
- `YOUTUBE_DATA_API_KEY`

Optional env:
- `YOUTUBE_DATA_API_PARTS` (default: `snippet`)
- `YOUTUBE_DATA_API_TIMEOUT_SEC` (default: `15`)

Fly secrets example:

```bash
flyctl secrets set YOUTUBE_DATA_API_KEY='<YOUR_KEY>' YOUTUBE_DATA_API_PARTS='snippet' YOUTUBE_DATA_API_TIMEOUT_SEC='15' -a crediclip-axraza-msba
```

Oracle worker env example (`/etc/default/crediclip-worker`):

```bash
YOUTUBE_DATA_API_KEY=<YOUR_KEY>
YOUTUBE_DATA_API_PARTS=snippet
YOUTUBE_DATA_API_TIMEOUT_SEC=15
```

### yt-dlp Cookie Authentication (Recommended for YouTube blocks)

If YouTube blocks server-side metadata/audio extraction, configure yt-dlp cookies:

- `YTDLP_COOKIE_FILE`: path to a Netscape cookie file inside runtime
- `YTDLP_COOKIES_B64`: base64 of Netscape cookie file content (best for Fly secrets)

Example (local shell):

```bash
base64 -i /path/to/cookies.txt | tr -d '\n'
```

Then set on Fly:

```bash
flyctl secrets set YTDLP_COOKIES_B64='<BASE64_VALUE>' -a crediclip-axraza-msba
```

### Oracle Worker: Safer Temporary Token Flow

Use this when YouTube blocks VM-side extraction and you need a short-lived bypass.

1. Keep tokens out of git/code; export them only in shell session:

```bash
export YTDLP_VISITOR_DATA='<visitorData>'
export YTDLP_PO_TOKEN_WEB='<poToken>'
```

2. Apply to worker env file and restart service:

```bash
cd ~/CrediClip
sudo python3 scripts/manage_worker_youtube_auth.py --set-from-env --restart
```

3. Remove temporary tokens after testing:

```bash
cd ~/CrediClip
sudo python3 scripts/manage_worker_youtube_auth.py --clear --restart
```

Risk reduction notes:
- Use a dedicated YouTube account/profile for worker cookies/tokens.
- Do not store token values in repo, screenshots, logs, or issue trackers.
- Rotate tokens regularly; treat them as temporary session secrets.
- `YTDLP_ENABLE_PO_TOKENS` defaults to off in code. Set `YTDLP_ENABLE_PO_TOKENS=1` only when you intentionally want PO token injection.

## Suggested Next Build Steps
1. Add a true ingestion pipeline for video metadata, transcript, and sampled frames.
2. Expand trusted corpora and add vector-store retrieval (FAISS/pgvector) for higher recall.
3. Add persistent storage (Postgres) for video analysis history.
4. Add authentication and role-based dashboards for moderators/researchers.
5. Evaluate against benchmark datasets (FaceForensics++, DFDC, ASVspoof).

## Train Generation Labels from Kaggle (AI + Human)

To build/refresh `generation_origin` calibration labels from:
- AI dataset: `aibuttonfoundation/youtube-ai-slop-shorts-dataset`
- Human dataset: `prince7489/youtube-shorts-performance-dataset`

```bash
cd "/Users/aliraaza/Documents/New project"
source .venv/bin/activate
pip install -r requirements-train.txt
python scripts/train_generation_labels.py
```

Optional: specify explicit files if a dataset has multiple tables:

```bash
python scripts/train_generation_labels.py \
  --ai-file-path "ai_file.csv" \
  --human-file-path "human_file.csv"
```

AI-only mode:

```bash
python scripts/train_generation_labels.py --skip-human
```

This writes:
- `app/data/generation_labels.json` (`video_id -> ai_generated|human_generated`)

To retrain and automatically re-apply manual overrides:

```bash
python scripts/retrain_generation_labels.py
```

One-command pipeline (build real human labels from channels + retrain + verify):

```bash
python scripts/train_generation_all.py
```

Fast rerun without rebuilding channel labels:

```bash
python scripts/train_generation_all.py --skip-build-human
```

Manual override file:
- `app/data/manual_generation_overrides.json`

## Build Mixed Eval Labels (AI + Human Shorts)

Build a mixed ground-truth file using:
- AI Shorts: `aibuttonfoundation/youtube-ai-slop-shorts-dataset`
- Human Shorts: `prince7489/youtube-shorts-performance-dataset`

```bash
python scripts/build_mixed_eval_labels.py
```

Optional class balancing cap (example: 5000 per class):

```bash
python scripts/build_mixed_eval_labels.py --max-ai 5000 --max-human 5000
```

Output:
- `app/data/eval_mixed_labels.json` (`video_id -> ai_generated|human_generated`)

### Recommended Human Label Source (Real YouTube IDs)

If your Kaggle human dataset contains synthetic IDs (e.g., `vid_1000`), build human labels from real public YouTube channels:

```bash
python scripts/build_human_labels_from_channels.py --max-per-channel 40
```

Inputs:
- `app/data/human_channels.txt` (editable list of channels/handles)

Output:
- `app/data/human_generation_labels.json`

Then build mixed labels using AI Kaggle labels + real human channel labels:

```bash
python scripts/build_mixed_eval_labels.py --human-labels-json app/data/human_generation_labels.json --max-ai 5000 --max-human 5000
```

## Validation Report Workflow

Run a random sample validation against live API and save reproducible reports:

```bash
python scripts/evaluate_random_sample.py --n 10 --seed 20260225
```

Run it against mixed labels:

```bash
python scripts/evaluate_random_sample.py --labels app/data/eval_mixed_labels.json --n 50 --seed 20260226
```

Run balanced sampling for fair AI vs human validation:

```bash
python scripts/evaluate_random_sample.py --labels app/data/eval_mixed_labels.json --n 50 --seed 20260226 --balanced
```

Outputs:
- `reports/validation_10_<timestamp>.json`
- `reports/validation_10_<timestamp>.csv`

## Oracle Worker (Recommended for YouTube Ingestion Blocks)

If Fly-hosted ingestion gets blocked by YouTube, run ingestion on your Oracle VM and send extracted text to the same API.

On Oracle Ubuntu:

```bash
sudo apt update
sudo apt install -y python3-venv ffmpeg tesseract-ocr
```

Project setup:

```bash
cd "/path/to/New project"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run worker against a links file:

```bash
python scripts/oracle_ingest_and_analyze.py \
  --links-file /tmp/links20.txt \
  --api-url "https://crediclip-axraza-msba.fly.dev/api/analyze" \
  --concurrency 1
```

### Local LLM on Oracle VM (Ollama)

Install Ollama and pull a model:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:4b
```

Recommended model choice for this project:
- `qwen3:4b` on small Oracle/Fly-adjacent worker VMs
- `qwen3:8b` only if you move to a materially larger RAM box

Practical note from current Oracle worker testing:
- the current Always Free worker (`~1 GB RAM + 2 GB swap`) is too tight for reliable `3b/4b` production inference
- use `qwen2.5:0.5b` only for lightweight experimentation on this box
- for real local-LLM claim scoring, move the worker to a larger VM first

On small Always Free VMs, add swap to avoid Ollama memory failures:

```bash
sudo fallocate -l 2G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Run worker with local analysis (manual mode):

```bash
export CLAIM_LLM_MODE=ollama
export OLLAMA_BASE_URL="http://127.0.0.1:11434"
export OLLAMA_MODEL="qwen3:4b"
export OLLAMA_TIMEOUT_SEC=180
export OLLAMA_NUM_CTX=8192
export OLLAMA_NUM_PREDICT=220

PYTHONPATH="$PWD" python scripts/queue_worker.py \
  --api-base "https://crediclip-axraza-msba.fly.dev" \
  --worker-id "oracle-worker-1" \
  --ingest-mode "rich" \
  --analysis-mode "local"
```

Run worker as a persistent service (recommended):

```bash
chmod +x oracle/install_worker_service.sh
./oracle/install_worker_service.sh
sudo nano /etc/default/crediclip-worker
sudo systemctl restart crediclip-worker
sudo systemctl status crediclip-worker --no-pager
sudo journalctl -u crediclip-worker -f
```

Provision durable Fly queue storage before long benchmark runs:

```bash
flyctl volumes create crediclip_data --region iad --size 1 -a crediclip-axraza-msba
flyctl deploy -a crediclip-axraza-msba --config fly.toml
```

Verify the live app is using the mounted SQLite path:

```bash
flyctl ssh console -a crediclip-axraza-msba -C "sh -lc 'echo JOBS_DB_PATH=$JOBS_DB_PATH; ls -l /data /data/jobs.db 2>/dev/null || true'"
```

Run worker with OpenAI claim assessment:

```bash
export CLAIM_LLM_MODE=openai
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="gpt-5-mini"
export OPENAI_TIMEOUT_SEC=45
export OPENAI_MAX_RETRIES=2
export OPENAI_CLAIM_ALLOWED_PLATFORMS="instagram,tiktok,youtube_shorts"
export OPENAI_CLAIM_MIN_TOKENS=50
export OPENAI_CLAIM_MIN_EVIDENCE_LEVEL=medium
export OPENAI_CLAIM_MIN_TOKENS_INSTAGRAM=60
export OPENAI_CLAIM_MIN_EVIDENCE_LEVEL_INSTAGRAM=medium
export OPENAI_CLAIM_MIN_TOKENS_TIKTOK=50
export OPENAI_CLAIM_MIN_EVIDENCE_LEVEL_TIKTOK=medium
export OPENAI_CLAIM_MIN_TOKENS_YOUTUBE_SHORTS=20
export OPENAI_CLAIM_MIN_EVIDENCE_LEVEL_YOUTUBE_SHORTS=low
export TRANSCRIPTION_PROVIDER=auto
export OPENAI_TRANSCRIPTION_MODEL="gpt-4o-mini-transcribe"

PYTHONPATH="$PWD" python scripts/queue_worker.py \
  --api-base "https://crediclip-axraza-msba.fly.dev" \
  --worker-id "oracle-worker-1" \
  --ingest-mode "rich" \
  --analysis-mode "server"
```

Outputs:
- `reports/oracle_worker_<n>_<timestamp>.json`
- `reports/oracle_worker_<n>_<timestamp>.csv`

Recommended rollout:
- step 1: `CLAIM_LLM_MODE=openai` with `OPENAI_MODEL=gpt-5-mini`
- step 2: use platform-specific gates so OpenAI is available wherever evidence is strong enough
- step 3: prefer `TRANSCRIPTION_PROVIDER=auto` so OpenAI transcription is used when available without disabling local fallback
- step 4: compare before/after reports objectively with:

```bash
python scripts/compare_batch_reports.py \
  --before reports/before.csv \
  --after reports/after.csv
```

Optional for blocked extraction on Oracle too:
- set `YTDLP_COOKIES_B64` with a fresh Netscape cookies export.

## Metrics + Threshold Recommendation

Compute generation-origin quality metrics and a recommended score threshold from validation CSVs:

```bash
python scripts/evaluate_thresholds.py
```

Use mixed-label truth explicitly:

```bash
python scripts/evaluate_thresholds.py --labels app/data/eval_mixed_labels.json
```

Optional explicit CSV input:

```bash
python scripts/evaluate_thresholds.py --report reports/validation_5_20260226T091350Z.csv
```

Outputs:
- `reports/calibration_<timestamp>.json`

Note:
- Reliable thresholding needs mixed ground truth (both `ai_generated` and human-generated samples).
