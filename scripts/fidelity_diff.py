"""Matched-pair fidelity diff: Popper simulated fills vs a real MT5 run.

The strategy logic in examples/hypotheses/fidelity_ema_cross.yaml and
mt5/FidelityEMACross.mq5 is identical by construction, so this report
separates two very different things:

  LOGIC MISMATCH  — unmatched trades / wrong direction. Should be ~zero for a
                    truly matched pair; anything here means the pair diverged
                    logically (data gap, seeding, session mismatch), NOT fills.
  FILL SLIPPAGE   — matched trades with entry/exit price deltas, timing deltas
                    and exit-reason flips. This is the fill-model difference
                    the benchmark exists to quantify.

Usage:
  python scripts/fidelity_diff.py --mt5 <export.csv> \
      --spec examples/hypotheses/fidelity_ema_cross.yaml \
      --start 2026-04-01 --end 2026-05-01 [--tol 120] [--mt5-utc-offset 0]

  python scripts/fidelity_diff.py --self-check   # synthetic alignment test

MT5 CSV columns (written by the EA): entry_time, entry_price, exit_time,
exit_price, direction, exit_reason. Times are MT5 SERVER time; if the tester
server clock differs from the parquet's UTC tagging, pass --mt5-utc-offset
(hours to SUBTRACT from MT5 times to get UTC; e.g. 3 for a UTC+3 broker).
"""

import argparse
import csv
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root


# --------------------------------------------------------------- loading
def load_mt5(path: str, utc_offset_hours: float) -> list[dict]:
    """EA CSV export -> list of trade dicts with naive-UTC datetimes."""
    shift = timedelta(hours=utc_offset_hours)
    trades = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            trades.append({
                "entry_time": datetime.strptime(row["entry_time"], "%Y.%m.%d %H:%M:%S") - shift,
                "entry": float(row["entry_price"]),
                "exit_time": datetime.strptime(row["exit_time"], "%Y.%m.%d %H:%M:%S") - shift,
                "exit": float(row["exit_price"]),
                "dir": row["direction"].strip().lower(),
                "reason": row["exit_reason"].strip().lower(),
            })
    trades.sort(key=lambda t: t["entry_time"])
    return trades


def run_popper(spec_path: str, start: str, end: str) -> list[dict]:
    """Compile + run the spec over the same window; -> trade dicts (naive UTC)."""
    from popper.data import load_bars
    from popper.dsl import compile_to_backtest
    from popper.experiments.runner import _tf_to_mt5
    from popper.research.loop import load_hypothesis_file

    _, spec = load_hypothesis_file(spec_path)
    bt = compile_to_backtest(spec)
    try:
        bars = load_bars(spec.context.universe[0], _tf_to_mt5(spec.context.timeframe), start, end)
    except FileNotFoundError as e:
        sys.exit(f"No local bar data for this spec ({e}). "
                 "Export it first with scripts/export_mt5.py, then re-run.")
    if bars.empty:
        sys.exit(f"No bars in [{start}, {end}] — parquet covers a different window.")

    result = bt(bars)  # frictionless: the MT5 spread is part of the measured gap
    times = bars["time"].dt.tz_localize(None)  # parquet is UTC-tagged -> naive UTC
    out = []
    for t in result["trades"]:
        out.append({
            "entry_time": times.iloc[t["entry_i"]].to_pydatetime(),
            "entry": float(t["entry"]),
            "exit_time": times.iloc[t["exit_i"]].to_pydatetime(),
            "exit": float(t["exit"]),
            "dir": t["dir"],
            "reason": t["reason"],
            "risk": float(t["risk"]),
            "pnl_r": float(t["pnl_r"]),
        })
    return out


