from app.models import ClaimAssessment


KNOWN_REDFLAG_PATTERNS = {
    "cure cancer": "Medical misinformation pattern detected.",
    "guaranteed return": "Financial scam/misinformation pattern detected.",
    "100% returns": "Unrealistic financial claim pattern detected.",
    "no side effects": "Potentially unsafe absolute medical claim detected.",
}


TRUSTED_BASE_CITATIONS = [
    "https://www.cdc.gov",
    "https://www.who.int",
    "https://www.consumerfinance.gov",
    "https://www.ftc.gov",
]


def assess_claim(claim: str) -> ClaimAssessment:
    lowered = claim.lower()

    for pattern, rationale in KNOWN_REDFLAG_PATTERNS.items():
        if pattern in lowered:
            return ClaimAssessment(
                claim=claim,
                status="refuted",
                confidence=0.82,
                rationale=rationale,
                citations=["https://www.ftc.gov/scams"],
            )

    if any(token in lowered for token in ["study", "report", "according to", "data"]):
        return ClaimAssessment(
            claim=claim,
            status="not_enough_evidence",
            confidence=0.55,
            rationale="Claim appears check-worthy but needs source-grounded verification.",
            citations=TRUSTED_BASE_CITATIONS[:2],
        )

    return ClaimAssessment(
        claim=claim,
        status="supported",
        confidence=0.60,
        rationale="No strong red flags found in baseline heuristic pass.",
        citations=[TRUSTED_BASE_CITATIONS[0]],
    )


def assess_claims(claims: list[str]) -> list[ClaimAssessment]:
    return [assess_claim(claim) for claim in claims]
