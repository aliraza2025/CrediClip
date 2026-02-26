from urllib.parse import urlparse

from app.models import AnalyzeRequest, AnalyzeResponse, ClaimAssessment
from app.services.claim_checker import assess_claims
from app.services.debug_state import get_debug_notes, reset_debug_notes
from app.services.detectors import optional_aiornot_scan
from app.services.extractors import extract_signals
from app.services.ingestion import enrich_from_youtube
from app.services.scoring import (
    aggregate_credibility,
    build_flags,
    score_generation_origin,
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
    reset_debug_notes()
    platform = infer_platform(str(request.url))
    caption = request.caption.strip()
    transcript = request.transcript.strip()
    notes: list[str] = []

    if not caption and not transcript:
        if platform == "youtube_shorts":
            auto_caption, auto_transcript, ingest_notes = await enrich_from_youtube(str(request.url))
            notes.extend(ingest_notes)
            caption = auto_caption.strip()
            transcript = auto_transcript.strip()
        else:
            notes.append(
                "Link-only auto-ingestion is not enabled for this platform in v1; running limited analysis."
            )

    no_text_mode = not caption and not transcript
    if no_text_mode:
        notes.append(
            "Could not extract transcript/caption from this link. Returning limited-confidence report."
        )
        claims = [
            ClaimAssessment(
                claim="Insufficient extracted text for factual verification from URL-only input.",
                status="not_enough_evidence",
                confidence=0.99,
                rationale="No transcript/caption was available, so claim-level verification is limited.",
                citations=[],
            )
        ]
        signals = extract_signals("", "")
        claim_notes: list[str] = []
    else:
        signals = extract_signals(caption, transcript)
        claims, claim_notes = await assess_claims(signals.claims)

    external_scan = await optional_aiornot_scan(str(request.url))

    if no_text_mode:
        misinformation = 65.0
        scam = 25.0
        manipulation = max(25.0, score_manipulation(signals.manipulation_cues, external_scan))
        uncertainty = 100.0
    else:
        misinformation = score_misinformation(claims)
        scam = score_scam(signals.scam_cues)
        manipulation = score_manipulation(signals.manipulation_cues, external_scan)
        uncertainty = score_uncertainty(claims)

    generation_origin = score_generation_origin(
        text=f"{caption}\n{transcript}",
        manipulation_cues=signals.manipulation_cues,
        aiornot=external_scan,
    )

    component_scores = {
        "misinformation": misinformation,
        "scam": scam,
        "manipulation": manipulation,
        "uncertainty": uncertainty,
        "generation_origin": generation_origin,
    }

    credibility_score = aggregate_credibility(
        misinformation=misinformation,
        scam=scam,
        manipulation=manipulation,
        uncertainty=uncertainty,
    )

    notes.extend(claim_notes)
    if external_scan is None:
        notes.append("External deepfake API not configured; manipulation score uses heuristic signals.")
    if not transcript:
        notes.append("No transcript provided; claim extraction may be incomplete.")
    notes.extend(get_debug_notes())

    return AnalyzeResponse(
        platform=platform,
        credibility_score=credibility_score,
        flags=build_flags(component_scores),
        claim_assessments=claims,
        component_scores={k: round(v, 2) for k, v in component_scores.items()},
        notes=notes,
    )
