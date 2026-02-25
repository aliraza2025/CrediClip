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
- Modular scoring pipeline with RAG claim verification and optional external deepfake API integration

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

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

## Optional OpenAI Claim Verification (RAG)

The app now uses a retrieval + LLM flow for claim verification:
- Retrieve relevant evidence chunks from trusted-source corpus
- Ask LLM to classify claim as supported/refuted/not_enough_evidence with citations
- Fall back to evidence-grounded heuristics if OpenAI credentials are missing/unavailable

Set these in `.env` to enable LLM verification:
- `OPENAI_API_KEY`
- `OPENAI_MODEL` (default: `gpt-4o-mini`)

## Auto-Ingestion (YouTube Shorts)

For YouTube Shorts URL-only requests, the app attempts to auto-ingest:
- Title/description metadata using `yt-dlp`
- Transcript using `youtube-transcript-api`

Current limitation:
- Full frame-by-frame visual analysis is not enabled in this v1 (noted in response notes).

## Suggested Next Build Steps
1. Add a true ingestion pipeline for video metadata, transcript, and sampled frames.
2. Expand trusted corpora and add vector-store retrieval (FAISS/pgvector) for higher recall.
3. Add persistent storage (Postgres) for video analysis history.
4. Add authentication and role-based dashboards for moderators/researchers.
5. Evaluate against benchmark datasets (FaceForensics++, DFDC, ASVspoof).
