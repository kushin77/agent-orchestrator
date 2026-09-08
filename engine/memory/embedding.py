"""engine/memory - offline semantic embeddings + cosine similarity.

Issue kushin77/agent-orchestrator#25. The issue asks for a "vector store w/
semantic retrieval". Everything here must run fully OFFLINE with only the
stdlib, so this module provides the *embedding seam* and a deterministic
default implementation:

``Embedder``            the seam a real model-backed embedder can implement.
``BagOfWordsEmbedder``  deterministic feature-hashed term-vector embedder
                        (pure function of the text - same bytes in, same
                        vector out, across processes and platforms).
``cosine_similarity``   the scoring function used for ranking + dedup.

A production deployment can inject a model-backed embedder behind the same
``Embedder`` protocol (harvested ``vscode-memory`` embedding_service /
qdrant_client shape); the store and retriever never depend on *which*
embedder is used, only on the protocol.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List, Protocol

_WORD_RE = re.compile(r"[a-z0-9]+")

# A small, fixed stop list. Kept tiny on purpose: the hashing trick makes the
# occasional stopword harmless, but removing the noisiest function words keeps
# vectors discriminative without needing a large downloaded list (offline).
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on",
        "for", "with", "as", "at", "by", "is", "are", "was", "were", "be",
        "been", "it", "its", "this", "that", "these", "those", "i", "you",
        "we", "they", "he", "she", "do", "does", "did", "have", "has", "had",
        "not", "no", "so", "then", "there", "here",
    }
)


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens with digits, stopwords removed."""
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]


class Embedder(Protocol):
    """Anything that turns text into a fixed-length float vector."""

    def embed(self, text: str) -> List[float]:  # pragma: no cover - protocol
        ...


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity in [0, 1]; 0 for empty/zero vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class BagOfWordsEmbedder:
    """Deterministic offline embedder using signed feature hashing.

    Each term maps to a fixed (index, sign) via MD5 of ``bow:<term>``; the
    vector accumulates ``sign * sqrt(term_frequency)`` and is L2-normalized.
    This is the standard hashing-trick bag-of-words model - no model weights,
    no downloads, byte-stable across runs - which gives cosine similarity real
    ranking signal for the offline semantic-retrieval and dedup tests.
    """

    def __init__(self, dimensions: int = 256,
                 stopwords: frozenset = _STOPWORDS) -> None:
        if dimensions < 16:
            raise ValueError("dimensions must be >= 16")
        self.dimensions = dimensions
        self.stopwords = frozenset(stopwords)

    def embed(self, text: str) -> List[float]:
        """Return the L2-normalized feature vector for ``text``."""
        counts: dict = {}
        for word in _WORD_RE.findall(text.lower()):
            if word in self.stopwords:
                continue
            counts[word] = counts.get(word, 0) + 1

        vector = [0.0] * self.dimensions
        for word, count in counts.items():
            digest = hashlib.md5(("bow:" + word).encode("utf-8")).digest()
            index = int.from_bytes(digest[:2], "big") % self.dimensions
            sign = 1.0 if digest[2] & 1 else -1.0
            vector[index] += sign * math.sqrt(count)

        norm = math.sqrt(sum(x * x for x in vector))
        if norm == 0.0:
            return vector
        return [x / norm for x in vector]

    def embed_text(self, text: str) -> List[float]:
        """Alias matching the harvested ``embedding_service.embed_text`` name."""
        return self.embed(text)
