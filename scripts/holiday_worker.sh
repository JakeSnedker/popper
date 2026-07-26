#!/usr/bin/env bash
# Unattended subscription hunt. The Python router alternates official Claude and
# Codex CLI accounts for creative work, uses cheap synthesis first, and returns 75
# as soon as every provider circuit is open.
set -u
cd "$(dirname "$0")/.." || exit 1
PY=".venv/Scripts/python.exe"
LOG="${POPPER_HOLIDAY_LOG:-holiday.log}"
export POPPER_LLM_BACKEND="${POPPER_LLM_BACKEND:-subscriptions}"

echo "[$(date '+%F %T')] holiday worker START (pid $$ backend=$POPPER_LLM_BACKEND)" >> "$LOG"
while true; do
  out=$($PY scripts/run_hunt.py --minutes 25 --iterations 20 --explore-heavy 2>&1)
  rc=$?
  echo "$out" | grep -E "\[batch|PROVIDER_PAUSED|final counts" | tail -3 >> "$LOG"
  if [ "$rc" -eq 75 ]; then
    wait=$(echo "$out" | sed -n 's/.*PROVIDER_PAUSED retry_after=\([0-9][0-9]*\).*/\1/p' | tail -1)
    wait=${wait:-2700}
    [ "$wait" -lt 60 ] && wait=60
    [ "$wait" -gt 7200 ] && wait=7200
    echo "[$(date '+%F %T')] provider circuit open -> back off ${wait}s" >> "$LOG"
    sleep "$wait"
  elif echo "$out" | grep -qE "proposed=[1-9]"; then
    sleep 10
  else
    echo "[$(date '+%F %T')] no proposals (content/data failures) -> back off 5m" >> "$LOG"
    sleep 300
  fi
done
