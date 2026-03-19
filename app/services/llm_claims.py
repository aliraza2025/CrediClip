from __future__ import annotations

import json
import os
import asyncio

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


def _llm_mode() -> str:
    return (os.getenv("CLAIM_LLM_MODE", "auto").strip().lower() or "auto")


def _ollama_enabled() -> bool:
    return bool(os.getenv("OLLAMA_MODEL", "").strip())


def llm_provider_label() -> str:
    mode = _llm_mode()
    if mode == "none":
        return "none"
    if mode == "openai":
        return "openai" if bool(os.getenv("OPENAI_API_KEY", "").strip()) else "none"
    if mode == "groq":
        return "groq" if bool(os.getenv("GROQ_API_KEY", "").strip()) else "none"
    if mode in {"ollama", "local"}:
        return "local_ollama" if _ollama_enabled() else "none"
    if mode == "openrouter":
        return "openrouter" if bool(os.getenv("OPENROUTER_API_KEY", "").strip()) else "none"
    if _ollama_enabled():
        return "local_ollama"
    if bool(os.getenv("GROQ_API_KEY", "").strip()):
        return "groq"
    if bool(os.getenv("OPENAI_API_KEY", "").strip()):
        return "openai"
    if bool(os.getenv("OPENROUTER_API_KEY", "").strip()):
        return "openrouter"
    return "none"


