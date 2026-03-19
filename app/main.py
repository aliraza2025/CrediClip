import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from app.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    JobArtifactsRequest,
    JobClaimRequest,
    JobClaimResponse,
    JobCompleteRequest,
    JobCreateRequest,
    JobFailRequest,
    JobResponse,
    QueueStatsResponse,
    JobsListResponse,
)
from app.services.pipeline import analyze_video, infer_platform, normalize_input_url
from app.services.jobs import (
    claim_next_job,
    complete_job,
    create_or_reuse_job,
    fail_job,
    get_job,
    init_job_store,
    list_jobs,
    queue_stats,
)

load_dotenv()

app = FastAPI(title="CrediClip MVP", version="0.1.0")

_ANALYZE_MAX_CONCURRENCY = max(1, int(os.getenv("ANALYZE_MAX_CONCURRENCY", "1")))
_ANALYZE_TIMEOUT_SEC = max(15, int(os.getenv("ANALYZE_TIMEOUT_SEC", "180")))
_YOUTUBE_ANALYZE_VIA_QUEUE = (os.getenv("YOUTUBE_ANALYZE_VIA_QUEUE") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_INSTAGRAM_ANALYZE_VIA_QUEUE = (os.getenv("INSTAGRAM_ANALYZE_VIA_QUEUE") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_TIKTOK_ANALYZE_VIA_QUEUE = (os.getenv("TIKTOK_ANALYZE_VIA_QUEUE") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_YOUTUBE_ANALYZE_QUEUE_WAIT_SEC = max(30, int(os.getenv("YOUTUBE_ANALYZE_QUEUE_WAIT_SEC", "300")))
_YOUTUBE_ANALYZE_QUEUE_POLL_SEC = max(0.25, float(os.getenv("YOUTUBE_ANALYZE_QUEUE_POLL_SEC", "1.5")))
_INSTAGRAM_ANALYZE_QUEUE_WAIT_SEC = max(30, int(os.getenv("INSTAGRAM_ANALYZE_QUEUE_WAIT_SEC", "300")))
_INSTAGRAM_ANALYZE_QUEUE_POLL_SEC = max(0.25, float(os.getenv("INSTAGRAM_ANALYZE_QUEUE_POLL_SEC", "1.5")))
_TIKTOK_ANALYZE_QUEUE_WAIT_SEC = max(30, int(os.getenv("TIKTOK_ANALYZE_QUEUE_WAIT_SEC", "300")))
_TIKTOK_ANALYZE_QUEUE_POLL_SEC = max(0.25, float(os.getenv("TIKTOK_ANALYZE_QUEUE_POLL_SEC", "1.5")))
_analyze_semaphore = asyncio.Semaphore(_ANALYZE_MAX_CONCURRENCY)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.on_event("startup")
def startup() -> None:
    init_job_store()


async def _run_analyze_with_limits(request: AnalyzeRequest) -> AnalyzeResponse:
    async with _analyze_semaphore:
        return await asyncio.wait_for(analyze_video(request), timeout=_ANALYZE_TIMEOUT_SEC)


async def _wait_for_completed_job(job_id: str, timeout_sec: float, poll_sec: float) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while True:
        job = get_job(job_id)
        if not job:
            raise HTTPException(status_code=500, detail=f"Queued analysis job disappeared: {job_id}")
        if job["status"] == "completed":
            return job
        if job["status"] == "failed":
            error = job.get("error") or "unknown_error"
            raise HTTPException(status_code=502, detail=f"Queued analysis failed ({job_id}): {error}")
        if asyncio.get_running_loop().time() >= deadline:
            raise HTTPException(
                status_code=504,
                detail=f"Queued analysis timed out waiting for worker ({job_id})",
            )
        await asyncio.sleep(poll_sec)


def _should_use_worker_backed_ingest(request: AnalyzeRequest) -> tuple[bool, str]:
    if request.caption.strip() or request.transcript.strip():
        return False, ""
    normalized_url, _ = normalize_input_url(str(request.url))
    platform = infer_platform(normalized_url)
    if platform == "youtube_shorts" and _YOUTUBE_ANALYZE_VIA_QUEUE:
        return True, platform
    if platform == "instagram" and _INSTAGRAM_ANALYZE_VIA_QUEUE:
        return True, platform
    if platform == "tiktok" and _TIKTOK_ANALYZE_VIA_QUEUE:
        return True, platform
    return False, platform


async def _run_worker_backed_analysis(request: AnalyzeRequest, platform: str) -> AnalyzeResponse:
    normalized_url, _ = normalize_input_url(str(request.url))
    job = create_or_reuse_job(normalized_url)
    if job["status"] == "completed":
        result = job.get("result")
        if isinstance(result, dict):
            try:
                return AnalyzeResponse.model_validate(result)
            except Exception:
                pass
    if platform == "youtube_shorts":
        wait_sec = _YOUTUBE_ANALYZE_QUEUE_WAIT_SEC
        poll_sec = _YOUTUBE_ANALYZE_QUEUE_POLL_SEC
    elif platform == "instagram":
        wait_sec = _INSTAGRAM_ANALYZE_QUEUE_WAIT_SEC
        poll_sec = _INSTAGRAM_ANALYZE_QUEUE_POLL_SEC
    else:
        wait_sec = _TIKTOK_ANALYZE_QUEUE_WAIT_SEC
        poll_sec = _TIKTOK_ANALYZE_QUEUE_POLL_SEC
    completed_job = await _wait_for_completed_job(
        job_id=job["id"],
        timeout_sec=wait_sec,
        poll_sec=poll_sec,
    )
    result = completed_job.get("result")
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail=f"Queued analysis returned no result payload ({job['id']})")
    try:
        return AnalyzeResponse.model_validate(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Queued analysis returned invalid result ({job['id']})") from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        static_dir / "index.html",
        headers={
            "Cache-Control": "no-store, max-age=0, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/dashboard")
def dashboard() -> FileResponse:
    return FileResponse(
        static_dir / "dashboard.html",
        headers={
            "Cache-Control": "no-store, max-age=0, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    try:
        use_queue, platform = _should_use_worker_backed_ingest(request)
        if use_queue:
            return await _run_worker_backed_analysis(request, platform)
        return await _run_analyze_with_limits(request)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=504, detail="Analysis timed out") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs", response_model=JobResponse)
def create_analysis_job(request: JobCreateRequest) -> JobResponse:
    normalized_url, _ = normalize_input_url(str(request.url))
    job = create_or_reuse_job(normalized_url)
    return JobResponse(**job)


@app.get("/api/jobs", response_model=JobsListResponse)
def list_analysis_jobs(status: str | None = None, limit: int = 50) -> JobsListResponse:
    if status and status not in {"queued", "processing", "completed", "failed"}:
        raise HTTPException(status_code=400, detail="Invalid status filter")
    jobs = list_jobs(status=status, limit=limit)
    return JobsListResponse(jobs=[JobResponse(**j) for j in jobs])


@app.get("/api/queue/stats", response_model=QueueStatsResponse)
def get_queue_stats() -> QueueStatsResponse:
    return QueueStatsResponse(**queue_stats())


@app.post("/api/jobs/claim", response_model=JobClaimResponse)
def claim_analysis_job(request: JobClaimRequest) -> JobClaimResponse:
    job = claim_next_job(
        request.worker_id,
        include_platforms=request.include_platforms,
        exclude_platforms=request.exclude_platforms,
    )
    return JobClaimResponse(job=JobResponse(**job) if job else None)


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_analysis_job(job_id: str) -> JobResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse(**job)


@app.post("/api/jobs/{job_id}/artifacts", response_model=JobResponse)
async def submit_job_artifacts(job_id: str, request: JobArtifactsRequest) -> JobResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail=f"Job already {job['status']}")

    try:
        result = await _run_analyze_with_limits(
            AnalyzeRequest(
                url=job["url"],
                caption=request.caption,
                transcript=request.transcript,
                ingest_evidence=request.ingest_evidence,
            )
        )
    except asyncio.TimeoutError as exc:
        updated = fail_job(
            job_id=job_id,
            error="analysis_timeout",
            ingest_notes=request.ingest_notes,
            debug_notes=request.debug_notes,
        )
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to update job status") from exc
        return JobResponse(**updated)
    except ValueError as exc:
        updated = fail_job(
            job_id=job_id,
            error=str(exc),
            ingest_notes=request.ingest_notes,
            debug_notes=request.debug_notes,
        )
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to update job status") from exc
        return JobResponse(**updated)

    updated = complete_job(
        job_id=job_id,
        caption=request.caption,
        transcript=request.transcript,
        ingest_notes=request.ingest_notes,
        debug_notes=request.debug_notes,
        result=result.model_dump(),
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update job status")
    return JobResponse(**updated)


@app.post("/api/jobs/{job_id}/complete", response_model=JobResponse)
def complete_analysis_job(job_id: str, request: JobCompleteRequest) -> JobResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] in {"completed", "failed"}:
        raise HTTPException(status_code=409, detail=f"Job already {job['status']}")

    try:
        validated = AnalyzeResponse.model_validate(request.result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid analysis result payload") from exc

    updated = complete_job(
        job_id=job_id,
        caption=request.caption,
        transcript=request.transcript,
        ingest_notes=request.ingest_notes,
        debug_notes=request.debug_notes,
        result=validated.model_dump(),
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update job status")
    return JobResponse(**updated)


@app.post("/api/jobs/{job_id}/fail", response_model=JobResponse)
def fail_analysis_job(job_id: str, request: JobFailRequest) -> JobResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    updated = fail_job(job_id=job_id, error=request.error)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update job status")
    return JobResponse(**updated)
