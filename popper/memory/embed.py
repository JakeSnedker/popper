"""Embedding layer for the alpha map (spec/04).

Two backends behind one `embed(text) -> list[float]` call:
  - OpenAIEmbedder   — text-embedding-3-small via the REST API (stdlib urllib,
                       no SDK dep). Used when a key is present AND the backend is
                       selected. Keeps embedding compute off the laptop.
  - LocalEmbedder    — deterministic hashed bag-of-words vector. No network,
                       zero cost, reproducible. The offline fallback.

DECISION: default backend is "local" (offline + hermetic tests + no spend). Set
POPPER_EMBED_BACKEND=auto (OpenAI if OPENAI_API_KEY present, else local) or
=openai to activate the API path. get_embedder() reports which is active.
Anthropic has no embeddings endpoint (spec/04) — hence OpenAI, not Claude.
"""

import hashlib
import json
import math
import os
import re
import urllib.request

_DIM = 256  # local embedder dimensionality
_TOKEN = re.compile(r"[a-z0-9_]+")


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 if either is empty or dims mismatch (backend swap)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class LocalEmbedder:
    """Deterministic hashed bag-of-words vector, L2-normalised. Shared tokens ->
    high cosine; identical text -> 1.0. ponytail: bag-of-words has no semantics
    beyond token overlap; swap in OpenAI when paraphrase-robustness matters."""

    name = "local"
    dim = _DIM

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for tok in _TOKEN.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
            vec[h % _DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec


class OpenAIEmbedder:
    """text-embedding-3-small via REST. Raises on network/auth error (caller
    decides whether to fall back)."""

    name = "openai"
    dim = 1536
    _URL = "https://api.openai.com/v1/embeddings"

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.model = model

    def embed(self, text: str) -> list[float]:
        body = json.dumps({"model": self.model, "input": text}).encode()
        req = urllib.request.Request(
            self._URL, data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed host)
            return json.load(resp)["data"][0]["embedding"]


def get_embedder(backend: str | None = None):
    """Select an embedder. backend: "local" | "openai" | "auto" (env
    POPPER_EMBED_BACKEND, default "local"). "auto" picks OpenAI iff a key exists."""
    backend = backend or os.getenv("POPPER_EMBED_BACKEND", "local")
    key = os.getenv("OPENAI_API_KEY", "")
    if backend == "openai" or (backend == "auto" and key):
        if not key:
            raise RuntimeError("POPPER_EMBED_BACKEND=openai but OPENAI_API_KEY is unset")
        return OpenAIEmbedder(key)
    return LocalEmbedder()
