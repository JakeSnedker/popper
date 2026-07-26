"""Zero-cost CPU embeddings for novelty and stale-hypothesis detection.

These are small deterministic feature hashes, not local LLMs and do not use a GPU.
The legacy space remains available for existing maps; `local_semantic` adds
market-domain normalization. Switching dimensions is explicit via the migration tool.
"""

import hashlib
import math
import os
import re

_DIM = 256
_SEMANTIC_DIM = 384
_TOKEN = re.compile(r"[a-z0-9_]+")
_SYNONYMS = {
    "continue": "momentum", "continuation": "momentum", "persist": "momentum",
    "drift": "momentum", "trend": "momentum", "drive": "momentum",
    "revert": "reversion", "reversal": "reversion", "fade": "reversion",
    "snapback": "reversion", "mean": "reversion",
    "break": "breakout", "expansion": "breakout", "escape": "breakout",
    "stoprun": "liquidity_sweep", "sweep": "liquidity_sweep",
    "absorption": "liquidity_sweep",
    "ny": "session_ny", "newyork": "session_ny",
    "london": "session_london", "asia": "session_asia",
}


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _hashed(tokens: list[str], dim: int) -> list[float]:
    vec = [0.0] * dim
    for token in tokens:
        value = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
        vec[value % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class LocalEmbedder:
    """Legacy deterministic bag-of-words space; no network or GPU."""

    name = "local-v1"
    dim = _DIM

    def embed(self, text: str) -> list[float]:
        return _hashed(_TOKEN.findall(text.lower()), self.dim)


class LocalSemanticEmbedder:
    """Domain-normalized unigrams+bigrams; still a tiny CPU-only hash."""

    name = "local-semantic-v2"
    dim = _SEMANTIC_DIM

    def embed(self, text: str) -> list[float]:
        raw = _TOKEN.findall(text.lower().replace("new york", "newyork"))
        words = [_SYNONYMS.get(token, token) for token in raw]
        bigrams = [f"{a}::{b}" for a, b in zip(words, words[1:])]
        return _hashed(words + bigrams, self.dim)


def get_embedder(backend: str | None = None):
    backend = (backend or os.getenv("POPPER_EMBED_BACKEND", "local")).lower()
    if backend == "local":
        return LocalEmbedder()
    if backend in {"local_semantic", "semantic"}:
        return LocalSemanticEmbedder()
    raise ValueError("POPPER_EMBED_BACKEND must be local or local_semantic")
