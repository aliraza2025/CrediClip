---
layout: article
title: Building a Multi-Lane Data Plane for Short-Form Video Credibility Analysis
description: How I separated CrediClip's web control plane from platform-specific evidence workers to analyze YouTube Shorts, Instagram Reels, and TikTok videos reliably.
author: Ali Raza
date: 2026-10-14
read_time: 10 min read
permalink: /data-plane/
tags:
  - Python
  - FastAPI
  - System Architecture
  - AI
---

Explore my complete [product, AI, analytics, and strategy portfolio](https://alirazaportfolio2026.netlify.app/).

Short-form video looks simple from the outside: paste a link, wait briefly, and receive a credibility score. The engineering behind that interaction is less simple. YouTube Shorts, Instagram Reels, and TikTok expose different metadata, apply different anti-bot controls, and fail in different ways. A single request handler that tries to download media, transcribe audio, scan frames, retrieve evidence, and score a claim quickly becomes slow and fragile.

I encountered this problem while building **CrediClip**, my capstone project for credibility analysis of short-form video. The most important architectural decision was to separate the system into two parts:

- a **control plane** that accepts links, stores job state, exposes status APIs, and returns results;
- a **data plane** made of background workers that perform platform-specific evidence extraction and analysis.

Within the data plane, I introduced **worker lanes**. Each lane can accept or reject jobs by platform, allowing expensive or unreliable workloads to be isolated without duplicating the entire application.

This article explains why the architecture was necessary, how it works, and what I learned from implementing it.

> The code excerpts below are shortened from the working project for readability. The complete implementation is available in the [CrediClip repository](https://github.com/aliraza2025/CrediClip).

## The problem with a single synchronous pipeline

The first version of the application followed a straightforward flow:

```text
browser request
    -> fetch platform page
    -> extract caption and transcript
    -> run OCR or speech transcription
    -> analyze credibility
    -> return response
```

That design works for a controlled demo, but it creates several production problems.

First, evidence extraction is much slower than ordinary web processing. A video may require browser rendering, media download, frame sampling, OCR, or automatic speech recognition. Holding an HTTP request open during all of those stages makes timeouts much more likely.

Second, the platforms do not behave alike. Instagram may expose useful rendered metadata while withholding the media file. TikTok may require a browser session or cookies. YouTube may return captions successfully for one video and produce an anti-bot challenge for the next. Treating those workloads as identical means one platform's failure mode can consume all available capacity.

Third, the workload is resource-heavy. Browser processes, media decoding, OCR, and transcription compete for memory and CPU. Running them inside the public web process risks slowing every visitor or terminating the process entirely.

The solution was not simply “add more servers.” The system needed a clear boundary between coordinating work and performing work.

## Control plane versus data plane

The **control plane** is intentionally lightweight. It performs four main tasks:

1. normalize and validate submitted URLs;
2. create or reuse jobs in a persistent queue;
3. allow workers to claim and update jobs;
4. let the client poll one job until it completes or fails.

The **data plane** performs the expensive operations:

1. identify the source platform;
2. retrieve captions, transcripts, metadata, and media where available;
3. run browser-assisted extraction, OCR, frame scanning, or speech transcription;
4. calculate evidence coverage and credibility signals;
5. send artifacts or a completed result back to the control plane.

The resulting flow is:

```text
User
  |
  v
FastAPI control plane
  |
  +--> SQLite job queue
           |
           +--> Instagram/TikTok lane A
           +--> Instagram/TikTok lane B
           +--> YouTube Shorts lane
                         |
                         v
              captions + ASR + OCR + metadata
                         |
                         v
                 credibility result
                         |
                         v
                  SQLite job record
```

This separation provides two useful guarantees. The public API remains responsive even when extraction is slow, and a failing platform can be isolated to its own worker lane.

## Step 1: represent analysis as a durable job

When the frontend submits a video URL, the API does not immediately perform rich extraction. It creates or reuses a queue job:

```python
@app.post("/api/jobs", response_model=JobResponse)
def create_analysis_job(request: JobCreateRequest) -> JobResponse:
    normalized_url, _ = normalize_input_url(str(request.url))
    job = create_or_reuse_job(normalized_url)
    return JobResponse(**job)
```

The job record stores a small state machine:

```text
queued -> processing -> completed
                    \-> failed
```

For the MVP, I used SQLite rather than introducing a separate queue product. That was a deliberate tradeoff. The project needed durable state, atomic claiming, simple backup, and low operating cost more than it needed distributed throughput.

The table stores the URL, status, worker identity, extracted caption and transcript, diagnostic notes, result JSON, error information, and timestamps. This makes the queue useful not only for scheduling but also for observability.

The API also reuses an active or recently completed job for the same URL. That prevents repeated clicks from creating duplicate browser and transcription work. A result-version marker ensures that stale results are not reused after scoring logic changes.

## Step 2: claim jobs atomically

Multiple workers poll the same API. If two workers read the same queued record at the same time, they could both analyze it. To prevent that, the claim operation starts an immediate SQLite transaction, selects an eligible job, and changes its status before releasing the lock.

```python
def claim_next_job(worker_id, include_platforms=None, exclude_platforms=None):
    recover_stale_jobs()
    include = set(include_platforms or [])
    exclude = set(exclude_platforms or [])

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 200
            """
        ).fetchall()

        selected = None
        for candidate in rows:
            platform = _job_platform(candidate["url"])
            if include and platform not in include:
                continue
            if exclude and platform in exclude:
                continue
            selected = candidate
            break

        if selected is None:
            return None

        conn.execute(
            """
            UPDATE jobs
            SET status = 'processing', worker_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (worker_id, _now_iso(), selected["id"]),
        )
        conn.commit()

    return get_job(selected["id"])
```

`BEGIN IMMEDIATE` is important here. It serializes competing writers during the short claim operation. This is enough for the scale of the MVP and avoids adding Redis or a message broker solely for locking.

## Step 3: create lanes with routing filters

A lane is not a separate codebase. It is the same worker started with a different routing policy.

```python
parser.add_argument("--include-platforms", default="")
parser.add_argument("--exclude-platforms", default="")

payload = {"worker_id": worker_id}
if include_platforms:
    payload["include_platforms"] = include_platforms
if exclude_platforms:
    payload["exclude_platforms"] = exclude_platforms

response = client.post(f"{api_base}/api/jobs/claim", json=payload)
job = response.json().get("job")
```

The deployed topology used two general lanes for Instagram and TikTok and one dedicated YouTube Shorts lane:

```bash
# General evidence lane
python scripts/queue_worker.py \
  --worker-id oracle-worker-1 \
  --exclude-platforms youtube_shorts \
  --ingest-mode rich

# Dedicated YouTube lane
python scripts/queue_worker.py \
  --worker-id oracle-worker-youtube \
  --include-platforms youtube_shorts \
  --ingest-mode rich
```

YouTube was separated because its anti-bot and authentication behavior differed from Instagram and TikTok. If YouTube extraction stalled, it could no longer occupy every worker. The general lanes remained available for the other platforms.

The same mechanism can support future policies without rewriting the queue: a high-priority lane, a GPU-backed transcription lane, or a lane restricted to jobs that already contain transcripts.

## Step 4: use one interface with platform-specific adapters

Once a worker claims a job, it selects the correct ingestion adapter. Each adapter returns the same logical output: caption, transcript, notes, and structured evidence coverage.

```python
async def ingest(url: str, worker_mode: bool):
    normalized_url, _ = normalize_input_url(url)
    platform = infer_platform(normalized_url)

    if platform == "youtube_shorts":
        return await enrich_from_youtube(
            normalized_url,
            worker_mode=worker_mode,
            include_evidence=True,
        )
    if platform == "instagram":
        return await enrich_from_instagram(
            normalized_url,
            worker_mode=worker_mode,
            include_evidence=True,
        )
    if platform == "tiktok":
        return await enrich_from_tiktok(
            normalized_url,
            worker_mode=worker_mode,
            include_evidence=True,
        )

    return "", "", ["Unsupported platform"], {}
```

This adapter boundary matters because the downstream scoring pipeline should not need to know how evidence was collected. It consumes normalized text and evidence metadata regardless of whether the source was a page description, subtitle track, OCR frame, or ASR transcript.

The evidence object also records coverage signals such as caption tokens, transcript tokens, OCR tokens, ASR tokens, and an overall evidence level. That information affects uncertainty and prevents a sparse extraction from being presented with the same confidence as a high-coverage analysis.

## Step 5: decide where analysis runs

The worker supports two modes:

- **local analysis**: extract evidence and run the complete scoring pipeline on the worker;
- **server analysis**: extract evidence on the worker, then send artifacts to the API for scoring.

```python
if analysis_mode == "local":
    result = await analyze_video(
        AnalyzeRequest(
            url=url,
            caption=caption,
            transcript=transcript,
            ingest_evidence=ingest_evidence,
        )
    )
    post_complete_result(job_id, caption, transcript, result)
else:
    post_artifacts(job_id, caption, transcript, ingest_evidence)
```

This switch made deployment more flexible. A small web host could remain lightweight while a VM performed rich ingestion and scoring. Alternatively, the worker could upload artifacts to a stronger analysis server. The queue contract stayed the same in both cases.

## Step 6: design for failure, not only success

Background processing creates a specific reliability problem: a worker can claim a job and then disappear. Without recovery, that job remains in `processing` forever.

CrediClip periodically checks processing jobs and requeues any that have exceeded a configurable stale threshold:

```python
def recover_stale_jobs():
    now = datetime.now(timezone.utc)
    recovered = []

    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, updated_at FROM jobs WHERE status = 'processing'"
        ).fetchall()

        for row in rows:
            age = now - datetime.fromisoformat(row["updated_at"])
            if age.total_seconds() >= PROCESSING_STALE_SEC:
                recovered.append(row["id"])

        for job_id in recovered:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'queued', worker_id = NULL, updated_at = ?
                WHERE id = ?
                """,
                (_now_iso(), job_id),
            )

        conn.commit()
```

Timeouts are handled at the ingestion and analysis boundaries, and failures are written back to the job record. The frontend can therefore distinguish “still working” from “failed,” and operators can inspect the reason instead of searching through only terminal logs.

## What the lane model improved

The lane architecture improved the project in four practical ways.

**Isolation.** YouTube-specific failures no longer consumed all worker capacity.

**Responsiveness.** The web layer handled short API operations while media processing happened elsewhere.

**Observability.** Every job recorded its state, assigned worker, evidence volume, notes, result, and error.

**Cost control.** The MVP could use SQLite and small worker VMs instead of a managed queue, distributed database, or large always-on server.

It also made the system easier to explain. The score was no longer the output of a single opaque model call. It was the final stage of an observable pipeline that collected evidence, measured coverage, assessed signals, and recorded uncertainty.

## Scoring, confidence, and evaluation

The final score is computed in deterministic application code. LLMs can provide structured claim assessments when evidence thresholds are met. Low analysis confidence shrinks scores toward a neutral anchor of 52, while evidence coverage describes the recovered inputs. That behavior is a design choice, not an independently validated probability.

In the March 11, 2026 YouTube batch, all 780 jobs eventually completed, including two retried links, but 759 results (97.3%) were low evidence. In a separate 19-link Instagram comparison between March 11 and March 13, mean evidence tokens rose from 1.0 to 130.9. These measurements describe completion and evidence recovery. They do not establish credibility accuracy.

The [evaluation article](https://aliraza2025.github.io/CrediClip/evaluating-ai-product-quality/) links the underlying reports and distinguishes these measurements from independently labeled assessment quality. Stronger labeled evaluation and confidence calibration remain necessary.

## Tradeoffs and limits

The design is intentionally pragmatic, but it has limits.

SQLite is suitable for a small deployment with a limited number of concurrent writers. At higher scale, the job store should move to PostgreSQL and a dedicated queue such as Redis, RabbitMQ, or a managed equivalent.

Polling is simple but introduces repeated requests and small delays. Server-Sent Events or WebSockets could improve result delivery for a larger interactive product.

Platform adapters remain dependent on external websites whose markup, authentication, and anti-automation controls can change. The lane model contains those failures, but it cannot eliminate them.

Finally, routing by platform is only a first-level policy. A mature system could route by evidence requirements, expected duration, account/session availability, or resource class.

## Lessons from building the data plane

The main lesson was that credibility analysis is not only a modeling problem. Before scoring can be trusted, the system must reliably collect and describe the evidence that reached the model.

Separating the control plane from the data plane created a stable boundary between user-facing coordination and unpredictable media work. Adding lanes then turned platform differences into explicit routing decisions rather than hidden exceptions inside one large process.

For an MVP, the best architecture is not necessarily the one with the most infrastructure. It is the smallest architecture that makes failure visible, protects the user-facing path, and leaves clear upgrade points. In CrediClip, a FastAPI control plane, a durable SQLite queue, and lane-aware Python workers were enough to achieve that goal.

---

**Project:** [CrediClip on GitHub](https://github.com/aliraza2025/CrediClip)  
**Author:** Ali Raza  
**Topic:** Data-plane architecture for short-form video credibility analysis

Explore my complete [product, AI, analytics, and strategy portfolio](https://alirazaportfolio2026.netlify.app/).

