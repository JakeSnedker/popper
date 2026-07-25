#!/usr/bin/env bash
# Unattended long-run worker for when Jake is away. ONE cli (claude.ai subscription,
# $0) explore-max worker that runs steadily and, when the subscription session rate
# limit is hit (a whole 25-min window yields zero proposals), backs off ~45 min and
# resumes when the window reopens. Low concurrency ON PURPOSE: the sub rate limit is
# per-account, so extra parallel workers only burn the window faster, they don't
# produce more — they just trip the limit instantly (that's what stalled the pool).
#
# Pair with a Scheduled Task (at startup + every 5h, "don't start if already running")
# so it self-restarts after a reboot or crash. All state lives in the shared Supabase
# map, so nothing is lost between restarts.
set -u
cd "$(dirname "$0")/.." || exit 1          # repo root
PY=".venv/Scripts/python.exe"
LOG="${POPPER_HOLIDAY_LOG:-holiday.log}"
export POPPER_LLM_BACKEND=cli              # subscription, $0 (ClaudeCliClient strips ANTHROPIC_API_KEY)

echo "[$(date '+%F %T')] holiday worker START (pid $$)" >> "$LOG"
while true; do
  out=$("$PY" scripts/run_hunt.py --minutes 25 --iterations 4 --explore-heavy 2>&1)
  echo "$out" | grep -E "\[batch|final counts" | tail -2 >> "$LOG"
  if echo "$out" | grep -qE "proposed=[1-9]"; then
    sleep 10                                # producing normally -> keep going
  else
    echo "[$(date '+%F %T')] 0 proposals in 25m (rate-limited/exhausted) -> back off 45m" >> "$LOG"
    sleep 2700
  fi
done
