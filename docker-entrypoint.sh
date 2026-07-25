#!/bin/sh
# Worker loop = Popper's "cron". run_hunt.py already time-boxes each run, so we just
# run it, sleep, and run again forever. compose's `restart: unless-stopped` handles
# crashes/reboots. This is the whole scheduling story — no host cron needed.
#
# ponytail: a shell while-loop, not a real scheduler. That's deliberate — the run is
# already wall-clock bounded and idempotent (every artifact goes to the shared map).
# Upgrade path (discrete scheduled runs) is documented in SHARE_README.md via
# `docker compose run`; reach for it only if you need exact clock times.
set -e

RUN_MODE="${RUN_MODE:-llm}"          # seeded ($0, no API) | llm (spends Anthropic)
RUN_MINUTES="${RUN_MINUTES:-60}"     # wall-clock budget per llm batch
RUN_ITERATIONS="${RUN_ITERATIONS:-12}"
RUN_SLEEP="${RUN_SLEEP:-30}"         # pause between runs (seconds)

echo "[entrypoint] mode=$RUN_MODE minutes=$RUN_MINUTES iterations=$RUN_ITERATIONS sleep=${RUN_SLEEP}s store=${POPPER_STORE:-sqlite}"

while true; do
    if [ "$RUN_MODE" = "seeded" ]; then
        python scripts/run_hunt.py --seeded || echo "[entrypoint] run failed, retrying after sleep"
    else
        python scripts/run_hunt.py --minutes "$RUN_MINUTES" --iterations "$RUN_ITERATIONS" \
            || echo "[entrypoint] run failed, retrying after sleep"
    fi
    echo "[entrypoint] batch done; sleeping ${RUN_SLEEP}s"
    sleep "$RUN_SLEEP"
done
