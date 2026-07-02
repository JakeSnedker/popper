"""SQLite-backed alpha memory. Zero external deps — runs immediately."""

import json
import sqlite3
import threading

from popper.artifacts import (
    ExperimentResult,
    Hypothesis,
    HypothesisStatus,
    Scorecard,
    StrategySpec,
    Verdict,
)
from popper.memory.base import AlphaMemory

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hypotheses (
    id TEXT PRIMARY KEY, claim TEXT, mechanism TEXT, rationale TEXT,
    required_features TEXT, expected_regime TEXT, novelty_score REAL,
    status TEXT, parent_id TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS specs (
    id TEXT PRIMARY KEY, hypothesis_id TEXT, dsl_version TEXT, name TEXT,
    alpha TEXT, wrapper TEXT, context TEXT, param_space TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY, spec_id TEXT, dataset TEXT, period_start TEXT,
    period_end TEXT, trades_n INTEGER, raw_metrics TEXT, equity_curve TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS scorecards (
    id TEXT PRIMARY KEY, experiment_id TEXT, robustness REAL, regime_stability REAL,
    capacity REAL, execution_realism REAL, novelty REAL, deflated_sharpe REAL,
    pbo REAL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS verdicts (
    id TEXT PRIMARY KEY, hypothesis_id TEXT, experiment_id TEXT, type TEXT,
    reasons TEXT, created_at TEXT
);
"""

# columns whose values are JSON-encoded lists/dicts
_JSON_COLS = {
    "required_features", "alpha", "wrapper", "context",
    "param_space", "raw_metrics", "equity_curve", "reasons",
}


def _encode(model) -> dict:
    """Pydantic model -> row dict (datetimes as ISO, lists/dicts as JSON strings)."""
    row = model.model_dump(mode="json")
    for col in _JSON_COLS:
        if col in row:
            row[col] = json.dumps(row[col])
    return row


def _decode(row: sqlite3.Row) -> dict:
    d = dict(row)
    for col in _JSON_COLS:
        if col in d and d[col] is not None:
            d[col] = json.loads(d[col])
    return d


class SqliteMemory(AlphaMemory):
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def _next_id(self, table: str, prefix: str) -> str:
        with self._connect() as con:
            n = con.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        return f"{prefix}-{n + 1:04d}"

    def _insert(self, table: str, model, prefix: str):
        if model.id is None:
            model = model.model_copy(update={"id": self._next_id(table, prefix)})
        row = _encode(model)
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        with self._lock, self._connect() as con:
            con.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", list(row.values()))
        return model

    # --- writes ---
    def add_hypothesis(self, h: Hypothesis) -> Hypothesis:
        return self._insert("hypotheses", h, "H")

    def add_spec(self, s: StrategySpec) -> StrategySpec:
        return self._insert("specs", s, "S")

    def add_experiment(self, e: ExperimentResult) -> ExperimentResult:
        return self._insert("experiments", e, "E")

    def add_scorecard(self, sc: Scorecard) -> Scorecard:
        return self._insert("scorecards", sc, "SC")

    def add_verdict(self, v: Verdict) -> Verdict:
        return self._insert("verdicts", v, "V")

    def set_status(self, hypothesis_id: str, status: HypothesisStatus) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE hypotheses SET status = ? WHERE id = ?",
                (status.value, hypothesis_id),
            )

    # --- reads ---
    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,)).fetchone()
        return Hypothesis(**_decode(row)) if row else None

    def list_hypotheses(self, status: HypothesisStatus | None = None) -> list[Hypothesis]:
        q = "SELECT * FROM hypotheses"
        params: tuple = ()
        if status is not None:
            q += " WHERE status = ?"
            params = (status.value,)
        q += " ORDER BY created_at DESC"
        with self._connect() as con:
            rows = con.execute(q, params).fetchall()
        return [Hypothesis(**_decode(r)) for r in rows]

    def record(self, hypothesis_id: str) -> dict:
        h = self.get_hypothesis(hypothesis_id)
        if h is None:
            return {}
        with self._connect() as con:
            specs = [StrategySpec(**_decode(r)) for r in con.execute(
                "SELECT * FROM specs WHERE hypothesis_id = ?", (hypothesis_id,)).fetchall()]
            spec_ids = [s.id for s in specs]
            exps, scs, verdicts = [], [], []
            for sid in spec_ids:
                exps += [ExperimentResult(**_decode(r)) for r in con.execute(
                    "SELECT * FROM experiments WHERE spec_id = ?", (sid,)).fetchall()]
            for e in exps:
                scs += [Scorecard(**_decode(r)) for r in con.execute(
                    "SELECT * FROM scorecards WHERE experiment_id = ?", (e.id,)).fetchall()]
            verdicts = [Verdict(**_decode(r)) for r in con.execute(
                "SELECT * FROM verdicts WHERE hypothesis_id = ?", (hypothesis_id,)).fetchall()]
        return {
            "hypothesis": h.model_dump(mode="json"),
            "specs": [s.model_dump(mode="json") for s in specs],
            "experiments": [e.model_dump(mode="json") for e in exps],
            "scorecards": [sc.model_dump(mode="json") for sc in scs],
            "verdicts": [v.model_dump(mode="json") for v in verdicts],
        }

    def stats(self) -> dict:
        with self._connect() as con:
            by_status = {s.value: 0 for s in HypothesisStatus}
            for r in con.execute("SELECT status, COUNT(*) c FROM hypotheses GROUP BY status"):
                by_status[r["status"]] = r["c"]
            counts = {
                t: con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                for t in ("hypotheses", "specs", "experiments", "verdicts")
            }
        return {"hypotheses_by_status": by_status, "counts": counts}

    def is_stale(self, h: Hypothesis) -> bool:
        # v0: exact claim match. Phase 4 upgrades this to embedding-distance novelty.
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM hypotheses WHERE lower(claim) = lower(?) LIMIT 1", (h.claim,)
            ).fetchone()
        return row is not None
