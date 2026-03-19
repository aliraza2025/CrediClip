from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class AnalyzeRequest(BaseModel):
    url: HttpUrl = Field(description="Public TikTok, Instagram, or YouTube Shorts URL")
    caption: str = Field(default="", description="Optional caption text")
    transcript: str = Field(default="", description="Optional ASR transcript")
    ingest_evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional worker-supplied evidence accounting metadata",
    )


class RiskFlag(BaseModel):
    type: Literal[
        "misinformation",
        "scam",
        "manipulation",
        "uncertainty",
        "generation_origin",
        "evidence_quality",
    ]
    level: Literal["low", "medium", "high"]
    score: float
    rationale: str


class ClaimAssessment(BaseModel):
    claim: str
    status: Literal["supported", "refuted", "not_enough_evidence"]
    confidence: float
    rationale: str
    citations: list[str] = Field(default_factory=list)


class EvidenceCoverage(BaseModel):
    total_tokens: int = 0
    caption_tokens: int = 0
    transcript_tokens: int = 0
    ocr_tokens: int = 0
    asr_tokens: int = 0
    level: Literal["low", "medium", "high"] = "low"
    transcript_present: bool = False
    ocr_present: bool = False
    asr_present: bool = False


class AnalyzeResponse(BaseModel):
    platform: Literal["tiktok", "instagram", "youtube_shorts"]
    credibility_score: float
    flags: list[RiskFlag]
    claim_assessments: list[ClaimAssessment]
    component_scores: dict[str, float]
    evidence_coverage: EvidenceCoverage = Field(default_factory=EvidenceCoverage)
    notes: list[str] = Field(default_factory=list)


JobStatus = Literal["queued", "processing", "completed", "failed"]


class JobCreateRequest(BaseModel):
    url: HttpUrl


class JobClaimRequest(BaseModel):
    worker_id: str = Field(default="worker-default", min_length=1)
    include_platforms: list[str] = Field(default_factory=list)
    exclude_platforms: list[str] = Field(default_factory=list)


class JobArtifactsRequest(BaseModel):
    caption: str = Field(default="")
    transcript: str = Field(default="")
    ingest_notes: list[str] = Field(default_factory=list)
    debug_notes: list[str] = Field(default_factory=list)
    ingest_evidence: dict[str, Any] = Field(default_factory=dict)


class JobCompleteRequest(BaseModel):
    caption: str = Field(default="")
    transcript: str = Field(default="")
    ingest_notes: list[str] = Field(default_factory=list)
    debug_notes: list[str] = Field(default_factory=list)
    result: dict[str, Any]


class JobFailRequest(BaseModel):
    error: str = Field(..., min_length=1)


class JobResponse(BaseModel):
    id: str
    url: str
    status: JobStatus
    created_at: str
    updated_at: str
    reused: bool = False
    worker_id: str | None = None
    caption_chars: int = 0
    transcript_chars: int = 0
    ingest_notes: list[str] = Field(default_factory=list)
    debug_notes: list[str] = Field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] | None = None


class JobClaimResponse(BaseModel):
    job: JobResponse | None = None


class JobsListResponse(BaseModel):
    jobs: list[JobResponse]


class QueueStatsResponse(BaseModel):
    counts: dict[str, int]
    total: int
    oldest_queued_job_id: str | None = None
    oldest_queued_created_at: str | None = None
