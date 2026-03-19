from __future__ import annotations

from app.models import ClaimAssessment, RiskFlag


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def level_from_score(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def score_misinformation(claims: list[ClaimAssessment]) -> float:
    if not claims:
        return 18.0

    penalties: list[float] = []
    for claim in claims:
        confidence = clamp(float(claim.confidence) * 100.0)
        if claim.status == "refuted":
            penalties.append(60.0 + (0.35 * confidence))
        elif claim.status == "not_enough_evidence":
            citation_bonus = 8.0 if claim.citations else 0.0
            penalties.append(18.0 + (0.22 * confidence) + citation_bonus)
        else:
            penalties.append(max(2.0, 12.0 - (0.10 * confidence)))

    return round(clamp(sum(penalties) / len(penalties)), 2)


def score_scam(cues: list[str]) -> float:
    if not cues:
        return 10.0
    return clamp(20 + len(cues) * 20)


def score_manipulation(cues: list[str], aiornot: dict[str, float] | None) -> float:
    # Weight stronger synthetic cues above generic mentions.
    weighted = 0.0
    for cue in cues:
        lc = cue.lower()
        if any(k in lc for k in ["deepfake", "synthetic", "face", "clone", "ai hashtag"]):
            weighted += 22.0
        elif any(k in lc for k in ["ai", "prompt", "render"]):
            weighted += 14.0
        else:
            weighted += 10.0
    cue_score = min(75.0, weighted)
    api_score = 0.0
    if aiornot:
        api_score = (aiornot.get("video_risk", 0.0) + aiornot.get("audio_risk", 0.0)) * 50
    return clamp(cue_score + api_score)


def score_uncertainty(claims: list[ClaimAssessment]) -> float:
    if not claims:
        return 68.0

    penalties: list[float] = []
    for claim in claims:
        confidence = clamp(float(claim.confidence) * 100.0)
        if claim.status == "not_enough_evidence":
            penalties.append(55.0 + (0.30 * confidence))
        elif claim.status == "supported":
            penalties.append(max(8.0, 32.0 - (0.24 * confidence)))
        else:
            penalties.append(max(12.0, 36.0 - (0.22 * confidence)))

    return round(clamp(sum(penalties) / len(penalties)), 2)


def score_evidence_quality_penalty(
    source_token_count: int,
    transcript_present: bool,
    claims: list[ClaimAssessment],
) -> float:
    """Return penalty (0..100) where higher means poorer evidence quality."""
    tokens = max(0, min(int(source_token_count), 260))
    token_quality = (tokens / 260.0) * 100.0
    transcript_quality = 100.0 if transcript_present else (40.0 if tokens >= 20 else 20.0)

    if claims:
        decisive = sum(1 for c in claims if c.status in {"supported", "refuted"})
        citationful = sum(1 for c in claims if c.citations)
        decisive_quality = (decisive / len(claims)) * 100.0
        citation_quality = (citationful / len(claims)) * 100.0
    else:
        decisive_quality = 0.0
        citation_quality = 0.0

    quality = (
        0.40 * token_quality
        + 0.20 * transcript_quality
        + 0.23 * decisive_quality
        + 0.17 * citation_quality
    )
    return clamp(100.0 - quality)


def score_analysis_confidence(
    source_token_count: int,
    transcript_present: bool,
    claims: list[ClaimAssessment],
    evidence_level: str = "low",
    ocr_present: bool = False,
    asr_present: bool = False,
) -> float:
    """Estimate how much the final score should trust available evidence."""
    tokens = max(0, min(int(source_token_count), 180))
    token_signal = tokens / 180.0
    transcript_signal = 1.0 if transcript_present else (0.35 if tokens >= 20 else 0.0)

    if claims:
        decisive = sum(1 for c in claims if c.status in {"supported", "refuted"})
        citationful = sum(1 for c in claims if c.citations)
        decisive_signal = decisive / len(claims)
        citation_signal = citationful / len(claims)
    else:
        decisive_signal = 0.0
        citation_signal = 0.0

    modality_signal = 0.0
    if ocr_present:
        modality_signal += 0.35
    if asr_present:
        modality_signal += 0.45
    if tokens > 0:
        modality_signal += 0.20
    modality_signal = min(1.0, modality_signal)

    coverage_signal = {
        "low": 0.20,
        "medium": 0.60,
        "high": 1.0,
    }.get(str(evidence_level).lower(), 0.20)

    confidence = 100.0 * (
        0.28 * token_signal
        + 0.22 * transcript_signal
        + 0.20 * decisive_signal
        + 0.15 * citation_signal
        + 0.15 * max(modality_signal, coverage_signal)
    )
    return round(clamp(confidence), 2)


def score_generation_origin(
    text: str,
    manipulation_cues: list[str],
    aiornot: dict[str, float] | None,
) -> float:
    """Returns AI-generation likelihood on a 0..100 scale.

    Low score => likely human-generated.
    High score => likely AI-generated/synthetic.
    """
    cue_score = min(55.0, len(manipulation_cues) * 16.0)
    api_score = 0.0
    if aiornot:
        api_score = (aiornot.get("video_risk", 0.0) * 70) + (aiornot.get("audio_risk", 0.0) * 30)

    lowered = text.lower()
    keyword_bonus = 0.0
    for k in ["ai generated", "ai-made", "deepfake", "synthetic", "voice clone", "face swap"]:
        if k in lowered:
            keyword_bonus += 8.0

    # Self-declared AI tags/phrases should strongly influence origin scoring.
    declaration_bonus = 0.0
    declaration_patterns = [
        "#ai",
        "#aigenerated",
        "#aiart",
        "#midjourney",
        "#stablediffusion",
        "made with ai",
        "generated with ai",
        "this is ai",
        "ai video",
        "ai clip",
    ]
    for pat in declaration_patterns:
        if pat in lowered:
            declaration_bonus += 20.0

    # Generic AI mentions are weaker than explicit declarations.
    if " ai " in f" {lowered} ":
        declaration_bonus += 8.0

    return clamp(cue_score + api_score + keyword_bonus + declaration_bonus)


def aggregate_credibility(
    misinformation: float,
    scam: float,
    manipulation: float,
    uncertainty: float,
    evidence_quality: float,
    analysis_confidence: float = 100.0,
) -> float:
    penalty = (
        0.34 * misinformation
        + 0.24 * scam
        + 0.18 * manipulation
        + 0.12 * uncertainty
        + 0.12 * evidence_quality
    )
    raw_score = clamp(100 - penalty)
    confidence = clamp(analysis_confidence) / 100.0
    neutral_anchor = 52.0
    blended_score = (raw_score * confidence) + (neutral_anchor * (1.0 - confidence))
    return round(clamp(blended_score), 2)


def build_flags(component_scores: dict[str, float]) -> list[RiskFlag]:
    rationales = {
        "misinformation": "Claim-level assessment found potentially false or weakly supported assertions.",
        "scam": "Language cues suggest potential fraud or deceptive persuasion patterns.",
        "manipulation": "Audio/visual indicators suggest potential synthetic or altered media.",
        "uncertainty": "Evidence availability is limited for one or more key claims.",
        "generation_origin": "Estimated clip origin based on synthetic-media signals.",
        "evidence_quality": "Evidence quality for this analysis is limited by extracted text depth and citations.",
    }

    flags: list[RiskFlag] = []
    for key, score in component_scores.items():
        rationale = rationales[key]
        if key == "generation_origin":
            if score >= 70:
                rationale = "AI-generated content likely."
            elif score >= 35:
                rationale = "Origin uncertain (mixed AI/human signals)."
            else:
                rationale = "Human-generated content likely."
        if key == "evidence_quality":
            if score >= 70:
                rationale = "Low evidence quality: sparse extracted text and weak citation support."
            elif score >= 35:
                rationale = "Moderate evidence quality: partial transcript/OCR and limited citation support."
            else:
                rationale = "High evidence quality: richer extracted text with better citation support."
        flags.append(
            RiskFlag(
                type=key,
                level=level_from_score(score),
                score=round(score, 2),
                rationale=rationale,
            )
        )
    return flags
