"""Semantic scoring utilities with deterministic fallbacks."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, Optional, Sequence

EMOTIONS = ("trust", "dramatic", "inspiring", "nostalgic", "funny", "curious")
PROTOTYPES = {
    "trust": "loyalty friendship protection trust support staying together",
    "dramatic": "danger conflict betrayal risk loss consequences tension",
    "inspiring": "effort achievement growth overcoming failure determination",
    "nostalgic": "memory past childhood old times history legacy recognition",
    "funny": "comedy joke absurd awkward failure surprise laughter",
    "curious": "mystery question hidden truth unknown explanation discovery",
}


def lexical_scores(text: str) -> Dict[str, float]:
    source = str(text).lower()
    terms = {
        "trust": ("trust", "loyal", "friend", "stayed", "protected", "believed"),
        "dramatic": ("betray", "risk", "lost", "danger", "destroy", "fight", "secret"),
        "inspiring": ("built", "won", "overcame", "started", "dream", "worked", "changed"),
        "nostalgic": ("remember", "old", "first", "childhood", "used to", "back then", "legacy"),
        "funny": ("funny", "joke", "fail", "awkward", "ridiculous", "laugh", "chaos"),
        "curious": ("why", "how", "hidden", "unknown", "mystery", "truth", "nobody knew"),
    }
    return {emotion: float(sum(source.count(term) for term in values)) for emotion, values in terms.items()}


@lru_cache(maxsize=2)
def _load_model(model_name: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name)


def _semantic_vector_scores(text: str, prototypes: Sequence[str], model_name: Optional[str] = None) -> list[float]:
    requested = model_name or os.environ.get("AIVF_SEMANTIC_MODEL", "all-MiniLM-L6-v2")
    model = _load_model(requested)
    vectors = model.encode([str(text), *prototypes], normalize_embeddings=True)
    target = vectors[0]
    return [float(value) for value in (vectors[1:] @ target)]


def semantic_scores(text: str, model_name: Optional[str] = None) -> Dict[str, float]:
    """Use sentence-transformers when available, otherwise deterministic lexical scoring."""
    if os.environ.get("AIVF_DISABLE_SEMANTIC", "").lower() in {"1", "true", "yes"}:
        return lexical_scores(text)
    try:
        scores = _semantic_vector_scores(text, list(PROTOTYPES.values()), model_name)
        return {emotion: score for emotion, score in zip(EMOTIONS, scores)}
    except Exception:
        return lexical_scores(text)


def semantic_similarity(query: str, document: str, model_name: Optional[str] = None) -> float:
    """Return cosine similarity when the optional semantic model exists, else lexical recall."""
    if not str(query).strip() or not str(document).strip():
        return 0.0
    if os.environ.get("AIVF_DISABLE_SEMANTIC", "").lower() in {"1", "true", "yes"}:
        return lexical_scores(f"{query} {document}").get("curious", 0.0)
    try:
        score = _semantic_vector_scores(str(query), [str(document)], model_name)
        return max(0.0, min(1.0, (score[0] + 1.0) / 2.0))
    except Exception:
        q = set(str(query).lower().split())
        d = set(str(document).lower().split())
        return len(q & d) / max(1, len(q))


def combined_scores(topic: str, context: str = "", *, semantic_weight: float = 0.65) -> Dict[str, float]:
    text = f"{topic} {context}".strip()
    semantic = semantic_scores(text)
    lexical = lexical_scores(text)
    max_lex = max(1.0, max(lexical.values(), default=0.0))
    normalized_lex = {key: value / max_lex for key, value in lexical.items()}
    return {
        emotion: semantic_weight * semantic.get(emotion, 0.0)
        + (1.0 - semantic_weight) * normalized_lex.get(emotion, 0.0)
        for emotion in EMOTIONS
    }
