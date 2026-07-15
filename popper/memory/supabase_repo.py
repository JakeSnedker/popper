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

from popper.config import settings
from popper.memory.base import AlphaMemory

_PREFIX = {"hypotheses": "H", "specs": "S", "experiments": "E", "scorecards": "SC",
           "verdicts": "V", "research_events": "RE", "feature_gaps": "FG"}


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
        # ponytail: COUNT+1 ids race under concurrent writers — with 3 machines on one
        # shared DB, two simultaneous inserts can pick the same id and the second fails
        # the text PK (loud, non-corrupting: the Director drops that candidate + reports).
        # LOW severity at periodic-cron scale. Upgrade path if throughput bites: a Postgres
        # identity/sequence per table, or namespace ids by instrument (US30-/NAS-/DAX-).
        res = self.client.table(table).select("id", count="exact").execute()
        return f"{_PREFIX[table]}-{(res.count or 0) + 1:04d}"

    def _insert(self, table: str, model):
        if model.id is None:
            model = model.model_copy(update={"id": self._next_id(table)})
        self.client.table(table).insert(model.model_dump(mode="json")).execute()
        return model

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

    def get_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        res = self.client.table("hypotheses").select("*").eq("id", hypothesis_id).execute()
        return Hypothesis(**res.data[0]) if res.data else None

    def list_hypotheses(self, status: HypothesisStatus | None = None) -> list[Hypothesis]:
        q = self.client.table("hypotheses").select("*")
        if status is not None:
            q = q.eq("status", status.value)
        res = q.order("created_at", desc=True).execute()
        return [Hypothesis(**r) for r in res.data]

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
        return {"hypothesis": h.model_dump(mode="json"), "specs": specs,
                "experiments": exps, "scorecards": scs, "verdicts": verdicts}

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
        row = {"id": self._next_id("research_events"), "artifact_ids": {}, "payload": {}, **event}
        self.client.table("research_events").insert(row).execute()  # created_at = db default
        return row

    def list_research_events(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        q = self.client.table("research_events").select("*")
        if run_id:
            q = q.eq("run_id", run_id)
        return q.order("created_at", desc=True).order("id", desc=True).limit(limit).execute().data

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
        row = {"id": self._next_id("feature_gaps"), "name": name, "kind": kind, "source": source,
               "detail": detail, "examples": [example] if example else [], "count": 1,
               "status": "open", "updated_at": now}
        self.client.table("feature_gaps").insert(row).execute()
        return row

    def list_feature_gaps(self, status: str | None = None, limit: int = 50) -> list[dict]:
        q = self.client.table("feature_gaps").select("*")
        if status:
            q = q.eq("status", status)
        return q.order("updated_at", desc=True).limit(limit).execute().data

    def validated_equity_curves(self) -> list[list[float]]:
        res = (
            self.client.table("experiments").select("equity_curve, specs!inner(hypothesis_id, "
            "hypotheses!inner(status))").execute()
        )
        out = []
        for r in res.data:
            status = r.get("specs", {}).get("hypotheses", {}).get("status")
            if status in ("survived", "promoted") and r.get("equity_curve"):
                out.append(r["equity_curve"])
        return out

    # novelty / is_stale inherited from AlphaMemory (embedding-distance, spec/04)
