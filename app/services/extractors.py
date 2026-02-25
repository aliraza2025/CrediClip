import re
from dataclasses import dataclass


SCAM_CUES = [
    "limited time",
    "act now",
    "guaranteed",
    "double your money",
    "dm me",
    "wire transfer",
    "crypto giveaway",
    "send payment",
]

CLAIM_HINTS = ["will", "proven", "always", "never", "%", "cures", "guarantees"]


@dataclass
class ExtractedSignals:
    claims: list[str]
    scam_cues: list[str]
    manipulation_cues: list[str]


def split_sentences(text: str) -> list[str]:
    if not text.strip():
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\\s+", text) if s.strip()]


def extract_claims(text: str, top_k: int = 3) -> list[str]:
    sentences = split_sentences(text)
    claims = []
    for sentence in sentences:
        lowered = sentence.lower()
        if any(hint in lowered for hint in CLAIM_HINTS) and len(sentence) >= 20:
            claims.append(sentence)
        if len(claims) >= top_k:
            break
    if not claims:
        # Fallback: still analyze substantive sentences when explicit claim cues are absent.
        claims = [s for s in sentences if len(s) >= 20][:top_k]
    return claims


def extract_scam_cues(text: str) -> list[str]:
    lowered = text.lower()
    return [cue for cue in SCAM_CUES if cue in lowered]


def extract_manipulation_cues(text: str) -> list[str]:
    cues = []
    lowered = text.lower()
    keyword_map = {
        "ai voice": "Possible synthetic voice mention",
        "voice clone": "Possible cloned voice mention",
        "deepfake": "Explicit deepfake mention",
        "face swap": "Potential face manipulation mention",
        "ai generated": "Possible synthetic media mention",
    }
    for keyword, label in keyword_map.items():
        if keyword in lowered:
            cues.append(label)
    return cues


def extract_signals(caption: str, transcript: str) -> ExtractedSignals:
    combined = " ".join([caption.strip(), transcript.strip()]).strip()
    return ExtractedSignals(
        claims=extract_claims(combined),
        scam_cues=extract_scam_cues(combined),
        manipulation_cues=extract_manipulation_cues(combined),
    )
