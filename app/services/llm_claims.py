from app.models import ClaimAssessment
from app.services.debug_state import add_debug_note
from app.services.retrieval import EvidenceChunk, tokenize


REFUTE_PATTERNS = {
    "cure cancer": "Claim conflicts with established medical guidance.",
    "guaranteed return": "Guaranteed financial return language is a known scam signal.",
    "100% return": "Absolute financial return claims are not credible without regulated proof.",
    "no side effects": "Absolute medical safety claims are generally unreliable.",
}


def _lexical_support_score(claim: str, evidence: list[EvidenceChunk]) -> tuple[float, list[str]]:
    claim_tokens = set(tokenize(claim))
    if not claim_tokens:
        return 0.0, []

    best_score = 0.0
    best_urls: list[str] = []

    for chunk in evidence:
        evidence_tokens = set(tokenize(f"{chunk.title} {chunk.text}"))
        overlap = claim_tokens.intersection(evidence_tokens)
        score = len(overlap) / len(claim_tokens)
        if score > best_score:
            best_score = score
            best_urls = [chunk.source_url]

    return best_score, best_urls


async def assess_claim_with_llm(claim: str, evidence: list[EvidenceChunk]) -> ClaimAssessment | None:
    """Open-source verifier replacement for prior LLM call.

    Keeps function signature to avoid wider refactors.
    """
    lowered = claim.lower()
    for pattern, rationale in REFUTE_PATTERNS.items():
        if pattern in lowered:
            add_debug_note("Open-source verifier: refute pattern matched.")
            citations = [evidence[0].source_url] if evidence else []
            return ClaimAssessment(
                claim=claim,
                status="refuted",
                confidence=0.82,
                rationale=rationale,
                citations=citations,
            )

    support_score, urls = _lexical_support_score(claim, evidence)

    if support_score >= 0.55:
        add_debug_note("Open-source verifier: lexical evidence support high.")
        return ClaimAssessment(
            claim=claim,
            status="supported",
            confidence=min(0.85, 0.55 + support_score * 0.4),
            rationale="Claim has strong lexical overlap with retrieved trusted evidence.",
            citations=urls,
        )

    if support_score >= 0.30:
        add_debug_note("Open-source verifier: lexical overlap medium; uncertain verdict.")
        return ClaimAssessment(
            claim=claim,
            status="not_enough_evidence",
            confidence=0.58,
            rationale="Evidence is partially related but insufficient for confident support/refutation.",
            citations=urls,
        )

    add_debug_note("Open-source verifier: low overlap; uncertain verdict.")
    return ClaimAssessment(
        claim=claim,
        status="not_enough_evidence",
        confidence=0.52,
        rationale="Retrieved trusted evidence does not strongly address this claim.",
        citations=urls,
    )