# --------------------------------------------------------------- alignment
def align(popper: list[dict], mt5: list[dict], tol_s: float) -> tuple[list, list, list]:
    """Greedy nearest-entry-time match (same direction, |dt| <= tol).

    Trades never overlap on either side (one position at a time), so a greedy
    sweep over time-sorted lists is exact for this benchmark.
    Returns (pairs, unmatched_popper, unmatched_mt5).
    """
    pairs, used = [], set()
    for p in popper:
        best, best_dt = None, None
        for j, m in enumerate(mt5):
            if j in used or m["dir"] != p["dir"]:
                continue
            dt = abs((m["entry_time"] - p["entry_time"]).total_seconds())
            if dt <= tol_s and (best_dt is None or dt < best_dt):
                best, best_dt = j, dt
        if best is not None:
            used.add(best)
            pairs.append((p, mt5[best]))
    un_p = [p for p in popper if all(pp is not p for pp, _ in pairs)]
    un_m = [m for j, m in enumerate(mt5) if j not in used]
    return pairs, un_p, un_m


# --------------------------------------------------------------- reporting
def _stats(vals: list[float]) -> str:
    if not vals:
        return "n=0"
    a = [abs(v) for v in vals]
    return (f"n={len(vals)}  mean_abs={statistics.mean(a):.3f}  "
            f"median_abs={statistics.median(a):.3f}  max_abs={max(a):.3f}  "
            f"mean_signed={statistics.mean(vals):+.3f}")


def report(popper: list[dict], mt5: list[dict], tol_s: float) -> None:
    pairs, un_p, un_m = align(popper, mt5, tol_s)
    denom = max(len(popper), len(mt5))

    print("=" * 72)
    print("FIDELITY DIFF — Popper simulated fills vs MT5 Strategy Tester")
    print("=" * 72)
    print(f"popper trades: {len(popper)}   mt5 trades: {len(mt5)}   "
          f"matched: {len(pairs)} ({(100 * len(pairs) / denom if denom else 0):.1f}%)   "
          f"entry-time tolerance: {tol_s:.0f}s")

    print("\n--- LOGIC MISMATCH (should be ~zero for a matched pair) " + "-" * 14)
    print(f"unmatched popper: {len(un_p)}   unmatched mt5: {len(un_m)}")
    for label, lst in (("popper-only", un_p), ("mt5-only", un_m)):
        for t in lst[:10]:
            print(f"  {label}: {t['dir']} entry {t['entry_time']} @ {t['entry']} "
                  f"-> exit {t['exit_time']} @ {t['exit']} ({t['reason']})")
        if len(lst) > 10:
            print(f"  ... and {len(lst) - 10} more {label}")
    if not un_p and not un_m:
        print("  none — logic is matched; every difference below is fill-model.")

    if not pairs:
        print("\nNo matched trades — nothing to quantify. Check dates/offset/symbol.")
        return

    entry_d = [m["entry"] - p["entry"] for p, m in pairs]
    exit_d = [m["exit"] - p["exit"] for p, m in pairs]
    entry_t = [(m["entry_time"] - p["entry_time"]).total_seconds() for p, m in pairs]
    exit_t = [(m["exit_time"] - p["exit_time"]).total_seconds() for p, m in pairs]
    reason_flip = [(p, m) for p, m in pairs if p["reason"] != m["reason"]]
    # MT5 pnl in Popper's R unit (risk = the matched trade's 1R price distance)
    r_div = []
    for p, m in pairs:
        if p["risk"] > 0:
            sign = 1 if p["dir"] == "long" else -1
            r_div.append(sign * (m["exit"] - m["entry"]) / p["risk"] - p["pnl_r"])

    print("\n--- FILL SLIPPAGE (matched trades — the measured quantity) " + "-" * 11)
    print(f"entry price delta (mt5-popper): {_stats(entry_d)}")
    print(f"exit  price delta (mt5-popper): {_stats(exit_d)}")
    print(f"entry time  delta (s):          {_stats(entry_t)}")
    print(f"exit  time  delta (s):          {_stats(exit_t)}")
    print(f"pnl_R divergence  (mt5-popper): {_stats(r_div)}")
    agree = len(pairs) - len(reason_flip)
    print(f"exit reason agreement: {agree}/{len(pairs)} ({100 * agree / len(pairs):.1f}%)")
    for p, m in reason_flip[:10]:
        print(f"  flip: entry {p['entry_time']}  popper={p['reason']} mt5={m['reason']} "
              f"(same-bar stop-vs-target ordering is the usual cause)")
    if len(reason_flip) > 10:
        print(f"  ... and {len(reason_flip) - 10} more flips")

    print("\nworst 5 matched entry deltas:")
    for p, m in sorted(pairs, key=lambda pm: -abs(pm[1]["entry"] - pm[0]["entry"]))[:5]:
        print(f"  {p['entry_time']}  popper {p['entry']:.2f} vs mt5 {m['entry']:.2f} "
              f"(d={m['entry'] - p['entry']:+.2f})")


