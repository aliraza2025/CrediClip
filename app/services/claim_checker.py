from __future__ import annotations

import os
import re

from app.models import ClaimAssessment
from app.services.debug_state import add_debug_note
from app.services.llm_claims import assess_claim_with_llm, llm_provider_label
from app.services.retrieval import retrieve_evidence, tokenize


KNOWN_REDFLAG_PATTERNS = {
    "cure cancer": "Claim conflicts with trusted medical guidance; no universal instant cure exists.",
    "guaranteed return": "Guaranteed high financial returns are a common scam indicator.",
    "100% returns": "Absolute financial return claims are not credible without strong regulated evidence.",
    "no side effects": "Absolute medical safety claims are generally not supported.",
}


def _platform_env_suffix(platform: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", platform.strip().upper())


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name, "").strip() or "")
    try:
        return int(raw) if raw else default
    except Exception:
        return default


def _str_env(name: str, default: str) -> str:
    return (os.getenv(name, "").strip() or default).strip()


def _safe_confidence(value: object, default: float = 0.5) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _llm_allowed_for_evidence(
    provider: str,
    platform: str,
    source_token_count: int,
    transcript_present: bool,
    evidence_level: str,
) -> bool:
    if provider not in {"groq", "openai"}:
        return provider != "none"

    if provider == "openai":
        allowed_raw = _str_env("OPENAI_CLAIM_ALLOWED_PLATFORMS", "instagram,tiktok,youtube_shorts")
        allowed_platforms = {p.strip().lower() for p in allowed_raw.split(",") if p.strip()}
        normalized_platform = platform.strip().lower()
        if allowed_platforms and "all" not in allowed_platforms and normalized_platform not in allowed_platforms:
            return False
        suffix = _platform_env_suffix(normalized_platform or "unknown")
        min_tokens = _int_env(
            f"OPENAI_CLAIM_MIN_TOKENS_{suffix}",
            _int_env("OPENAI_CLAIM_MIN_TOKENS", 50),
        )
        min_level = _str_env(
            f"OPENAI_CLAIM_MIN_EVIDENCE_LEVEL_{suffix}",
            _str_env("OPENAI_CLAIM_MIN_EVIDENCE_LEVEL", "medium"),
        ).lower()
    else:
        min_tokens = _int_env("GROQ_CLAIM_MIN_TOKENS", 40)
        min_level = _str_env("GROQ_CLAIM_MIN_EVIDENCE_LEVEL", "medium").lower()

    if source_token_count < min_tokens:
        return False
    if transcript_present:
        return True
    ordered_levels = {"low": 0, "medium": 1, "high": 2}
    return ordered_levels.get(evidence_level, 0) >= ordered_levels.get(min_level, 1)


def _heuristic_with_evidence(claim: str, evidence_urls: list[str]) -> ClaimAssessment:
    lowered = claim.lower()
    for pattern, rationale in KNOWN_REDFLAG_PATTERNS.items():
        if pattern in lowered:
            return ClaimAssessment(
                claim=claim,
                status="refuted",
                confidence=0.80,
                rationale=rationale,
                citations=evidence_urls[:1],
            )

    if any(token in lowered for token in ["study", "report", "according to", "data", "proven"]):
        return ClaimAssessment(
            claim=claim,
            status="not_enough_evidence",
            confidence=0.56,
            rationale="Claim is check-worthy but available evidence is insufficient for strong support/refutation.",
            citations=evidence_urls[:2],
        )

    return ClaimAssessment(
        claim=claim,
        status="not_enough_evidence",
        confidence=0.52,
        rationale="No decisive contradiction found, but evidence support is not strong enough for a supported verdict.",
        citations=evidence_urls[:1],
    )


