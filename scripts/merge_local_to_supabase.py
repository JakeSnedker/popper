"""Conflict-aware import of a local SQLite alpha map into shared Supabase.

An ID match is not treated as identity: independent SQLite stores both start at
H-0001/S-0001/etc. Full normalized rows are compared. Differing rows are preserved
under deterministic import IDs, with every dependent foreign key and event payload
remapped to the preserved local graph. Re-running is idempotent.

    .venv/Scripts/python.exe scripts/merge_local_to_supabase.py --dry-run
    .venv/Scripts/python.exe scripts/merge_local_to_supabase.py
    .venv/Scripts/python.exe scripts/merge_local_to_supabase.py --check
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
import sys

from popper.artifacts import ExperimentResult, Hypothesis, Scorecard, StrategySpec, Verdict
from popper.config import settings
from popper.memory import sqlite_repo  # reuse its _decode (JSON cols -> native)

# FK-dependency order: parents before children (see supabase/migrations/0001_init.sql).
TABLES = ["hypotheses", "specs", "experiments", "scorecards", "verdicts",
          "research_events", "feature_gaps"]
_MODELS = {
    "hypotheses": Hypothesis,
    "specs": StrategySpec,
    "experiments": ExperimentResult,
    "scorecards": Scorecard,
    "verdicts": Verdict,
}
_PREFIX = {"hypotheses": "H", "specs": "S", "experiments": "E",
           "scorecards": "SC", "verdicts": "V", "research_events": "RE",
           "feature_gaps": "FG"}
_DEPENDENCIES = {
    "specs": {"hypothesis_id": "hypotheses"},
    "experiments": {"spec_id": "specs"},
    "scorecards": {"experiment_id": "experiments"},
    "verdicts": {"hypothesis_id": "hypotheses", "experiment_id": "experiments"},
}
_CHUNK = 200
_EXPERIMENT_CHUNK = 10  # Equity curves make experiment rows large enough to hit statement timeout.


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


def _read_cloud(client, table: str) -> list[dict]:
    """Read a complete table, paginated past PostgREST's response cap."""
    page = 20 if table == "experiments" else 1000
    rows, start = [], 0
    while True:
        batch = (client.table(table).select("*").order("id")
                 .range(start, start + page - 1).execute().data)
        rows.extend(batch)
        if len(batch) < page:
            return rows
        start += page


def _normalize(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _canonical(table: str, row: dict) -> str:
    model = _MODELS.get(table)
    normalized = model(**row).model_dump(mode="json") if model else _normalize(row)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=True)


def _comparison_value(table: str, row: dict):
    model = _MODELS.get(table)
    value = model(**row).model_dump(mode="json") if model else _normalize(row)

    def normalize_storage(item, field=None):
        if isinstance(item, float):
            if math.isnan(item):
                return "__POPPER_NAN__"
            if item == 0 or (math.isfinite(item) and item.is_integer()):
                return int(item)
            return item
        if isinstance(item, str) and field in {"created_at", "updated_at"}:
            try:
                return datetime.fromisoformat(item.replace("Z", "+00:00")).isoformat()
            except ValueError:
                return item
        if isinstance(item, dict):
            return {key: normalize_storage(child, key) for key, child in item.items()}
        if isinstance(item, list):
            return [normalize_storage(child, field) for child in item]
        return item

    return normalize_storage(value)


def _same_row(table: str, left: dict, right: dict) -> bool:
    # Postgres normalizes JSON numbers and trims timestamp precision trailing zeros.
    return _comparison_value(table, left) == _comparison_value(table, right)


def _import_id(table: str, source: dict) -> str:
    digest = hashlib.sha256(f"{table}:{_canonical(table, source)}".encode()).hexdigest()[:16]
    return f"{_PREFIX[table]}-import-{digest}"


def _remap_nested(value, remaps: dict[str, str]):
    if isinstance(value, str):
        return remaps.get(value, value)
    if isinstance(value, list):
        return [_remap_nested(item, remaps) for item in value]
    if isinstance(value, dict):
        return {key: _remap_nested(item, remaps) for key, item in value.items()}
    return value


