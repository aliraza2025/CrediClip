from urllib.parse import urlparse

from app.models import AnalyzeRequest, AnalyzeResponse
from app.services.claim_checker import assess_claims
from app.services.detectors import optional_aiornot_scan
from app.services.extractors import extract_signals
from app.services.scoring import (
    aggregate_credibility,
    build_flags,
    score_manipulation,
    score_misinformation,
    score_scam,
    score_uncertainty,
)


SUPPORTED_DOMAINS = {
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "youtu.be": "youtube",
}


def infer_platform(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in SUPPORTED_DOMAINS:
        platform = SUPPORTED_DOMAINS[host]
        if platform == "youtube":
            if host == "youtu.be" or parsed.path.startswith("/shorts/"):
                return "youtube_shorts"
            raise ValueError("For YouTube, provide a Shorts URL (e.g., /shorts/<id>).")
        return platform
    raise ValueError("Only public TikTok, Instagram, and YouTube Shorts URLs are supported in this MVP.")


async def analyze_video(request: AnalyzeRequest) -> AnalyzeResponse:
    platform = infer_platform(str(request.url))
    signals = extract_signals(request.caption, request.transcript)
    claims = assess_claims(signals.claims)

    external_scan = await optional_aiornot_scan(str(request.url))

    misinformation = score_misinformation(claims)
    scam = score_scam(signals.scam_cues)
    manipulation = score_manipulation(signals.manipulation_cues, external_scan)
    uncertainty = score_uncertainty(claims)

    component_scores = {
        "misinformation": misinformation,
        "scam": scam,
        "manipulation": manipulation,
        "uncertainty": uncertainty,
    }

    credibility_score = aggregate_credibility(
        misinformation=misinformation,
        scam=scam,
        manipulation=manipulation,
        uncertainty=uncertainty,
    )

    notes: list[str] = []
    if external_scan is None:
        notes.append("External deepfake API not configured; manipulation score uses heuristic signals.")
    if not request.transcript:
        notes.append("No transcript provided; claim extraction may be incomplete.")

    return AnalyzeResponse(
        platform=platform,
        credibility_score=credibility_score,
        flags=build_flags(component_scores),
        claim_assessments=claims,
        component_scores={k: round(v, 2) for k, v in component_scores.items()},
        notes=notes,
    )
