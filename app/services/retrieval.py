from dataclasses import dataclass
from typing import Iterable


@dataclass
class EvidenceChunk:
    title: str
    source_url: str
    text: str


# Seed corpus for MVP retrieval. Replace/expand with curated corpora in production.
TRUSTED_CORPUS: list[EvidenceChunk] = [
    EvidenceChunk(
        title="CDC - Preventing Chronic Disease",
        source_url="https://www.cdc.gov/chronicdisease/index.htm",
        text="Chronic diseases are managed through evidence-based prevention and treatment. Claims of instant cures should be treated with skepticism.",
    ),
    EvidenceChunk(
        title="WHO - Health Misinformation",
        source_url="https://www.who.int/news-room/questions-and-answers/item/health-misinformation",
        text="Health misinformation can cause harm. Trustworthy decisions should rely on reputable, verifiable medical sources.",
    ),
    EvidenceChunk(
        title="FTC - Avoiding Scams",
        source_url="https://consumer.ftc.gov/scams",
        text="Scam signals include urgency, guaranteed returns, and requests to move conversations or payments off trusted platforms.",
    ),
    EvidenceChunk(
        title="CFPB - Fraud and Scams",
        source_url="https://www.consumerfinance.gov/consumer-tools/fraud/",
        text="Financial fraud often uses high-pressure tactics and promises of unusually high or guaranteed returns.",
    ),
    EvidenceChunk(
        title="NIST - Digital Media Forensics",
        source_url="https://www.nist.gov/itl/iad/mig/media-forensics",
        text="Synthetic media and deepfakes can be difficult to detect; forensic analysis and provenance checks are important.",
    ),
]


STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "is",
    "are",
    "to",
    "for",
    "of",
    "in",
    "on",
    "with",
    "this",
    "that",
    "it",
    "be",
}


def tokenize(text: str) -> list[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return [t for t in cleaned.split() if t and t not in STOPWORDS]


def overlap_score(query_tokens: Iterable[str], doc_tokens: Iterable[str]) -> float:
    q = set(query_tokens)
    d = set(doc_tokens)
    if not q:
        return 0.0
    return len(q.intersection(d)) / len(q)


def retrieve_evidence(claim: str, top_k: int = 3) -> list[EvidenceChunk]:
    q_tokens = tokenize(claim)
    scored: list[tuple[float, EvidenceChunk]] = []
    for chunk in TRUSTED_CORPUS:
        score = overlap_score(q_tokens, tokenize(chunk.text + " " + chunk.title))
        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    best = [chunk for score, chunk in scored[:top_k] if score > 0]

    # If no lexical match, still return top trusted chunks to keep citations available.
    return best or [chunk for _, chunk in scored[:top_k]]