def _plan_table(table: str, local_rows: list[dict], cloud_rows: list[dict],
                id_maps: dict[str, dict[str, str]]) -> tuple[list[dict], dict, list[str]]:
    """Plan inserts and populate old->preserved ID mappings without writing."""
    cloud = {row["id"]: row for row in cloud_rows}
    planned = []
    redundant_imports = []
    stats = {"identical": 0, "new": 0, "rekeyed": 0, "already_reconciled": 0}

    for source in local_rows:
        candidate = deepcopy(source)
        for field, parent_table in _DEPENDENCIES.get(table, {}).items():
            old_parent = candidate.get(field)
            if old_parent:
                candidate[field] = id_maps[parent_table].get(old_parent, old_parent)
        if table == "research_events":
            remaps = {old: new for mapping in id_maps.values()
                      for old, new in mapping.items() if old != new}
            for field in ("artifact_ids", "payload"):
                candidate[field] = _remap_nested(candidate.get(field, {}), remaps)

        source_id = source["id"]
        existing = cloud.get(source_id)
        if existing is None:
            target_id = source_id
            stats["new"] += 1
            planned.append(candidate)
            cloud[target_id] = candidate
        elif _same_row(table, existing, candidate):
            target_id = source_id
            stats["identical"] += 1
            import_id = _import_id(table, source)
            imported = cloud.get(import_id)
            imported_candidate = {**candidate, "id": import_id}
            if imported and _same_row(table, imported, imported_candidate):
                redundant_imports.append(import_id)
        else:
            target_id = _import_id(table, source)
            candidate["id"] = target_id
            imported = cloud.get(target_id)
            if imported is None:
                stats["rekeyed"] += 1
                planned.append(candidate)
                cloud[target_id] = candidate
            elif _same_row(table, imported, candidate):
                stats["already_reconciled"] += 1
            else:  # Cryptographic collision or manually reused deterministic ID.
                raise RuntimeError(f"{table} import ID collision: {target_id}")
        id_maps[table][source_id] = target_id

    return planned, stats, redundant_imports


def main() -> int:
    if not settings.supabase_url or not settings.supabase_key:
        print("set POPPER_SUPABASE_URL and POPPER_SUPABASE_KEY first")
        return 1
    from supabase import create_client

    dry_run = "--dry-run" in sys.argv
    cleanup_events = "--cleanup-redundant-events" in sys.argv
    client = create_client(settings.supabase_url, settings.supabase_key)
    local = _read_local(settings.db_path)
    id_maps = {table: {} for table in TABLES}

    total_planned = 0
    total_cleaned = 0
    for table in TABLES:
        cloud = _read_cloud(client, table)
        planned, stats, redundant = _plan_table(table, local[table], cloud, id_maps)
        if not dry_run:
            chunk_size = _EXPERIMENT_CHUNK if table == "experiments" else _CHUNK
            for chunk in _chunks(planned, chunk_size):
                client.table(table).upsert(chunk, on_conflict="id").execute()
        if cleanup_events and not dry_run and table == "research_events":
            for chunk in _chunks(redundant):
                client.table(table).delete().in_("id", chunk).execute()
            total_cleaned += len(redundant)
        total_planned += len(planned)
        print(f"  {table:16} local={len(local[table]):5} cloud={len(cloud):5} "
              f"identical={stats['identical']:5} new={stats['new']:5} "
              f"rekeyed={stats['rekeyed']:5} reconciled={stats['already_reconciled']:5} "
              f"redundant={len(redundant):5}")

    action = "would preserve" if dry_run else "preserved"
    print(f"MERGE {'DRY RUN' if dry_run else 'OK'}: {action} {total_planned} rows; "
          f"cleaned {total_cleaned} redundant events")
    return 0


def _check() -> int:
    """No-network self-check: decode, chunking, conflicts, remaps, idempotence."""
    assert TABLES[:5] == ["hypotheses", "specs", "experiments", "scorecards", "verdicts"], TABLES
    row = {"id": "H-1", "required_features": '["atr_14"]', "claim": "x", "embedding": "[0.1,0.2]"}
    dec = sqlite_repo._decode(row)
    assert dec["required_features"] == ["atr_14"] and dec["embedding"] == [0.1, 0.2], dec
    assert len(list(_chunks(list(range(450))))) == 3
    assert _same_row("research_events", {"value": 1.0}, {"value": 1})

    local = [{"id": "RE-1", "message": "local", "artifact_ids": {"hypothesis": "H-1"},
              "payload": {}, "created_at": "2026-01-01T00:00:00+00:00"}]
    cloud = [{**local[0], "message": "cloud"}]
    maps = {table: {} for table in TABLES}
    maps["hypotheses"]["H-1"] = "H-import-local"
    planned, stats, redundant = _plan_table("research_events", local, cloud, maps)
    assert stats["rekeyed"] == 1 and planned[0]["id"].startswith("RE-import-") and not redundant
    assert planned[0]["artifact_ids"]["hypothesis"] == "H-import-local"
    planned_again, stats_again, redundant_again = _plan_table(
        "research_events", local, cloud + planned, maps,
    )
    assert not planned_again and stats_again["already_reconciled"] == 1 and not redundant_again
    print("self-check OK")
    return 0


if __name__ == "__main__":
    sys.exit(_check() if "--check" in sys.argv else main())
