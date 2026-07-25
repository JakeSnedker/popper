"""One-shot, idempotent push of the local popper.db alpha map up to the shared
Supabase project. Run ONCE after restoring/creating the cloud project; afterwards
runners write to Supabase directly (POPPER_STORE=supabase) so no re-merge is needed.

Reads every table from local sqlite, skips ids that already exist in the cloud,
and inserts only the new rows — in FK-dependency order. Re-running is safe: the
second pass finds everything already present and copies nothing.

    POPPER_SUPABASE_URL=... POPPER_SUPABASE_KEY=...  (from .env)
    .venv/Scripts/python.exe scripts/merge_local_to_supabase.py
    .venv/Scripts/python.exe scripts/merge_local_to_supabase.py --check   # no-network self-check

Requires `pip install supabase`. POPPER_STORE need not be flipped to run this.
"""
import sqlite3
import sys

from popper.config import settings
from popper.memory import sqlite_repo  # reuse its _decode (JSON cols -> native)

# FK-dependency order: parents before children (see supabase/migrations/0001_init.sql).
TABLES = ["hypotheses", "specs", "experiments", "scorecards", "verdicts",
          "research_events", "feature_gaps"]
_CHUNK = 200  # equity_curve/embedding rows can be large — chunk inserts to stay under limits


def _chunks(rows, n=_CHUNK):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def _read_local(path: str) -> dict:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return {t: [sqlite_repo._decode(r) for r in con.execute(f"SELECT * FROM {t}").fetchall()]
                for t in TABLES}
    finally:
        con.close()


def _existing_ids(client, table: str) -> set:
    """All ids already in the cloud table, paginated past PostgREST's 1000-row cap."""
    ids, start, page = set(), 0, 1000
    while True:
        rows = client.table(table).select("id").range(start, start + page - 1).execute().data
        ids.update(r["id"] for r in rows)
        if len(rows) < page:
            return ids
        start += page


def main() -> int:
    if not settings.supabase_url or not settings.supabase_key:
        print("set POPPER_SUPABASE_URL and POPPER_SUPABASE_KEY first")
        return 1
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_key)
    local = _read_local(settings.db_path)

    total_copied = 0
    for table in TABLES:  # dependency order matters for the FK inserts
        rows = local[table]
        have = _existing_ids(client, table)
        new = [r for r in rows if r["id"] not in have]
        for chunk in _chunks(new):
            # on_conflict=id => a concurrent/partial prior run can't duplicate a PK
            client.table(table).upsert(chunk, on_conflict="id").execute()
        total_copied += len(new)
        print(f"  {table:16} copied {len(new):5}  skipped {len(rows) - len(new):5}  "
              f"(cloud now {len(have) + len(new)})")

    print(f"MERGE OK — pushed {total_copied} new rows to {settings.supabase_url}")
    return 0


def _check() -> int:
    """No-network self-check: dependency order + decode + chunker."""
    assert TABLES[:5] == ["hypotheses", "specs", "experiments", "scorecards", "verdicts"], TABLES
    row = {"id": "H-1", "required_features": '["atr_14"]', "claim": "x", "embedding": "[0.1,0.2]"}
    dec = sqlite_repo._decode(row)
    assert dec["required_features"] == ["atr_14"] and dec["embedding"] == [0.1, 0.2], dec
    assert list(_chunks(list(range(450)))) and len(list(_chunks(list(range(450))))) == 3
    print("self-check OK")
    return 0


if __name__ == "__main__":
    sys.exit(_check() if "--check" in sys.argv else main())
