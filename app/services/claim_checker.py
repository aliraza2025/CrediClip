from app.models import ClaimAssessment
from app.services.debug_state import add_debug_note
from app.services.llm_claims import assess_claim_with_llm, openrouter_enabled
from app.services.retrieval import retrieve_evidence


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


async def assess_claims(claims: list[str]) -> tuple[list[ClaimAssessment], list[str]]:
    assessments: list[ClaimAssessment] = []
    notes: list[str] = []
    os_used = False
    or_enabled = openrouter_enabled()

    for claim in claims:
        evidence = retrieve_evidence(claim, top_k=3)
        evidence_urls = [e.source_url for e in evidence]

        result = await assess_claim_with_llm(claim, evidence)
        if result is not None:
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

    return assessments, notes
