#!/bin/sh
# Containers package the shared store/dashboard and deterministic smoke. Subscription
# generation runs on the host, where each operator authenticates official CLIs.
set -u

RUN_MODE="${RUN_MODE:-seeded}"
RUN_SLEEP="${RUN_SLEEP:-3600}"

if [ "$RUN_MODE" = "llm" ]; then
    echo "[entrypoint] RUN_MODE=llm is host-only: log into claude/codex and run scripts/run_hunt.py"
    exit 2
fi

echo "[entrypoint] seeded smoke mode store=${POPPER_STORE:-sqlite}"
while true; do
    python scripts/run_hunt.py --seeded || echo "[entrypoint] seeded smoke failed"
    sleep "$RUN_SLEEP"
done
