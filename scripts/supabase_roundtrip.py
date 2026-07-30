"""Step-2 gate: prove the shared Supabase map with one real round-trip.

Run this ONCE, on any machine that will write to the shared DB, before pointing
the real hunt at Supabase. It verifies the previously-broken pieces
(add_research_event / add_feature_gap) plus core CRUD against live Postgres.

    1. Apply migrations 0001, 0002 and 0003; add this Auth user to team_members.
    2. Set env (or .env):  POPPER_STORE=supabase
                           POPPER_SUPABASE_URL=https://xxxx.supabase.co
                           POPPER_SUPABASE_KEY=<project anon key>
                           POPPER_SUPABASE_EMAIL=<operator Auth email>
                           POPPER_SUPABASE_PASSWORD=<operator password>
                           POPPER_AUTH_REQUIRED=true
    3. .venv/Scripts/python.exe scripts/supabase_roundtrip.py

Exit 0 + "ROUND-TRIP OK" = the shared map works; point runs at it. Any traceback
= creds/migration/method problem, printed plainly.
"""
import sys

from popper.artifacts import Hypothesis
from popper.config import settings
from popper.memory.supabase_repo import SupabaseMemory

if settings.store != "supabase" or not settings.supabase_url or not settings.supabase_key:
    sys.exit("set POPPER_STORE=supabase, POPPER_SUPABASE_URL, POPPER_SUPABASE_KEY first")

mem = SupabaseMemory()
run_id = "RT-smoke"

# 1. research_event (was missing -> crashed every real run)
mem.add_research_event({"run_id": run_id, "iteration": 1, "engine": "explore",
                        "role": "director", "stage": "run", "status": "ok", "message": "round-trip"})
events = mem.list_research_events(run_id=run_id)
assert events and events[0]["message"] == "round-trip", events
print(f"  research_events: wrote+read {len(events)} (id {events[0]['id']})")

# 2. feature_gap dedup on (name, kind) -> count increments
mem.add_feature_gap("test_feat", "feature", "roundtrip", "unknown feature", example="close > test_feat")
g = mem.add_feature_gap("test_feat", "feature", "roundtrip", "unknown feature", example="ema_9 > test_feat")
assert g["count"] == 2, g
assert len(mem.list_feature_gaps()) >= 1
print(f"  feature_gaps: dedup ok, count={g['count']}, examples={len(g['examples'])}")

# 3. hypothesis CRUD + stats (exercises embedding enrich + id assignment)
h = mem.add_hypothesis(Hypothesis(claim="round-trip probe", mechanism="none",
                                  required_features=["atr_14"]))
assert mem.get_hypothesis(h.id).claim == "round-trip probe"
print(f"  hypothesis: stored {h.id}; stats counts={mem.stats()['counts']}")

print("ROUND-TRIP OK — shared Supabase map works. Point runs at it (POPPER_STORE=supabase).")
