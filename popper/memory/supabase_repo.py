"""Supabase (Postgres) alpha memory — the production store.

Opt-in: set POPPER_STORE=supabase, POPPER_SUPABASE_URL, POPPER_SUPABASE_KEY, and apply
supabase/migrations/0001_init.sql. Mirrors SqliteMemory exactly; JSON fields map to jsonb,
so values are passed natively (no string-encoding). Requires `pip install supabase`.
"""

from popper.artifacts import (
    ExperimentResult,
    Hypothesis,
    HypothesisStatus,
    Scorecard,
    StrategySpec,
    Verdict,
)
from datetime import datetime, timezone
from uuid import uuid4

from popper.config import settings
from popper.memory.base import AlphaMemory

_PREFIX = {"hypotheses": "H", "specs": "S", "experiments": "E", "scorecards": "SC",
           "verdicts": "V", "research_events": "RE", "feature_gaps": "FG"}
_ID_RETRIES = 15  # recompute-and-retry attempts on the id PK race (many concurrent writers)


class SupabaseMemory(AlphaMemory):
    def __init__(self):
        try:
            from supabase import create_client
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("POPPER_STORE=supabase requires `pip install supabase`") from exc
        if not settings.supabase_url or not settings.supabase_key:
            raise RuntimeError("Set POPPER_SUPABASE_URL and POPPER_SUPABASE_KEY")
        self.client = create_client(settings.supabase_url, settings.supabase_key)

    def _next_id(self, table: str) -> str:
        # Highest existing id + 1, via ORDER BY id DESC LIMIT 1 — NOT select-all-then-max
        # (PostgREST caps a SELECT at 1000 rows, so once a table passes 1000 the scanned
        # max is wrong and every insert collides) and NOT count+1 (ids go non-contiguous
        # after any delete). _insert_row's retry handles the concurrent-writer race on top.
        # ponytail: 4-digit zero-pad => lexical order == numeric up to 9999 rows/table;
        # past that, widen the pad or move to a Postgres identity/sequence.
        res = self.client.table(table).select("id").order("id", desc=True).limit(1).execute().data
        mx = 0
        if res:
            try:
                mx = int(str(res[0]["id"]).rsplit("-", 1)[-1])
            except (ValueError, KeyError, TypeError):
                mx = 0
        return f"{_PREFIX[table]}-{mx + 1:04d}"

    def _insert_row(self, table: str, row: dict) -> str:
        """Insert `row` (no 'id' key) under a random uuid id. Sequential COUNT/MAX
        ids need a read-before-write that both caps at PostgREST's 1000-row SELECT
        AND collides under concurrent writers; a uuid needs neither — it scales to
        any number of parallel workers writing one shared DB with zero coordination.
        ponytail: ids are no longer human-sequential (H-9f2c… not H-0007); nothing
        parses them (FKs are opaque text, the map keys by id), so it's a pure win here."""
        rid = f"{_PREFIX[table]}-{uuid4().hex[:12]}"
        self.client.table(table).insert({**row, "id": rid}).execute()
        return rid

    def _insert(self, table: str, model):
        if model.id is not None:
            self.client.table(table).insert(model.model_dump(mode="json")).execute()
            return model
        row = model.model_dump(mode="json")
        row.pop("id", None)
        return model.model_copy(update={"id": self._insert_row(table, row)})

    def add_hypothesis(self, h: Hypothesis) -> Hypothesis:
        return self._insert("hypotheses", self._enrich(h))

    def add_spec(self, s: StrategySpec) -> StrategySpec:
        return self._insert("specs", s)

    def add_experiment(self, e: ExperimentResult) -> ExperimentResult:
        return self._insert("experiments", e)

    def add_scorecard(self, sc: Scorecard) -> Scorecard:
        return self._insert("scorecards", sc)

    def add_verdict(self, v: Verdict) -> Verdict:
        return self._insert("verdicts", v)

    def set_status(self, hypothesis_id: str, status: HypothesisStatus) -> None:
        self.client.table("hypotheses").update({"status": status.value}).eq("id", hypothesis_id).execute()

    def set_behaviour(self, hypothesis_id: str, signature: dict) -> None:
        self.client.table("hypotheses").update(
            {"behaviour_signature": signature}).eq("id", hypothesis_id).execute()

    def set_embedding(self, hypothesis_id: str, embedding: list[float], tags: dict) -> None:
        self.client.table("hypotheses").update(
            {"embedding": embedding, "tags": tags}).eq("id", hypothesis_id).execute()

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        res = self.client.table("hypotheses").select("*").eq("id", hypothesis_id).execute()
        return Hypothesis(**res.data[0]) if res.data else None

    def list_hypotheses(self, status: HypothesisStatus | None = None) -> list[Hypothesis]:
        # Explicit pagination: PostgREST commonly caps one response at 1000 rows,
        # while novelty must see the complete map to prevent re-exploration.
        rows, start, page = [], 0, 1000
        while True:
            q = self.client.table("hypotheses").select("*")
            if status is not None:
                q = q.eq("status", status.value)
            batch = (q.order("created_at", desc=True)
                     .range(start, start + page - 1).execute().data)
            rows.extend(batch)
            if len(batch) < page:
                break
            start += page
        return [Hypothesis(**r) for r in rows]

    def record(self, hypothesis_id: str) -> dict:
        h = self.get_hypothesis(hypothesis_id)
        if h is None:
            return {}
        specs = self.client.table("specs").select("*").eq("hypothesis_id", hypothesis_id).execute().data
        spec_ids = [s["id"] for s in specs]
        exps = (
            self.client.table("experiments").select("*").in_("spec_id", spec_ids).execute().data
            if spec_ids else []
        )
        exp_ids = [e["id"] for e in exps]
        scs = (
            self.client.table("scorecards").select("*").in_("experiment_id", exp_ids).execute().data
            if exp_ids else []
        )
        verdicts = self.client.table("verdicts").select("*").eq("hypothesis_id", hypothesis_id).execute().data
        return {
            "hypothesis": h.model_dump(mode="json"),
            "specs": [StrategySpec(**row).model_dump(mode="json") for row in specs],
            "experiments": [ExperimentResult(**row).model_dump(mode="json") for row in exps],
            "scorecards": [Scorecard(**row).model_dump(mode="json") for row in scs],
            "verdicts": [Verdict(**row).model_dump(mode="json") for row in verdicts],
        }

    def stats(self) -> dict:
        by_status = {s.value: 0 for s in HypothesisStatus}
        for s in HypothesisStatus:
            res = self.client.table("hypotheses").select("id", count="exact").eq("status", s.value).execute()
            by_status[s.value] = res.count or 0
        counts = {}
        for t in ("hypotheses", "specs", "experiments", "verdicts"):
            counts[t] = self.client.table(t).select("id", count="exact").execute().count or 0
        return {"hypotheses_by_status": by_status, "counts": counts}

    def add_research_event(self, event: dict) -> dict:
        # research_events is append-only, high-volume (one+ per Director step) and its id
        # is never an FK target -> a random id sidesteps the counter entirely: no 1000-row
        # SELECT cap, no read-before-write, no collision under concurrent writers.
        rid = f"RE-{uuid4().hex[:12]}"
        row = {"artifact_ids": {}, "payload": {}, **event, "id": rid}  # created_at = db default
        self.client.table("research_events").insert(row).execute()
        return row

    def list_research_events(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        q = self.client.table("research_events").select("*")
        if run_id:
            q = q.eq("run_id", run_id)
        return q.order("created_at", desc=True).order("id", desc=True).limit(limit).execute().data

    def latest_verdict_types(self) -> dict[str, str]:
        rows, start, page = [], 0, 1000
        while True:
            batch = (self.client.table("verdicts")
                     .select("id,hypothesis_id,type,created_at")
                     .order("created_at", desc=True).order("id", desc=True)
                     .range(start, start + page - 1).execute().data)
            rows.extend(batch)
            if len(batch) < page:
                break
            start += page
        latest = {}
        for row in rows:
            latest.setdefault(row["hypothesis_id"], row["type"])
        return latest

    def recent_verdicts(self, limit: int = 20) -> list[dict]:
        verdicts = (self.client.table("verdicts").select("*")
                    .order("created_at", desc=True).order("id", desc=True)
                    .limit(limit).execute().data)
        if not verdicts:
            return []
        hypothesis_ids = list({row["hypothesis_id"] for row in verdicts})
        experiment_ids = list({row["experiment_id"] for row in verdicts})
        hypotheses = (self.client.table("hypotheses").select("id,claim")
                      .in_("id", hypothesis_ids).execute().data)
        experiments = (self.client.table("experiments").select("id,raw_metrics")
                       .in_("id", experiment_ids).execute().data)
        scorecards = (self.client.table("scorecards")
                      .select("experiment_id,deflated_sharpe")
                      .in_("experiment_id", experiment_ids).execute().data)
        claims = {row["id"]: row["claim"] for row in hypotheses}
        metrics = {row["id"]: row.get("raw_metrics") or {} for row in experiments}
        scores = {row["experiment_id"]: row for row in scorecards}
        return [{
            "hypothesis_id": row["hypothesis_id"],
            "claim": claims.get(row["hypothesis_id"], ""),
            "type": row["type"],
            "reasons": row.get("reasons") or [],
            "created_at": row["created_at"],
            "profit_factor": metrics.get(row["experiment_id"], {}).get("profit_factor"),
            "expectancy_net": metrics.get(row["experiment_id"], {}).get("expectancy_net"),
            "deflated_sharpe": scores.get(row["experiment_id"], {}).get("deflated_sharpe"),
        } for row in verdicts]

    def add_feature_gap(self, name: str, kind: str, source: str, detail: str,
                        example: str | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        found = (self.client.table("feature_gaps").select("*")
                 .eq("name", name).eq("kind", kind).execute().data)
        if found:
            row = found[0]
            examples = row.get("examples") or []
            if example and example not in examples:
                examples = ([example] + examples)[:5]
            self.client.table("feature_gaps").update(
                {"source": source, "detail": detail, "examples": examples,
                 "count": (row.get("count") or 0) + 1, "updated_at": now}
            ).eq("id", row["id"]).execute()
            return self.client.table("feature_gaps").select("*").eq("id", row["id"]).execute().data[0]
        row = {"name": name, "kind": kind, "source": source,
               "detail": detail, "examples": [example] if example else [], "count": 1,
               "status": "open", "updated_at": now}
        rid = self._insert_row("feature_gaps", row)
        return {"id": rid, **row}

    def list_feature_gaps(self, status: str | None = None, limit: int = 50) -> list[dict]:
        q = self.client.table("feature_gaps").select("*")
        if status:
            q = q.eq("status", status)
        return q.order("updated_at", desc=True).limit(limit).execute().data

    def validated_equity_curves(self) -> list[list[float]]:
        out, start, page = [], 0, 1000
        while True:
            batch = (
                self.client.table("experiments").select(
                    "equity_curve, specs!inner(hypothesis_id, hypotheses!inner(status))"
                ).range(start, start + page - 1).execute().data
            )
            for row in batch:
                status = row.get("specs", {}).get("hypotheses", {}).get("status")
                if status in ("survived", "promoted") and row.get("equity_curve"):
                    out.append(row["equity_curve"])
            if len(batch) < page:
                break
            start += page
        return out

    # novelty / is_stale inherited from AlphaMemory (embedding-distance, spec/04)
