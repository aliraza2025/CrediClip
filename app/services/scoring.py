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
) -> float:
    penalty = 0.40 * misinformation + 0.30 * scam + 0.20 * manipulation + 0.10 * uncertainty
    return round(clamp(100 - penalty), 2)


def build_flags(component_scores: dict[str, float]) -> list[RiskFlag]:
    rationales = {
        "misinformation": "Claim-level assessment found potentially false or weakly supported assertions.",
        "scam": "Language cues suggest potential fraud or deceptive persuasion patterns.",
        "manipulation": "Audio/visual indicators suggest potential synthetic or altered media.",
        "uncertainty": "Evidence availability is limited for one or more key claims.",
        "generation_origin": "Estimated clip origin based on synthetic-media signals.",
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
        flags.append(
            RiskFlag(
                type=key,
                level=level_from_score(score),
                score=round(score, 2),
                rationale=rationale,
            )
        )
    return flags