def _extract_first_json_object(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Plain JSON first.
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # Extract first {...} block as fallback.
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        fragment = raw[start : end + 1]
        try:
            parsed = json.loads(fragment)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


async def _assess_claim_with_ollama(claim: str, evidence: list[EvidenceChunk]) -> ClaimAssessment | None:
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if not model:
        return None

    base = (os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434").rstrip("/")
    timeout = float((os.getenv("OLLAMA_TIMEOUT_SEC", "90").strip() or "90"))
    num_ctx = int((os.getenv("OLLAMA_NUM_CTX", "8192").strip() or "8192"))
    num_predict = int((os.getenv("OLLAMA_NUM_PREDICT", "220").strip() or "220"))
    evidence_block = "\n".join(
        f"[{idx + 1}] {e.title} | {e.source_url}\n{e.text}"
        for idx, e in enumerate(evidence)
    )
    sys_prompt = (
        "You are a strict fact-checking classifier. "
        "Use ONLY the provided evidence snippets. "
        "Return a single JSON object with keys: status, confidence, rationale, citations. "
        "status must be exactly one of supported, refuted, not_enough_evidence. "
        "confidence must be a float from 0 to 1. "
        "rationale must be one short sentence. "
        "citations must be a JSON array of source URLs copied exactly from the evidence block. "
        "If evidence is weak, unrelated, or incomplete, return not_enough_evidence."
    )
    user_prompt = (
        "Classify this claim.\n\n"
        f"Claim:\n{claim}\n\n"
        f"Evidence:\n{evidence_block}\n\n"
        "Return JSON only. Do not add markdown, prose, or extra keys."
    )
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": 0,
            "num_ctx": max(2048, num_ctx),
            "num_predict": max(80, num_predict),
        },
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        add_debug_note(f"Ollama HTTP error ({model}): {exc.response.status_code}.")
        return None
    except Exception as exc:
        add_debug_note(f"Ollama error ({model}): {type(exc).__name__}.")
        return None

    content = ""
    message = data.get("message") if isinstance(data, dict) else None
    if isinstance(message, dict):
        content = str(message.get("content", "")).strip()
    parsed = _extract_first_json_object(content)
    if not parsed:
        add_debug_note(f"Ollama invalid JSON response ({model}).")
        return None

    status = parsed.get("status")
    if status not in {"supported", "refuted", "not_enough_evidence"}:
        add_debug_note(f"Ollama invalid status ({model}).")
        return None
    confidence = float(parsed.get("confidence", 0.5))
    rationale = str(parsed.get("rationale", "Ollama result missing rationale.")).strip()
    citations = parsed.get("citations") or []
    allowed_urls = {e.source_url for e in evidence}
    citations = [c for c in citations if isinstance(c, str) and c in allowed_urls]

    add_debug_note(f"Claim verification used local Ollama LLM ({model}).")
    return ClaimAssessment(
        claim=claim,
        status=status,
        confidence=max(0.0, min(1.0, confidence)),
        rationale=rationale,
        citations=citations,
    )


async def _assess_claim_with_groq(claim: str, evidence: list[EvidenceChunk]) -> ClaimAssessment | None:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None

    model = (os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-20b")
    base = (os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip() or "https://api.groq.com/openai/v1").rstrip("/")
    timeout = float((os.getenv("GROQ_TIMEOUT_SEC", "45").strip() or "45"))
    evidence_block = "\n".join(
        f"[{idx + 1}] {e.title} | {e.source_url}\n{e.text}"
        for idx, e in enumerate(evidence)
    )
    sys_prompt = (
        "You are a strict fact-checking classifier. "
        "Use ONLY the provided evidence snippets. "
        "Return a single JSON object with keys: status, confidence, rationale, citations. "
        "status must be exactly one of supported, refuted, not_enough_evidence. "
        "confidence must be a float from 0 to 1. "
        "rationale must be one short sentence. "
        "citations must be a JSON array of source URLs copied exactly from the evidence block. "
        "If evidence is weak, unrelated, or incomplete, return not_enough_evidence."
    )
    user_prompt = (
        "Classify this claim.\n\n"
        f"Claim:\n{claim}\n\n"
        f"Evidence:\n{evidence_block}\n\n"
        "Return JSON only. Do not add markdown, prose, or extra keys."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "claim_assessment",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["supported", "refuted", "not_enough_evidence"],
                        },
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["status", "confidence", "rationale", "citations"],
                },
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        add_debug_note(f"Groq HTTP error ({model}): {exc.response.status_code}.")
        return None
    except Exception as exc:
        add_debug_note(f"Groq error ({model}): {type(exc).__name__}.")
        return None

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        add_debug_note(f"Groq invalid response envelope ({model}).")
        return None
    parsed = _extract_first_json_object(str(content))
    if not parsed:
        add_debug_note(f"Groq invalid JSON response ({model}).")
        return None

    status = parsed.get("status")
    if status not in {"supported", "refuted", "not_enough_evidence"}:
        add_debug_note(f"Groq invalid status ({model}).")
        return None
    confidence = float(parsed.get("confidence", 0.5))
    rationale = str(parsed.get("rationale", "Groq result missing rationale.")).strip()
    citations = parsed.get("citations") or []
    allowed_urls = {e.source_url for e in evidence}
    citations = [c for c in citations if isinstance(c, str) and c in allowed_urls]

    add_debug_note(f"Claim verification used Groq LLM ({model}).")
    return ClaimAssessment(
        claim=claim,
        status=status,
        confidence=max(0.0, min(1.0, confidence)),
        rationale=rationale,
        citations=citations,
    )


async def _assess_claim_with_openai(claim: str, evidence: list[EvidenceChunk]) -> ClaimAssessment | None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    model = (os.getenv("OPENAI_MODEL", "").strip() or "gpt-5-mini")
    base = (os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip() or "https://api.openai.com/v1").rstrip("/")
    timeout = float((os.getenv("OPENAI_TIMEOUT_SEC", "45").strip() or "45"))
    evidence_block = "\n".join(
        f"[{idx + 1}] {e.title} | {e.source_url}\n{e.text}"
        for idx, e in enumerate(evidence)
    )
    sys_prompt = (
        "You are a strict fact-checking classifier. "
        "Use ONLY the provided evidence snippets. "
        "Return a single JSON object with keys: status, confidence, rationale, citations. "
        "status must be exactly one of supported, refuted, not_enough_evidence. "
        "confidence must be a float from 0 to 1. "
        "rationale must be one short sentence. "
        "citations must be a JSON array of source URLs copied exactly from the evidence block. "
        "If evidence is weak, unrelated, or incomplete, return not_enough_evidence."
    )
    user_prompt = (
        "Classify this claim.\n\n"
        f"Claim:\n{claim}\n\n"
        f"Evidence:\n{evidence_block}\n\n"
        "Return JSON only. Do not add markdown, prose, or extra keys."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "claim_assessment",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "status": {
                            "type": "string",
                            "enum": ["supported", "refuted", "not_enough_evidence"],
                        },
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["status", "confidence", "rationale", "citations"],
                },
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    max_attempts = max(1, int((os.getenv("OPENAI_MAX_RETRIES", "2").strip() or "2")) + 1)
    retryable_codes = {408, 409, 429, 500, 502, 503, 504}
    data = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, max_attempts + 1):
            try:
                resp = await client.post(f"{base}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                break
            except httpx.HTTPStatusError as exc:
                detail = (exc.response.text or "").strip().replace("\n", " ")
                if detail:
                    detail = detail[:220]
                    add_debug_note(f"OpenAI HTTP error ({model}): {exc.response.status_code} {detail}.")
                else:
                    add_debug_note(f"OpenAI HTTP error ({model}): {exc.response.status_code}.")
                if exc.response.status_code not in retryable_codes or attempt >= max_attempts:
                    return None
                await asyncio.sleep(min(3.0, 0.75 * attempt))
            except Exception as exc:
                add_debug_note(f"OpenAI error ({model}): {type(exc).__name__}.")
                if attempt >= max_attempts:
                    return None
                await asyncio.sleep(min(3.0, 0.75 * attempt))
    if data is None:
        return None

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        add_debug_note(f"OpenAI invalid response envelope ({model}).")
        return None
    parsed = _extract_first_json_object(str(content))
    if not parsed:
        add_debug_note(f"OpenAI invalid JSON response ({model}).")
        return None

    status = parsed.get("status")
    if status not in {"supported", "refuted", "not_enough_evidence"}:
        add_debug_note(f"OpenAI invalid status ({model}).")
        return None
    confidence = float(parsed.get("confidence", 0.5))
    rationale = str(parsed.get("rationale", "OpenAI result missing rationale.")).strip()
    citations = parsed.get("citations") or []
    allowed_urls = {e.source_url for e in evidence}
    citations = [c for c in citations if isinstance(c, str) and c in allowed_urls]
    if not citations:
        citations = [e.source_url for e in evidence[:1] if e.source_url]

    add_debug_note(f"Claim verification used OpenAI LLM ({model}).")
    return ClaimAssessment(
        claim=claim,
        status=status,
        confidence=max(0.0, min(1.0, confidence)),
        rationale=rationale,
        citations=citations,
    )


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


def _has_ai_self_declaration(claim: str) -> bool:
    lowered = claim.lower()
    patterns = [
        "#ai",
        "#aigenerated",
        "ai generated",
        "generated with ai",
        "made with ai",
        "this is ai",
        "ai video",
        "ai clip",
    ]
    return any(p in lowered for p in patterns)


def _openrouter_candidate_models() -> list[str]:
    """Build ordered OpenRouter model candidates from env vars.

    Priority:
    1) OPENROUTER_MODEL (single model or comma-separated list)
    2) OPENROUTER_MODEL_FALLBACKS (comma-separated list)
    3) Built-in free-model fallback list
    """
    candidates: list[str] = []
    primary_raw = os.getenv("OPENROUTER_MODEL", "").strip()
    fallback_raw = os.getenv("OPENROUTER_MODEL_FALLBACKS", "").strip()

    for raw in [primary_raw, fallback_raw]:
        if not raw:
            continue
        for part in raw.split(","):
            model = part.strip()
            if model and model not in candidates:
                candidates.append(model)

    defaults = [
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen3-4b:free",
    ]
    for model in defaults:
        if model not in candidates:
            candidates.append(model)

    return candidates


async def assess_claim_with_llm(claim: str, evidence: list[EvidenceChunk]) -> ClaimAssessment | None:
    """Open-source verifier with optional OpenRouter LLM path.

    Keeps function signature to avoid wider refactors.
    """
    mode = _llm_mode()

    # Optional local LLM path (Ollama on VM).
    if mode in {"auto", "ollama", "local"}:
        local_result = await _assess_claim_with_ollama(claim, evidence)
        if local_result is not None:
            return local_result

    # Optional Groq path.
    if mode in {"auto", "groq"} and bool(os.getenv("GROQ_API_KEY", "").strip()):
        groq_result = await _assess_claim_with_groq(claim, evidence)
        if groq_result is not None:
            return groq_result

    # Optional OpenAI path.
    if mode in {"auto", "openai"} and bool(os.getenv("OPENAI_API_KEY", "").strip()):
        openai_result = await _assess_claim_with_openai(claim, evidence)
        if openai_result is not None:
            return openai_result

    # Optional OpenRouter path.
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if mode in {"auto", "openrouter"} and openrouter_key:
        evidence_block = "\n".join(
            f"[{idx + 1}] {e.title} | {e.source_url}\n{e.text}"
            for idx, e in enumerate(evidence)
        )
        sys_prompt = (
            "Classify the claim using ONLY provided evidence. "
            "Return strict JSON with keys: status, confidence, rationale, citations. "
            "status in [supported, refuted, not_enough_evidence]. "
            "confidence is 0..1 float. citations are source URLs from evidence. "
            "If claim text self-declares AI generation (e.g., #ai, ai-generated) and evidence is not contradictory, "
            "prefer supported over not_enough_evidence."
        )
        user_prompt = f"Claim:\n{claim}\n\nEvidence:\n{evidence_block}"
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

        for openrouter_model in _openrouter_candidate_models():
            payload = {
                "model": openrouter_model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            }

            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers=headers,
                        json=payload,
                    )
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
                    add_debug_note(f"Claim verification used OpenRouter LLM ({openrouter_model}).")
                    return ClaimAssessment(
                        claim=claim,
                        status=status,
                        confidence=max(0.0, min(1.0, confidence)),
                        rationale=rationale,
                        citations=citations,
                    )
                add_debug_note(f"OpenRouter invalid response ({openrouter_model}); trying fallback model.")
            except httpx.HTTPStatusError as exc:
                add_debug_note(f"OpenRouter HTTP error ({openrouter_model}): {exc.response.status_code}.")
            except Exception as exc:
                add_debug_note(f"OpenRouter error ({openrouter_model}): {type(exc).__name__}.")

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

    if _has_ai_self_declaration(claim):
        add_debug_note("Open-source verifier: AI self-declaration detected in claim text.")
        return ClaimAssessment(
            claim=claim,
            status="supported",
            confidence=0.78,
            rationale="Claim includes explicit AI-generation self-declaration in source text.",
            citations=urls,
        )

    if support_score >= 0.45:
        add_debug_note("Open-source verifier: lexical evidence support high.")
        return ClaimAssessment(
            claim=claim,
            status="supported",
            confidence=min(0.85, 0.52 + support_score * 0.42),
            rationale="Claim has strong lexical overlap with retrieved trusted evidence.",
            citations=urls,
        )

    if support_score >= 0.22:
        add_debug_note("Open-source verifier: lexical overlap medium; uncertain verdict.")
        return ClaimAssessment(
            claim=claim,
            status="not_enough_evidence",
            confidence=0.46,
            rationale="Evidence is partially related but insufficient for confident support/refutation.",
            citations=urls,
        )

    add_debug_note("Open-source verifier: low overlap; uncertain verdict.")
    return ClaimAssessment(
        claim=claim,
        status="not_enough_evidence",
        confidence=0.28,
        rationale="No sufficiently relevant trusted evidence was found for this claim.",
        citations=[],
    )


def openrouter_enabled() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())
