"""SQLite-backed alpha memory. Zero external deps — runs immediately."""

import json
import sqlite3
import threading
from datetime import datetime, timezone

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
    status TEXT, parent_id TEXT, embedding TEXT, tags TEXT,
    behaviour_signature TEXT, created_at TEXT
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
    id TEXT PRIMARY KEY, experiment_id TEXT, robustness REAL, parameter_stability REAL,
    regime_stability REAL, capacity REAL, execution_realism REAL, novelty REAL,
    correlation REAL, deflated_sharpe REAL, pbo REAL, behaviour_signature TEXT,
    diagnostics TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS verdicts (
    id TEXT PRIMARY KEY, hypothesis_id TEXT, experiment_id TEXT, type TEXT,
    reasons TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS feature_gaps (
    id TEXT PRIMARY KEY, name TEXT, kind TEXT, source TEXT, detail TEXT,
    examples TEXT, count INTEGER, status TEXT, created_at TEXT, updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_feature_gaps_name_kind ON feature_gaps(name, kind);
CREATE TABLE IF NOT EXISTS research_events (
    id TEXT PRIMARY KEY, run_id TEXT, iteration INTEGER, engine TEXT, role TEXT,
    stage TEXT, status TEXT, message TEXT, artifact_ids TEXT, payload TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_events_run_created
    ON research_events(run_id, created_at);
"""

# columns whose values are JSON-encoded lists/dicts
_JSON_COLS = {
    "required_features", "embedding", "tags", "behaviour_signature",
    "alpha", "wrapper", "context",
    "param_space", "raw_metrics", "equity_curve", "reasons", "diagnostics", "examples",
    "artifact_ids", "payload",
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
    def __init__(self, path: str, embedder=None):
        self.path = path
        self._embedder = embedder  # None -> lazy get_embedder() via base property
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)
            try:  # migrate pre-Phase-8 DBs (created before the diagnostics column)
                con.execute("ALTER TABLE scorecards ADD COLUMN diagnostics TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists
            try:
                con.execute("ALTER TABLE research_events ADD COLUMN payload TEXT")
            except sqlite3.OperationalError:
                pass  # table/column already exists

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
        return self._insert("hypotheses", self._enrich(h), "H")

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

    def set_behaviour(self, hypothesis_id: str, signature: dict) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE hypotheses SET behaviour_signature = ? WHERE id = ?",
                (json.dumps(signature), hypothesis_id),
            )

    def set_embedding(self, hypothesis_id: str, embedding: list[float], tags: dict) -> None:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE hypotheses SET embedding = ?, tags = ? WHERE id = ?",
                (json.dumps(embedding), json.dumps(tags), hypothesis_id),
            )

    def add_feature_gap(self, name: str, kind: str, source: str, detail: str,
                        example: str | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT * FROM feature_gaps WHERE name = ? AND kind = ?",
                (name, kind),
            ).fetchone()
            if row:
                examples = json.loads(row["examples"] or "[]")
                if example and example not in examples:
                    examples = ([example] + examples)[:5]
                con.execute(
                    "UPDATE feature_gaps SET source = ?, detail = ?, examples = ?, "
                    "count = count + 1, updated_at = ? WHERE id = ?",
                    (source, detail, json.dumps(examples), now, row["id"]),
                )
                row = con.execute("SELECT * FROM feature_gaps WHERE id = ?", (row["id"],)).fetchone()
            else:
                gap_id = self._next_id("feature_gaps", "FG")
                examples = [example] if example else []
                con.execute(
                    "INSERT INTO feature_gaps "
                    "(id, name, kind, source, detail, examples, count, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (gap_id, name, kind, source, detail, json.dumps(examples), 1, "open", now, now),
                )
                row = con.execute("SELECT * FROM feature_gaps WHERE id = ?", (gap_id,)).fetchone()
        return _decode(row)

    def add_research_event(self, event: dict) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as con:
            n = con.execute("SELECT COUNT(*) AS c FROM research_events").fetchone()["c"]
            row = {"id": f"RE-{n + 1:04d}", "created_at": now, **event}
            row.setdefault("artifact_ids", {})
            row.setdefault("payload", {})
            enc = dict(row)
            for col in ("artifact_ids", "payload"):
                enc[col] = json.dumps(enc[col])
            cols = ", ".join(enc.keys())
            placeholders = ", ".join("?" for _ in enc)
            con.execute(f"INSERT INTO research_events ({cols}) VALUES ({placeholders})", list(enc.values()))
        return row

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
                for t in ("hypotheses", "specs", "experiments", "verdicts", "feature_gaps")
            }
        return {"hypotheses_by_status": by_status, "counts": counts}

    def list_feature_gaps(self, status: str | None = None, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM feature_gaps"
        params: list = []
        if status:
            q += " WHERE status = ?"
            params.append(status)
        q += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as con:
            rows = con.execute(q, params).fetchall()
        return [_decode(r) for r in rows]

    def list_research_events(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        q = "SELECT * FROM research_events"
        params: list = []
        if run_id:
            q += " WHERE run_id = ?"
            params.append(run_id)
        q += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as con:
            rows = con.execute(q, params).fetchall()
        return [_decode(r) for r in rows]

    def validated_equity_curves(self) -> list[list[float]]:
        # equity curves of SURVIVED/PROMOTED alphas (the correlation pool).
        with self._connect() as con:
            rows = con.execute(
                "SELECT e.equity_curve FROM experiments e "
                "JOIN specs s ON e.spec_id = s.id "
                "JOIN hypotheses h ON s.hypothesis_id = h.id "
                "WHERE h.status IN ('survived', 'promoted')"
            ).fetchall()
        return [json.loads(r["equity_curve"]) for r in rows if r["equity_curve"]]

    # novelty / is_stale inherited from AlphaMemory (embedding-distance, spec/04)
