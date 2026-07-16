"""Offline drift guard: every artifact model field must have a column in the
Supabase migration. Catches model<->schema drift (e.g. Scorecard.diagnostics
missing from scorecards) BEFORE a live run fails with a PostgREST error. No creds."""
import re
from pathlib import Path

from popper.artifacts import ExperimentResult, Hypothesis, Scorecard, StrategySpec, Verdict

_SQL = (Path(__file__).parent.parent / "supabase" / "migrations" / "0001_init.sql").read_text(encoding="utf-8")


def _cols(table: str) -> set[str]:
    block = _SQL.split(f"create table if not exists {table} (")[1].split(");")[0]
    return set(re.findall(r"^\s*([a-z_]+)\s", block, re.M))


def test_models_have_migration_columns():
    for table, model in [("hypotheses", Hypothesis), ("specs", StrategySpec),
                         ("experiments", ExperimentResult), ("scorecards", Scorecard),
                         ("verdicts", Verdict)]:
        missing = set(model.model_fields) - _cols(table)
        assert not missing, f"{table} table missing columns for model fields: {sorted(missing)}"
