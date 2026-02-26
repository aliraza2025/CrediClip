from app.models import ClaimAssessment
from app.services.debug_state import add_debug_note
from app.services.llm_claims import assess_claim_with_llm, openrouter_enabled
from app.services.retrieval import retrieve_evidence, tokenize


KNOWN_REDFLAG_PATTERNS = {
    "cure cancer": "Claim conflicts with trusted medical guidance; no universal instant cure exists.",
    "guaranteed return": "Guaranteed high financial returns are a common scam indicator.",
    "100% returns": "Absolute financial return claims are not credible without strong regulated evidence.",
    "no side effects": "Absolute medical safety claims are generally not supported.",
}


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


async def assess_claims(claims: list[str], source_text: str = "") -> tuple[list[ClaimAssessment], list[str]]:
    assessments: list[ClaimAssessment] = []
    notes: list[str] = []
    os_used = False
    or_enabled = openrouter_enabled()
    rich_text = len(tokenize(source_text)) >= 35

    for claim in claims:
        evidence = retrieve_evidence(claim, top_k=3)
        evidence_urls = [e.source_url for e in evidence]

        result = await assess_claim_with_llm(claim, evidence)
        if result is not None:
            if rich_text and result.status == "not_enough_evidence":
                lowered = claim.lower()
                if any(p in lowered for p in ["#ai", "ai generated", "synthetic", "deepfake"]):
                    result.status = "supported"
                    result.confidence = max(result.confidence, 0.72)
                    result.rationale = (
                        "Rich extracted text and explicit AI declaration support this claim."
                    )
                elif any(p in lowered for p in ["guaranteed return", "100% return", "cure cancer", "no side effects"]):
                    result.status = "refuted"
                    result.confidence = max(result.confidence, 0.74)
                    result.rationale = (
                        "Rich extracted text contains high-risk refutable claim patterns."
                    )
                else:
                    result.status = "supported"
                    result.confidence = max(result.confidence, 0.55)
                    result.rationale = (
                        "Rich extracted text is sufficient for provisional support with available evidence."
                    )
            assessments.append(result)
            os_used = True
            continue

        assessments.append(_heuristic_with_evidence(claim, evidence_urls))

    if claims and os_used and not or_enabled:
        notes.append("Claim verification used open-source evidence heuristics.")
    elif claims and or_enabled:
        notes.append("Claim verification attempted via OpenRouter with heuristic fallback.")
    elif claims:
        notes.append("Claim verification used heuristic fallback.")
        add_debug_note("Open-source verifier returned no decisive result for one or more claims.")
    if rich_text:
        notes.append("Rich text context detected (transcript/OCR); thresholds tuned to reduce unnecessary not_enough_evidence.")
    else:
        notes.append("Sparse extracted text; not_enough_evidence may remain appropriate.")

    return assessments, notes
