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
        return 15.0
    refuted = sum(1 for c in claims if c.status == "refuted")
    uncertain = sum(1 for c in claims if c.status == "not_enough_evidence")
    raw = (refuted / len(claims)) * 100 + (uncertain / len(claims)) * 30
    return clamp(raw)


def score_scam(cues: list[str]) -> float:
    if not cues:
        return 10.0
    return clamp(20 + len(cues) * 20)


def score_manipulation(cues: list[str], aiornot: dict[str, float] | None) -> float:
    cue_score = min(60.0, len(cues) * 18.0)
    api_score = 0.0
    if aiornot:
        api_score = (aiornot.get("video_risk", 0.0) + aiornot.get("audio_risk", 0.0)) * 50
    return clamp(cue_score + api_score)


def score_uncertainty(claims: list[ClaimAssessment]) -> float:
    if not claims:
        return 60.0
    uncertain = sum(1 for c in claims if c.status == "not_enough_evidence")
    return clamp((uncertain / len(claims)) * 100)


def aggregate_credibility(
    misinformation: float,
    scam: float,
    manipulation: float,
    uncertainty: float,
) -> float:
    penalty = 0.40 * misinformation + 0.30 * scam + 0.20 * manipulation + 0.10 * uncertainty
    return round(clamp(100 - penalty), 2)


def build_flags(component_scores: dict[str, float]) -> list[RiskFlag]:
    rationales = {
        "misinformation": "Claim-level assessment found potentially false or weakly supported assertions.",
        "scam": "Language cues suggest potential fraud or deceptive persuasion patterns.",
        "manipulation": "Audio/visual indicators suggest potential synthetic or altered media.",
        "uncertainty": "Evidence availability is limited for one or more key claims.",
    }

    flags: list[RiskFlag] = []
    for key, score in component_scores.items():
        flags.append(
            RiskFlag(
                type=key,
                level=level_from_score(score),
                score=round(score, 2),
                rationale=rationales[key],
            )
        )
    return flags
