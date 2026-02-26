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

## Validation Report Workflow

Run a random sample validation against live API and save reproducible reports:

```bash
python scripts/evaluate_random_sample.py --n 10 --seed 20260225
```

Outputs:
- `reports/validation_10_<timestamp>.json`
- `reports/validation_10_<timestamp>.csv`
