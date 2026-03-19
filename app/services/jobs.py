from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _processing_stale_sec() -> int:
    raw = os.getenv("JOBS_PROCESSING_STALE_SEC", "1800")
    try:
        return max(60, int(raw))
    except Exception:
        return 1800


def _recent_completed_sec() -> int:
    raw = os.getenv("JOBS_RECENT_COMPLETED_SEC", "900")
    try:
        return max(0, int(raw))
    except Exception:
        return 900


def _db_path() -> Path:
    raw = os.getenv("JOBS_DB_PATH", "app/data/jobs.db")
    path = Path(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_job_store() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                worker_id TEXT,
                caption TEXT DEFAULT '',
                transcript TEXT DEFAULT '',
                ingest_notes_json TEXT DEFAULT '[]',
                debug_notes_json TEXT DEFAULT '[]',
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    recover_stale_jobs()


def _parse_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    caption = row["caption"] or ""
    transcript = row["transcript"] or ""
    return {
        "id": row["id"],
        "url": row["url"],
        "status": row["status"],
        "worker_id": row["worker_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "caption_chars": len(caption),
        "transcript_chars": len(transcript),
        "ingest_notes": _parse_json(row["ingest_notes_json"], []),
        "debug_notes": _parse_json(row["debug_notes_json"], []),
        "error": row["error"],
        "result": _parse_json(row["result_json"], None),
    }


def _job_platform(url: str) -> str:
    raw = (url or "").lower()
    if "youtube.com/shorts/" in raw or "youtu.be/" in raw:
        return "youtube_shorts"
    if "instagram.com/" in raw:
        return "instagram"
    if "tiktok.com/" in raw:
        return "tiktok"
    return "unknown"


def create_job(url: str) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    ts = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, url, status, created_at, updated_at)
            VALUES (?, ?, 'queued', ?, ?)
            """,
            (job_id, url, ts, ts),
        )
        conn.commit()
    job = get_job(job_id)
    if not job:
        raise RuntimeError("Failed to create job")
    return job


def find_reusable_job(url: str) -> dict[str, Any] | None:
    """Return an existing active job, or a recent completed job for the same URL."""
    recover_stale_jobs()
    with _connect() as conn:
        active_row = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE url = ?
              AND status IN ('processing', 'queued')
            ORDER BY
              CASE status WHEN 'processing' THEN 0 ELSE 1 END,
              created_at ASC
            LIMIT 1
            """,
            (url,),
        ).fetchone()
        if active_row:
            return _row_to_job(active_row)

        recent_sec = _recent_completed_sec()
        if recent_sec <= 0:
            return None
        cutoff = datetime.now(timezone.utc).timestamp() - float(recent_sec)
        completed_rows = conn.execute(
            """
            SELECT *
            FROM jobs
            WHERE url = ?
              AND status = 'completed'
              AND result_json IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 10
            """,
            (url,),
        ).fetchall()
        for row in completed_rows:
            updated_at = _parse_iso(row["updated_at"])
            if updated_at is None:
                continue
            if updated_at.astimezone(timezone.utc).timestamp() < cutoff:
                continue
            return _row_to_job(row)
    return None


def create_or_reuse_job(url: str) -> dict[str, Any]:
    existing = find_reusable_job(url)
    if existing:
        job = dict(existing)
        job["reused"] = True
        return job
    job = create_job(url)
    job["reused"] = False
    return job


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def recover_stale_jobs() -> int:
    now = datetime.now(timezone.utc)
    stale_after_sec = _processing_stale_sec()
    recovered_rows: list[tuple[str, str]] = []

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, updated_at, debug_notes_json
            FROM jobs
            WHERE status = 'processing'
            """
        ).fetchall()
        for row in rows:
            updated_at = _parse_iso(row["updated_at"])
            if updated_at is None:
                continue
            age_sec = (now - updated_at.astimezone(timezone.utc)).total_seconds()
            if age_sec < stale_after_sec:
                continue
            notes = _parse_json(row["debug_notes_json"], [])
            notes.append(
                f"Auto-requeued after {int(age_sec)}s in processing without completion."
            )
            recovered_rows.append((json.dumps(notes), row["id"]))

        if recovered_rows:
            conn.executemany(
                """
                UPDATE jobs
                SET status = 'queued',
                    worker_id = NULL,
                    debug_notes_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                [(notes, _now_iso(), job_id) for notes, job_id in recovered_rows],
            )
            conn.commit()

    return len(recovered_rows)


def list_jobs(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    recover_stale_jobs()
    safe_limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        if status:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, safe_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
    return [_row_to_job(r) for r in rows]


def queue_stats() -> dict[str, Any]:
    recover_stale_jobs()
    counts = {"queued": 0, "processing": 0, "completed": 0, "failed": 0}
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS n
            FROM jobs
            GROUP BY status
            """
        ).fetchall()
        for r in rows:
            status = str(r["status"])
            if status in counts:
                counts[status] = int(r["n"])

        oldest_queued = conn.execute(
            """
            SELECT id, created_at
            FROM jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()

    return {
        "counts": counts,
        "total": sum(counts.values()),
        "oldest_queued_job_id": oldest_queued["id"] if oldest_queued else None,
        "oldest_queued_created_at": oldest_queued["created_at"] if oldest_queued else None,
    }


def claim_next_job(
    worker_id: str,
    include_platforms: list[str] | None = None,
    exclude_platforms: list[str] | None = None,
) -> dict[str, Any] | None:
    recover_stale_jobs()
    now = _now_iso()
    include = {p.strip().lower() for p in (include_platforms or []) if p and p.strip()}
    exclude = {p.strip().lower() for p in (exclude_platforms or []) if p and p.strip()}
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
        row = None
        for candidate in rows:
            platform = _job_platform(str(candidate["url"] or ""))
            if include and platform not in include:
                continue
            if exclude and platform in exclude:
                continue
            row = candidate
            break

        if row is None:
            conn.commit()
            return None

        job_id = row["id"]
        conn.execute(
            """
            UPDATE jobs
            SET status = 'processing', worker_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (worker_id, now, job_id),
        )
        conn.commit()

    return get_job(job_id)


def complete_job(
    job_id: str,
    caption: str,
    transcript: str,
    ingest_notes: list[str],
    debug_notes: list[str],
    result: dict[str, Any],
) -> dict[str, Any] | None:
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'completed',
                caption = ?,
                transcript = ?,
                ingest_notes_json = ?,
                debug_notes_json = ?,
                result_json = ?,
                error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                caption or "",
                transcript or "",
                json.dumps(ingest_notes or []),
                json.dumps(debug_notes or []),
                json.dumps(result),
                now,
                job_id,
            ),
        )
        conn.commit()
    return get_job(job_id)


def fail_job(
    job_id: str,
    error: str,
    ingest_notes: list[str] | None = None,
    debug_notes: list[str] | None = None,
) -> dict[str, Any] | None:
    now = _now_iso()
    with _connect() as conn:
        if ingest_notes is None and debug_notes is None:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed', error = ?, updated_at = ?
                WHERE id = ?
                """,
                (error, now, job_id),
            )
        else:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error = ?,
                    ingest_notes_json = ?,
                    debug_notes_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    error,
                    json.dumps(ingest_notes or []),
                    json.dumps(debug_notes or []),
                    now,
                    job_id,
                ),
            )
        conn.commit()
    return get_job(job_id)
