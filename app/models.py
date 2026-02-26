from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class AnalyzeRequest(BaseModel):
    url: HttpUrl = Field(description="Public TikTok, Instagram, or YouTube Shorts URL")
    caption: str = Field(default="", description="Optional caption text")
    transcript: str = Field(default="", description="Optional ASR transcript")


class RiskFlag(BaseModel):
    type: Literal["misinformation", "scam", "manipulation", "uncertainty", "generation_origin"]
    level: Literal["low", "medium", "high"]
    score: float
    rationale: str


class ClaimAssessment(BaseModel):
    claim: str
    status: Literal["supported", "refuted", "not_enough_evidence"]
    confidence: float
    rationale: str
    citations: list[str] = Field(default_factory=list)


class AnalyzeResponse(BaseModel):
    platform: Literal["tiktok", "instagram", "youtube_shorts"]
    credibility_score: float
    flags: list[RiskFlag]
    claim_assessments: list[ClaimAssessment]
    component_scores: dict[str, float]
    notes: list[str] = Field(default_factory=list)
