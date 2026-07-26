"""Rebuild the complete alpha map in one embedding space.

Examples:
  .venv/Scripts/python.exe scripts/reembed_hypotheses.py --backend local_semantic

All vectors are computed before the first write, so failures leave
the existing map untouched. Re-running safely completes interrupted writes.
"""

import argparse

from popper.memory import embed_text, get_memory
from popper.memory.embed import get_embedder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=("local", "local_semantic"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    memory = get_memory()
    embedder = get_embedder(args.backend)
    hypotheses = memory.list_hypotheses()
    prepared = []
    for index, hypothesis in enumerate(hypotheses, 1):
        vector = embedder.embed(embed_text(hypothesis))
        tags = dict(hypothesis.tags)
        tags["embedding_backend"] = embedder.name
        prepared.append((hypothesis.id, vector, tags))
        if index % 100 == 0:
            print(f"embedded {index}/{len(hypotheses)}", flush=True)

    dimensions = {len(vector) for _, vector, _ in prepared}
    if len(dimensions) > 1:
        raise RuntimeError(f"provider returned inconsistent dimensions: {sorted(dimensions)}")
    print(f"prepared {len(prepared)} vectors with {embedder.name}; dimensions={dimensions or {0}}")
    if args.dry_run:
        return 0
    for hypothesis_id, vector, tags in prepared:
        memory.set_embedding(hypothesis_id, vector, tags)
    print("re-embedding complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