async def assess_claims(
    claims: list[str],
    source_text: str = "",
    transcript_present: bool = False,
    evidence_level: str = "low",
    platform: str = "",
) -> tuple[list[ClaimAssessment], list[str]]:
    assessments: list[ClaimAssessment] = []
    notes: list[str] = []
    provider = llm_provider_label()
    source_token_count = len(tokenize(source_text))
    rich_text = source_token_count >= 35
    llm_attempted = False
    llm_used = False
    llm_skipped_for_evidence = claims and not _llm_allowed_for_evidence(
        provider=provider,
        platform=platform,
        source_token_count=source_token_count,
        transcript_present=transcript_present,
        evidence_level=evidence_level,
    )

    for claim in claims:
        evidence = retrieve_evidence(claim, top_k=3)
        evidence_urls = [e.source_url for e in evidence]

        result = None
        if not llm_skipped_for_evidence:
            llm_attempted = provider != "none"
            result = await assess_claim_with_llm(claim, evidence)
        if result is not None:
            conf = _safe_confidence(result.confidence, default=0.5)
            if not rich_text and result.status in {"supported", "refuted"}:
                result.status = "not_enough_evidence"
                result.confidence = min(conf, 0.62)
                result.rationale = (
                    "Sparse extracted text without transcript is insufficient for a decisive verdict."
                )
            if rich_text and result.status == "not_enough_evidence":
                lowered = claim.lower()
                if any(p in lowered for p in ["#ai", "ai generated", "synthetic", "deepfake"]):
                    result.status = "supported"
                    result.confidence = max(conf, 0.72)
                    result.rationale = (
                        "Rich extracted text and explicit AI declaration support this claim."
                    )
                elif any(p in lowered for p in ["guaranteed return", "100% return", "cure cancer", "no side effects"]):
                    result.status = "refuted"
                    result.confidence = max(conf, 0.74)
                    result.rationale = (
                        "Rich extracted text contains high-risk refutable claim patterns."
                    )
                else:
                    result.status = "supported"
                    result.confidence = max(conf, 0.55)
                    result.rationale = (
                        "Rich extracted text is sufficient for provisional support with available evidence."
                    )
            assessments.append(result)
            llm_used = True
            continue

        assessments.append(_heuristic_with_evidence(claim, evidence_urls))

    if claims and llm_used and provider == "local_ollama":
        notes.append("Claim verification attempted via local Ollama LLM with heuristic fallback.")
    elif claims and llm_used and provider == "groq":
        notes.append("Claim verification attempted via Groq with heuristic fallback.")
    elif claims and llm_used and provider == "openai":
        notes.append("Claim verification attempted via OpenAI with heuristic fallback.")
    elif claims and llm_used and provider == "openrouter":
        notes.append("Claim verification attempted via OpenRouter with heuristic fallback.")
    elif claims and provider == "groq" and llm_skipped_for_evidence:
        notes.append("Groq claim verification skipped because extracted evidence was too thin; using heuristics.")
        add_debug_note(
            "Groq claim verification skipped because evidence did not meet the minimum transcript/token threshold."
        )
    elif claims and provider == "openai" and llm_skipped_for_evidence:
        notes.append("OpenAI claim verification skipped because extracted evidence was too thin; using heuristics.")
        add_debug_note(
            "OpenAI claim verification skipped because platform or evidence did not meet the configured threshold."
        )
    elif claims and llm_attempted and provider == "groq":
        notes.append("Claim verification attempted via Groq with heuristic fallback.")
    elif claims and llm_attempted and provider == "openai":
        notes.append("Claim verification attempted via OpenAI with heuristic fallback.")
    elif claims and llm_attempted and provider == "openrouter":
        notes.append("Claim verification attempted via OpenRouter with heuristic fallback.")
    elif claims and llm_attempted and provider == "local_ollama":
        notes.append("Claim verification attempted via local Ollama LLM with heuristic fallback.")
    elif claims and provider == "none":
        notes.append("Claim verification used open-source evidence heuristics.")
    elif claims:
        notes.append("Claim verification used heuristic fallback.")
        add_debug_note("Open-source verifier returned no decisive result for one or more claims.")
    if rich_text:
        notes.append("Rich text context detected (transcript/OCR); thresholds tuned to reduce unnecessary not_enough_evidence.")
    else:
        notes.append("Sparse extracted text; not_enough_evidence may remain appropriate.")

    return assessments, notes
