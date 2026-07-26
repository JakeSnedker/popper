"""Novelty-map cost and migration safety at scale."""

import pytest

from popper.agents import screen, view
from popper.artifacts import Hypothesis
from popper.memory import EmbeddingSpaceMismatch, LocalEmbedder, LocalSemanticEmbedder
from popper.memory.embed import cosine
from popper.memory.sqlite_repo import SqliteMemory


class CountingEmbedder(LocalEmbedder):
    def __init__(self):
        self.calls = 0

    def embed(self, text):
        self.calls += 1
        return super().embed(text)


def test_gatekeeper_embeds_candidate_once(tmp_path):
    embedder = CountingEmbedder()
    memory = SqliteMemory(str(tmp_path / "map.db"), embedder=embedder)
    memory.add_hypothesis(Hypothesis(claim="existing breakout", mechanism="flow"))
    before = embedder.calls
    result = screen(
        Hypothesis(claim="fresh London liquidity reversal", mechanism="stop exhaustion"),
        view("gatekeeper", memory),
    )
    assert result["accepted"]
    assert result["hypothesis"].embedding
    assert embedder.calls - before == 1


def test_mixed_embedding_dimensions_fail_closed(tmp_path):
    memory = SqliteMemory(str(tmp_path / "map.db"), embedder=LocalEmbedder())
    memory.add_hypothesis(Hypothesis(claim="one", mechanism="m"))
    memory._embedder = LocalSemanticEmbedder()
    with pytest.raises(EmbeddingSpaceMismatch, match="reembed_hypotheses"):
        memory.assess(Hypothesis(claim="two", mechanism="m"))


def test_local_semantic_normalizes_domain_paraphrases():
    embedder = LocalSemanticEmbedder()
    continuation = embedder.embed("London trend continuation persists with directional flow")
    momentum = embedder.embed("London momentum continues with directional flow")
    unrelated = embedder.embed("Asia liquidity sweep fades into mean reversion")
    assert cosine(continuation, momentum) > cosine(continuation, unrelated)
