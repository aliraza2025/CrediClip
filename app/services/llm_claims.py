import json
import os
from typing import Any

import httpx

from app.models import ClaimAssessment
from app.services.retrieval import EvidenceChunk


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        snippet = text[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None
    return None


async def assess_claim_with_llm(claim: str, evidence: list[EvidenceChunk]) -> ClaimAssessment | None:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        return None

    evidence_block = "\n".join(
        f"[{idx + 1}] {e.title} | {e.source_url}\n{e.text}"
        for idx, e in enumerate(evidence)
    )

    system_prompt = (
        "You are a factual claim-checking assistant. Use only provided evidence. "
        "Return strict JSON with keys: status, confidence, rationale, citations. "
        "status must be one of supported, refuted, not_enough_evidence. "
        "confidence must be 0-1 float. citations must be list of source URLs from evidence."
    )

    user_prompt = (
        f"Claim:\n{claim}\n\n"
        f"Evidence:\n{evidence_block}\n\n"
        "If evidence is weak/indirect, choose not_enough_evidence."
    )

    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
    except Exception:
        return None

    parsed = _extract_json(content)
    if not parsed:
        return None

    status = parsed.get("status")
    confidence = parsed.get("confidence")
    rationale = parsed.get("rationale")
    citations = parsed.get("citations") or []

    if status not in {"supported", "refuted", "not_enough_evidence"}:
        return None
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    if not isinstance(rationale, str) or not rationale.strip():
        rationale = "LLM returned an incomplete rationale."
    if not isinstance(citations, list):
        citations = []

    # Keep only known evidence URLs to enforce provenance.
    allowed_urls = {e.source_url for e in evidence}
    filtered_citations = [c for c in citations if isinstance(c, str) and c in allowed_urls]
    if not filtered_citations:
        filtered_citations = [evidence[0].source_url] if evidence else []

    return ClaimAssessment(
        claim=claim,
        status=status,
        confidence=max(0.0, min(1.0, float(confidence))),
        rationale=rationale.strip(),
        citations=filtered_citations,
    )
