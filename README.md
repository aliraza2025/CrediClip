# CrediClip MVP

CrediClip is an AI credibility scoring prototype for TikTok, Instagram, and YouTube Shorts videos.
This MVP accepts a public URL and returns:
- Credibility score (0-100)
- Risk flags (misinformation, scam, manipulation, uncertainty)
- Claim-level assessments

Link-only behavior:
- YouTube Shorts: supported (auto metadata + transcript ingestion)
- TikTok/Instagram: currently requires caption or transcript input

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

Current limitation:
- Some videos may still block downloads/transcripts from server-side IPs.

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

## Suggested Next Build Steps
1. Add a true ingestion pipeline for video metadata, transcript, and sampled frames.
2. Expand trusted corpora and add vector-store retrieval (FAISS/pgvector) for higher recall.
3. Add persistent storage (Postgres) for video analysis history.
4. Add authentication and role-based dashboards for moderators/researchers.
5. Evaluate against benchmark datasets (FaceForensics++, DFDC, ASVspoof).

## Train Generation Labels from Kaggle (AI Shorts Only)

To build/refresh `generation_origin` calibration labels from:
`aibuttonfoundation/youtube-ai-slop-shorts-dataset`

```bash
cd "/Users/aliraaza/Documents/New project"
source .venv/bin/activate
pip install -r requirements-train.txt
python scripts/train_generation_labels.py
```

Optional: specify a dataset file path if Kaggle dataset has multiple files:

```bash
python scripts/train_generation_labels.py --file-path "your_file.csv"
```

This writes:
- `app/data/generation_labels.json` (video_id -> `ai_generated`)

To retrain and automatically re-apply manual overrides:

```bash
python scripts/retrain_generation_labels.py
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

Outputs:
- `reports/oracle_worker_<n>_<timestamp>.json`
- `reports/oracle_worker_<n>_<timestamp>.csv`

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
