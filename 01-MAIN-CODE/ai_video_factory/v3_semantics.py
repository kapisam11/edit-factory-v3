"""Optional semantic scoring with a deterministic fallback."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, Optional

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


def semantic_scores(text: str, model_name: Optional[str] = None) -> Dict[str, float]:
    """Use sentence-transformers when enabled; always retain a deterministic fallback."""
    if os.environ.get("AIVF_DISABLE_SEMANTIC", "").lower() in {"1", "true", "yes"}:
        return lexical_scores(text)
    requested = model_name or os.environ.get("AIVF_SEMANTIC_MODEL", "all-MiniLM-L6-v2")
    try:
        model = _load_model(requested)
        vectors = model.encode([str(text), *PROTOTYPES.values()], normalize_embeddings=True)
        target = vectors[0]
        scores = vectors[1:] @ target
        return {emotion: float(score) for emotion, score in zip(EMOTIONS, scores)}
    except Exception:
        return lexical_scores(text)


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
