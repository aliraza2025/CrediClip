from __future__ import annotations

import re
from urllib.parse import urlparse

from app.models import AnalyzeRequest, AnalyzeResponse, ClaimAssessment, EvidenceCoverage
from app.services.claim_checker import assess_claims
from app.services.debug_state import get_debug_notes, reset_debug_notes
from app.services.detectors import optional_aiornot_scan
from app.services.generation_training import apply_generation_training_override
from app.services.extractors import extract_signals
from app.services.instagram_ingestion import enrich_from_instagram
from app.services.tiktok_ingestion import enrich_from_tiktok
from app.services.ingestion import enrich_from_youtube, extract_youtube_video_id
from app.services.retrieval import tokenize
from app.services.scoring import (
    aggregate_credibility,
    build_flags,
    score_analysis_confidence,
    score_evidence_quality_penalty,
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


def _calibrate_sparse_text_scores(
    misinformation: float,
    uncertainty: float,
    claims: list[ClaimAssessment],
    source_token_count: int,
) -> tuple[float, float, bool]:
    """Reduce score collapse when all claims are uncertain under sparse text.

    This keeps the model conservative but avoids identical 75/100-style outputs
    for many unrelated links with tiny extracted text.
    """
    if not claims:
        return misinformation, uncertainty, False

    if not all(c.status == "not_enough_evidence" for c in claims):
        return misinformation, uncertainty, False

    tokens = max(0, min(int(source_token_count), 80))
    # Sparse text should stay cautious, but not read as near-certain uncertainty.
    calibrated_uncertainty = max(52.0, round(76.0 - (tokens * 0.35), 2))
    # Keep a mild misinformation penalty without collapsing many inputs to one band.
    calibrated_misinformation = max(10.0, round(22.0 - (tokens * 0.08), 2))
    return calibrated_misinformation, calibrated_uncertainty, True


def _is_low_evidence_regime(transcript: str, source_token_count: int) -> bool:
    return (not transcript.strip()) and source_token_count < 35


def _build_evidence_coverage(
    caption: str, transcript: str, source_token_count: int, ingest_evidence: dict | None = None
) -> EvidenceCoverage:
    ingest_evidence = ingest_evidence or {}
    caption_tokens = int(ingest_evidence.get("caption_tokens", len(tokenize(caption))))
    transcript_tokens = int(ingest_evidence.get("transcript_tokens", len(tokenize(transcript))))
    ocr_tokens = int(ingest_evidence.get("ocr_tokens", 0))
    asr_tokens = int(ingest_evidence.get("asr_tokens", 0))
    total_tokens = int(ingest_evidence.get("total_tokens", source_token_count))

    if total_tokens >= 120 or (transcript_tokens + ocr_tokens) >= 80:
        level = "high"
    elif total_tokens >= 40 or (transcript_tokens + ocr_tokens) >= 20:
        level = "medium"
    else:
        level = "low"
    ingest_level = str(ingest_evidence.get("level", "")).strip().lower()
    if ingest_level not in {"low", "medium", "high"}:
        ingest_level = level

    return EvidenceCoverage(
        total_tokens=max(0, total_tokens),
        caption_tokens=max(0, caption_tokens),
        transcript_tokens=max(0, transcript_tokens),
        ocr_tokens=max(0, ocr_tokens),
        asr_tokens=max(0, asr_tokens),
        level=ingest_level,
        transcript_present=bool(ingest_evidence.get("transcript_present", bool(transcript.strip()))),
        ocr_present=bool(ingest_evidence.get("ocr_present", ocr_tokens > 0)),
        asr_present=bool(ingest_evidence.get("asr_present", asr_tokens > 0)),
    )


def normalize_input_url(url: str) -> tuple[str, bool]:
    raw = (url or "").strip()
    # Handle escaped query chars coming from pasted CSV or shell-escaped text.
    cleaned = re.sub(r"\\([?=&/])", r"\1", raw)
    changed = cleaned != raw

    parsed = urlparse(cleaned)
    host = parsed.netloc.lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        vid = extract_youtube_video_id(cleaned)
        if vid:
            canonical = f"https://www.youtube.com/shorts/{vid}"
            if canonical != cleaned:
                changed = True
            cleaned = canonical
    elif host in {"instagram.com", "www.instagram.com"}:
        trimmed_path = parsed.path.rstrip("/")
        if trimmed_path:
            canonical = f"https://www.instagram.com{trimmed_path}/"
            if canonical != cleaned:
                changed = True
            cleaned = canonical
    elif host in {"tiktok.com", "www.tiktok.com"}:
        trimmed_path = parsed.path.rstrip("/")
        if trimmed_path:
            canonical = f"https://www.tiktok.com{trimmed_path}"
            if parsed.query:
                canonical = f"{canonical}?{parsed.query}"
            if canonical != cleaned:
                changed = True
            cleaned = canonical
    return cleaned, changed


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
    normalized_url, normalized = normalize_input_url(str(request.url))
    platform = infer_platform(normalized_url)
    caption = request.caption.strip()
    transcript = request.transcript.strip()
    ingest_evidence: dict | None = dict(request.ingest_evidence or {}) or None
    notes: list[str] = []
    if normalized:
        notes.append("Normalized input URL to canonical format before analysis.")

    if not caption and not transcript:
        if platform == "youtube_shorts":
            auto_caption, auto_transcript, ingest_notes, ingest_evidence = await enrich_from_youtube(
                normalized_url, include_evidence=True
            )
            notes.extend(ingest_notes)
            caption = auto_caption.strip()
            transcript = auto_transcript.strip()
        elif platform == "instagram":
            auto_caption, auto_transcript, ingest_notes, ingest_evidence = await enrich_from_instagram(
                normalized_url, include_evidence=True
            )
            notes.extend(ingest_notes)
            caption = auto_caption.strip()
            transcript = auto_transcript.strip()
        elif platform == "tiktok":
            auto_caption, auto_transcript, ingest_notes, ingest_evidence = await enrich_from_tiktok(
                normalized_url, include_evidence=True
            )
            notes.extend(ingest_notes)
            caption = auto_caption.strip()
            transcript = auto_transcript.strip()
        else:
            notes.append(
                "Link-only auto-ingestion is not enabled for this platform in v1; running limited analysis."
            )

    no_text_mode = not caption and not transcript
    source_token_count = len(tokenize(f"{caption}\n{transcript}"))
    evidence_coverage = _build_evidence_coverage(
        caption=caption,
        transcript=transcript,
        source_token_count=source_token_count,
        ingest_evidence=ingest_evidence,
    )
    if no_text_mode:
        notes.append(
            "Could not extract transcript/caption from this link. Returning limited-confidence report."
        )
        notes.append("Ingestion quality: insufficient evidence extracted from source media.")
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
        claims, claim_notes = await assess_claims(
            signals.claims,
            source_text=f"{caption}\n{transcript}",
            transcript_present=evidence_coverage.transcript_present,
            evidence_level=evidence_coverage.level,
            platform=platform,
        )

    external_scan = await optional_aiornot_scan(normalized_url)

    if no_text_mode:
        misinformation = 65.0
        scam = 25.0
        manipulation = max(25.0, score_manipulation(signals.manipulation_cues, external_scan))
        uncertainty = 100.0
        evidence_quality = 100.0
    else:
        misinformation = score_misinformation(claims)
        scam = score_scam(signals.scam_cues)
        manipulation = score_manipulation(signals.manipulation_cues, external_scan)
        uncertainty = score_uncertainty(claims)
        misinformation, uncertainty, sparse_calibrated = _calibrate_sparse_text_scores(
            misinformation=misinformation,
            uncertainty=uncertainty,
            claims=claims,
            source_token_count=source_token_count,
        )
        if sparse_calibrated:
            notes.append(
                "Applied sparse-text score calibration to reduce flat scoring under limited extracted evidence."
            )
        evidence_quality = score_evidence_quality_penalty(
            source_token_count=source_token_count,
            transcript_present=bool(transcript.strip()),
            claims=claims,
        )

    analysis_confidence = score_analysis_confidence(
        source_token_count=source_token_count,
        transcript_present=bool(transcript.strip()),
        claims=claims,
        evidence_level=evidence_coverage.level,
        ocr_present=evidence_coverage.ocr_present,
        asr_present=evidence_coverage.asr_present,
    )

    base_credibility = aggregate_credibility(
        misinformation=misinformation,
        scam=scam,
        manipulation=manipulation,
        uncertainty=uncertainty,
        evidence_quality=evidence_quality,
        analysis_confidence=analysis_confidence,
    )

    generation_origin = score_generation_origin(
        text=f"{caption}\n{transcript}",
        manipulation_cues=signals.manipulation_cues,
        aiornot=external_scan,
    )
    top_claim = claims[0] if claims else None
    generation_features = {
        "generation_origin_score": float(generation_origin),
        "manipulation_score": float(manipulation),
        "uncertainty_score": float(uncertainty),
        "evidence_quality_score": float(evidence_quality),
        "top_claim_confidence": float(top_claim.confidence if top_claim else 0.5),
        "top_claim_not_enough": 1.0 if (top_claim and top_claim.status == "not_enough_evidence") else 0.0,
        "credibility_inverse": max(0.0, min(100.0, 100.0 - float(base_credibility))),
    }
    generation_origin, generation_note = apply_generation_training_override(
        normalized_url, generation_origin, feature_values=generation_features
    )
    if generation_note:
        notes.append(generation_note)

    component_scores = {
        "misinformation": misinformation,
        "scam": scam,
        "manipulation": manipulation,
        "uncertainty": uncertainty,
        "generation_origin": generation_origin,
        "evidence_quality": evidence_quality,
    }

    credibility_score = aggregate_credibility(
        misinformation=misinformation,
        scam=scam,
        manipulation=manipulation,
        uncertainty=uncertainty,
        evidence_quality=evidence_quality,
        analysis_confidence=analysis_confidence,
    )
    low_evidence_regime = _is_low_evidence_regime(transcript, source_token_count)
    if low_evidence_regime and credibility_score > 64.0:
        credibility_score = 64.0
        notes.append("Applied low-evidence confidence cap due to missing transcript and sparse extracted text.")

    notes.extend(claim_notes)
    notes.append(f"Analysis-confidence score: {round(analysis_confidence, 2)}.")
    notes.append(f"Evidence-quality penalty score: {round(evidence_quality, 2)}.")
    notes.append(f"Extracted evidence token count: {source_token_count}.")
    if analysis_confidence < 45.0:
        notes.append("Final score was blended toward a neutral baseline because evidence confidence is limited.")
    notes.append(
        "Evidence coverage: "
        f"total={evidence_coverage.total_tokens}, "
        f"caption={evidence_coverage.caption_tokens}, "
        f"transcript={evidence_coverage.transcript_tokens}, "
        f"ocr={evidence_coverage.ocr_tokens}, "
        f"asr={evidence_coverage.asr_tokens}, "
        f"level={evidence_coverage.level}."
    )
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
        evidence_coverage=evidence_coverage,
        notes=notes,
    )
