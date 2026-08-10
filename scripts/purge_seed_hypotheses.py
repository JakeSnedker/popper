"""Remove `popper seed` demo hypotheses from a real alpha memory.

The seed fixtures were written into the production Supabase memory, where they
are indistinguishable from research results:

  H-0001 promoted, H-0002 survived  -> inflate the only number that matters
                                       (validated alphas) with zero gate evidence
  one killed                        -> inflates the kill count
  one testing, one proposed         -> sit in the "untested" backlog forever

They also cannot be honestly re-run: two are NAS100 and this fleet holds only
US30 data. Demo data belongs in a demo store, so this deletes them and their
whole artifact trail, children first, leaving no dangling foreign key.

    python scripts/purge_seed_hypotheses.py            # dry run, changes nothing
    python scripts/purge_seed_hypotheses.py --apply    # delete them
    python scripts/purge_seed_hypotheses.py --apply --id H-0001

Matching is by exact claim text against the claims defined in popper/seed.py, so
it stays correct if the seed set changes and never touches a real hypothesis.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from popper.api.main import _GATE_ORDER
from popper.artifacts import HypothesisStatus
from popper.memory import get_memory

# Children before parents: verdicts/scorecards reference experiments, experiments
# reference specs, specs reference the hypothesis.
_CHILD_TABLES = ("verdicts", "scorecards", "experiments", "specs")
_SEED_FILE = Path(__file__).resolve().parent.parent / "popper" / "seed.py"


def seed_claims() -> set[str]:
    """The demo claims, read from seed.py rather than duplicated here."""
    return set(re.findall(r'claim="([^"]+)"', _SEED_FILE.read_text(encoding="utf-8")))


def _entry(mem, hypothesis) -> dict:
    record = mem.record(hypothesis.id)
    return {
        "id": hypothesis.id,
        "status": hypothesis.status.value,
        "claim": hypothesis.claim,
        "specs": [row["id"] for row in record.get("specs", [])],
        "experiments": [row["id"] for row in record.get("experiments", [])],
        "scorecards": [row["id"] for row in record.get("scorecards", [])],
        "verdicts": [row["id"] for row in record.get("verdicts", [])],
    }


def find_seeded(mem) -> list[dict]:
    claims = seed_claims()
    return [_entry(mem, h) for h in mem.list_hypotheses() if h.claim in claims]


def unauditable_survivors(mem) -> list[str]:
    """Passing hypotheses with no recorded gates — unprovable by definition."""
    passing = [
        *mem.list_hypotheses(HypothesisStatus.PROMOTED),
        *mem.list_hypotheses(HypothesisStatus.SURVIVED),
    ]
    flagged = []
    for hypothesis in passing:
        record = mem.record(hypothesis.id)
        gates = max(
            (sum(key in (row.get("diagnostics") or {}) for key in _GATE_ORDER)
             for row in record.get("scorecards", [])),
            default=0,
        )
        if not gates:
            flagged.append(hypothesis.id)
    return flagged


def purge(mem, entry: dict) -> None:
    client = mem.client
    for table in _CHILD_TABLES:
        if entry[table]:
            client.table(table).delete().in_("id", entry[table]).execute()
    client.table("hypotheses").delete().eq("id", entry["id"]).execute()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="perform the deletion (default: dry run)")
    parser.add_argument("--id", action="append", dest="ids", default=None,
                        help="restrict to these hypothesis ids (repeatable)")
    args = parser.parse_args()

    mem = get_memory()
    entries = find_seeded(mem)
    if args.ids:
        entries = [entry for entry in entries if entry["id"] in set(args.ids)]
    if not entries:
        print("No seed demo hypotheses in this memory.")
    else:
        print(f"{len(entries)} seed demo hypothes{'is' if len(entries) == 1 else 'es'}:\n")
        for entry in entries:
            counts = " ".join(f"{table}={len(entry[table])}" for table in _CHILD_TABLES)
            print(f"  {entry['id']:26s} {entry['status']:9s} {counts}")
            print(f"    {entry['claim'][:95]}")

    if not args.apply:
        print("\nDRY RUN - nothing deleted. Re-run with --apply to delete.")
        return 0

    for entry in entries:
        purge(mem, entry)
        print(f"deleted {entry['id']}")

    remaining = unauditable_survivors(mem)
    print(f"\nPurged {len(entries)}.")
    if remaining:
        print(f"WARNING: {len(remaining)} passing hypotheses still have no gate "
              f"evidence: {', '.join(remaining)}")
    else:
        print("Every remaining passing hypothesis has recorded gate evidence.")
    print("Re-check with: popper stats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
