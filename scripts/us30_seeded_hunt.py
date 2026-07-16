"""Finish step 1 HARD: the full self-learning loop on REAL US30 M5, seeded (no LLM,
deterministic, $0). Feeds the 3 real example specs as scripted candidates, runs the
Director end-to-end (explore -> kill -> capture cost-killed near-miss -> exploit
rescue -> persist), then builds the deployable book from the map.

Re-runnable proof the pipeline produces a real book on real data. The book may be
thin/empty (finding a keeper alpha is step 4's job) — completing end-to-end is the win.

    .venv/Scripts/python.exe scripts/us30_seeded_hunt.py
"""
import glob
import json
import os
import tempfile

import yaml

from popper.memory import LocalEmbedder
from popper.memory.sqlite_repo import SqliteMemory
from popper.orchestrator import run_research
from popper.portfolio import book_from_mem

EX = os.path.join(os.path.dirname(__file__), "..", "popper", "dsl", "examples")

# claim text chosen so edge_type() routes each sensibly; distinct so none are stale.
_CLAIMS = {
    "jjc_trendeng_baseline": ("US30 momentum: a strong bar in an established up-trend continues", "herding flow"),
    "ny_open_momentum": ("US30 NY-open expansion drives price with the trend after a wide opening range", "auction imbalance"),
    "sweep_reversal": ("US30 sharp sweeps beyond prior swings revert to fair value", "liquidity absorption"),
}


class ScriptedLLM:
    """Hypothesis calls -> next hyp JSON; Synthesis calls -> next spec YAML."""
    def __init__(self, hyps, specs):
        self.hyps, self.specs, self.models = list(hyps), list(specs), []

    def complete(self, system, prompt, model, max_tokens=1024):
        self.models.append(model)
        return self.hyps.pop(0) if "FALSIFIABLE" in system else self.specs.pop(0)


def _load():
    hyps, specs = [], []
    for path in sorted(glob.glob(os.path.join(EX, "*.yaml"))):
        name = os.path.splitext(os.path.basename(path))[0]
        d = yaml.safe_load(open(path, encoding="utf-8"))
        d.pop("hypothesis_id", None)  # assigned by memory at store time
        feats = d["alpha"]["features"]
        claim, mech = _CLAIMS[name]
        hyps.append(json.dumps({"claim": claim, "mechanism": mech,
                                "required_features": feats, "expected_regime": "unknown"}))
        specs.append(yaml.safe_dump(d, sort_keys=False))
    return hyps, specs


def main():
    mem = SqliteMemory(os.path.join(tempfile.mkdtemp(), "hunt.db"), embedder=LocalEmbedder())
    hyps, specs = _load()
    # iterations > candidates so exploit gets budget to rescue the cost-killed edge
    s = run_research(mem=mem, llm=ScriptedLLM(hyps, specs), period=None, iterations=16)

    print("=" * 70)
    print(f"RUN: proposed={s.proposed} survived={s.survived} killed={s.killed} "
          f"worker_runs={s.worker_runs} skipped={s.skipped}")
    print(f"budget={s.budget_spent}")
    for et, c in s.categories.items():
        print(f"  {et:14s} survivors={len(c.get('survivors',[]))} kills={c.get('kills',0)} "
              f"near_misses={len(c.get('near_misses',[]))} refined={len(c.get('refined_ids',set()))} "
              f"best={c.get('best_verdict')}")

    book = book_from_mem(mem)
    print("=" * 70)
    print(f"BOOK: {book['n_selected']}/{book['n_candidates']} candidates pass the friction hard-gate")
    for e in book["edges"]:
        print(f"  spec {e['spec_id']}  weight={e['weight']}  net_E={e['expectancy_net']}R")
    if book["edges"]:
        print(f"  combined: {book['combined']}")
    print(f"  rejected (first 8): {[(r['spec_id'], r['reason']) for r in book['rejected']][:8]}")
    print("=" * 70)
    print("STEP 1 HARD: full loop ran end-to-end on real US30 and emitted a book.")


if __name__ == "__main__":
    main()