# --------------------------------------------------------------- self-check
def self_check() -> None:
    """Assert-based test of alignment + delta math on synthetic trades."""
    dt = datetime(2026, 4, 1, 12, 0, 0)

    def trade(mins, entry, exit_px, reason, risk=10.0, direction="long"):
        return {"entry_time": dt + timedelta(minutes=mins), "entry": entry,
                "exit_time": dt + timedelta(minutes=mins + 5), "exit": exit_px,
                "dir": direction, "reason": reason, "risk": risk,
                "pnl_r": (exit_px - entry) / risk}

    popper = [trade(0, 100.0, 120.0, "target"),
              trade(30, 200.0, 190.0, "stop"),
              trade(60, 300.0, 320.0, "target")]          # popper-only (no mt5 twin)
    mt5 = [trade(0, 100.5, 120.5, "target"),              # matches #1, +0.5 slippage
           trade(31, 200.2, 190.0, "stop"),               # matches #2, 60s late, +0.2
           trade(90, 400.0, 410.0, "target")]             # mt5-only

    pairs, un_p, un_m = align(popper, mt5, tol_s=120)
    assert len(pairs) == 2, f"expected 2 matches, got {len(pairs)}"
    assert len(un_p) == 1 and un_p[0]["entry"] == 300.0, "popper-only trade wrong"
    assert len(un_m) == 1 and un_m[0]["entry"] == 400.0, "mt5-only trade wrong"

    entry_d = [m["entry"] - p["entry"] for p, m in pairs]
    assert abs(statistics.mean([abs(d) for d in entry_d]) - 0.35) < 1e-9, entry_d
    (p1, m1), (p2, m2) = pairs
    assert (m2["entry_time"] - p2["entry_time"]).total_seconds() == 60.0
    # R divergence: trade 1 mt5 pnl_r = 20/10 = 2.0 == popper -> 0
    assert abs((m1["exit"] - m1["entry"]) / p1["risk"] - p1["pnl_r"]) < 1e-9

    # direction guard: a short never matches a long at the same time
    flipped = [dict(mt5[0], dir="short")]
    pairs2, _, _ = align(popper[:1], flipped, tol_s=120)
    assert not pairs2, "direction mismatch must not align"

    print("self-check OK: alignment, unmatched split, and delta math verified")


# --------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mt5", help="CSV exported by mt5/FidelityEMACross.mq5")
    ap.add_argument("--spec", default="examples/hypotheses/fidelity_ema_cross.yaml")
    ap.add_argument("--start", help="YYYY-MM-DD (match the tester start date)")
    ap.add_argument("--end", help="YYYY-MM-DD (match the tester end date)")
    ap.add_argument("--tol", type=float, default=120.0,
                    help="entry-time match tolerance in seconds (default 120)")
    ap.add_argument("--mt5-utc-offset", type=float, default=0.0,
                    help="hours to subtract from MT5 server times to get UTC")
    ap.add_argument("--self-check", action="store_true",
                    help="run the synthetic alignment/delta test and exit")
    a = ap.parse_args()

    if a.self_check:
        self_check()
        return
    if not (a.mt5 and a.start and a.end):
        ap.error("--mt5, --start and --end are required (or use --self-check)")

    mt5 = load_mt5(a.mt5, a.mt5_utc_offset)
    popper = run_popper(a.spec, a.start, a.end)
    report(popper, mt5, a.tol)


if __name__ == "__main__":
    main()
