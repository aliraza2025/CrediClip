import json
import os

import httpx

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
    """Open-source verifier with optional OpenRouter LLM path.

    Keeps function signature to avoid wider refactors.
    """
    # Optional OpenRouter path (free-tier capable depending on selected model).
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free").strip()
    if openrouter_key:
        evidence_block = "\n".join(
            f"[{idx + 1}] {e.title} | {e.source_url}\n{e.text}"
            for idx, e in enumerate(evidence)
        )
        sys_prompt = (
            "Classify the claim using ONLY provided evidence. "
            "Return strict JSON with keys: status, confidence, rationale, citations. "
            "status in [supported, refuted, not_enough_evidence]. "
            "confidence is 0..1 float. citations are source URLs from evidence."
        )
        user_prompt = f"Claim:\n{claim}\n\nEvidence:\n{evidence_block}"
        payload = {
            "model": openrouter_model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
        }
        referer = os.getenv("OPENROUTER_HTTP_REFERER", "").strip()
        title = os.getenv("OPENROUTER_APP_TITLE", "CrediClip").strip()
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            status = parsed.get("status")
            confidence = float(parsed.get("confidence", 0.5))
            rationale = str(parsed.get("rationale", "OpenRouter result missing rationale.")).strip()
            citations = parsed.get("citations") or []
            if status in {"supported", "refuted", "not_enough_evidence"}:
                allowed_urls = {e.source_url for e in evidence}
                citations = [c for c in citations if isinstance(c, str) and c in allowed_urls]
                if not citations and evidence:
                    citations = [evidence[0].source_url]
                add_debug_note("Claim verification used OpenRouter LLM.")
                return ClaimAssessment(
                    claim=claim,
                    status=status,
                    confidence=max(0.0, min(1.0, confidence)),
                    rationale=rationale,
                    citations=citations,
                )
            add_debug_note("OpenRouter response invalid; falling back to open-source verifier.")
        except httpx.HTTPStatusError as exc:
            add_debug_note(f"OpenRouter HTTP error: {exc.response.status_code}.")
        except Exception as exc:
            add_debug_note(f"OpenRouter error: {type(exc).__name__}.")

    # Open-source fallback path.
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


def openrouter_enabled() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())
