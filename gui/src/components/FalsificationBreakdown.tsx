import type { GateDiag, WalkForwardWindow } from "../lib/types";

/** Per-gate glass box: renders Scorecard.diagnostics from the Falsification
 * Gauntlet — each gate's measured values vs its threshold, pass/fail. */

const GATE_ORDER = [
  "gate_signal_edge",
  "gate0_leakage",
  "gate1_walkforward",
  "gate2_robustness",
  "gate3_mc_ruin",
  "gate4_multiple_testing",
];

const GATE_LABELS: Record<string, string> = {
  gate_signal_edge: "Layer 1 · Signal edge / horizon family",
  gate0_leakage: "Gate 0 · Leakage / look-ahead",
  gate1_walkforward: "Gate 1 · Walk-forward OOS",
  gate2_robustness: "Gate 2 · Parameter robustness",
  gate3_mc_ruin: "Gate 3 · Monte Carlo ruin",
  gate4_multiple_testing: "Gate 4 · Multiple testing (DSR / PBO)",
};

const num = (v: number | undefined, d = 2) => (v == null ? "–" : v.toFixed(d));
const pct = (v: number | undefined) => (v == null ? "–" : `${(v * 100).toFixed(0)}%`);

function PassChip({ ok }: { ok: boolean }) {
  return (
    <span
      className={`pill ${ok ? "bg-emerald-500/15 text-emerald-300" : "bg-red-500/15 text-red-300"}`}
    >
      {ok ? "PASS" : "FAIL"}
    </span>
  );
}

/** One measured value vs its threshold. `ok` undefined = informational only. */
function MetricRow({
  label,
  value,
  cmp,
  ok,
}: {
  label: string;
  value: string;
  cmp?: string;
  ok?: boolean;
}) {
  const color = ok === undefined ? "text-slate-200" : ok ? "text-emerald-300" : "text-red-300";
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-slate-400">{label}</span>
      <span className="font-mono shrink-0">
        <span className={color}>{value}</span>
        {cmp && <span className="text-slate-600"> {cmp}</span>}
      </span>
    </div>
  );
}

/** Gate 1 small-multiple: per-window IS vs OOS profit factor, pass line at min PF. */
function WindowsChart({ windows, minPf }: { windows: WalkForwardWindow[]; minPf: number }) {
  const W = 320;
  const H = 96;
  const padL = 26;
  const padB = 14;
  const padT = 6;
  const top = Math.max(minPf, ...windows.flatMap((w) => [w.is_pf, w.fwd_pf])) * 1.1;
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / top);
  const band = (W - padL) / windows.length;
  const barW = Math.min(14, band / 2 - 3);
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full mt-1"
      role="img"
      aria-label="Per-window in-sample vs out-of-sample profit factor"
    >
      {/* baseline + pass threshold */}
      <line x1={padL} y1={y(0)} x2={W} y2={y(0)} stroke="#1e2633" strokeWidth="1" />
      <line
        x1={padL} y1={y(minPf)} x2={W} y2={y(minPf)}
        stroke="#64748b" strokeWidth="1" strokeDasharray="3 3"
      />
      <text x={padL - 4} y={y(minPf) + 3} textAnchor="end" fontSize="8" fill="#64748b">
        {minPf.toFixed(1)}
      </text>
      <text x={padL - 4} y={y(0) + 3} textAnchor="end" fontSize="8" fill="#475569">
        0
      </text>
      {windows.map((w, i) => {
        const x0 = padL + i * band + band / 2;
        const passed = w.fwd_pf >= minPf;
        return (
          <g key={i}>
            <title>{`W${i + 1} · opt ${w.opt[0]} → ${w.opt[1]} (IS PF ${w.is_pf.toFixed(2)}) · fwd ${w.fwd[0]} → ${w.fwd[1]} (OOS PF ${w.fwd_pf.toFixed(2)})`}</title>
            <rect
              x={x0 - barW - 1} y={y(w.is_pf)} width={barW} height={y(0) - y(w.is_pf)}
              fill="#475569" rx="1.5"
            />
            <rect
              x={x0 + 1} y={y(w.fwd_pf)} width={barW} height={y(0) - y(w.fwd_pf)}
              fill={passed ? "#34d399" : "#f87171"} rx="1.5"
            />
            <text x={x0} y={H - 3} textAnchor="middle" fontSize="8" fill="#475569">
              {i + 1}
            </text>
          </g>
        );
      })}
      {/* mini legend */}
      <rect x={padL} y={0} width={7} height={7} fill="#475569" rx="1.5" />
      <text x={padL + 10} y={6.5} fontSize="8" fill="#64748b">IS</text>
      <rect x={padL + 26} y={0} width={7} height={7} fill="#34d399" rx="1.5" />
      <text x={padL + 36} y={6.5} fontSize="8" fill="#64748b">OOS (red = below PF {minPf.toFixed(1)})</text>
    </svg>
  );
}

/** Gate 2 strip plot: every param-grid PF as a dot, break-even line at 1.0. */
function GridPfStrip({ pfs }: { pfs: number[] }) {
  const W = 320;
  const H = 34;
  const lo = Math.min(...pfs, 1);
  const hi = Math.max(...pfs, 1);
  const pad = (hi - lo) * 0.08 + 0.005;
  const x = (v: number) => ((v - lo + pad) / (hi - lo + 2 * pad)) * W;
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full mt-1"
      role="img"
      aria-label="Profit factor across the parameter grid"
    >
      <line x1={0} y1={16} x2={W} y2={16} stroke="#1e2633" strokeWidth="1" />
      <line x1={x(1)} y1={4} x2={x(1)} y2={28} stroke="#64748b" strokeWidth="1" strokeDasharray="3 3" />
      <text x={x(1)} y={H - 1} textAnchor="middle" fontSize="8" fill="#64748b">1.0</text>
      {pfs.map((p, i) => (
        <circle key={i} cx={x(p)} cy={16} r={3} fillOpacity={0.55} fill={p > 1 ? "#34d399" : "#f87171"}>
          <title>{`grid combo ${i + 1} · PF ${p.toFixed(3)}`}</title>
        </circle>
      ))}
      <text x={2} y={H - 1} fontSize="8" fill="#475569">{lo.toFixed(2)}</text>
      <text x={W - 2} y={H - 1} textAnchor="end" fontSize="8" fill="#475569">{hi.toFixed(2)}</text>
    </svg>
  );
}

/** Gate 3 range strip: MC drawdown distribution vs the ruin threshold. */
function DrawdownStrip({ d }: { d: GateDiag }) {
  const W = 320;
  const H = 40;
  const worst = d.dd_worst ?? 0;
  const ruin = d.ruin_dd ?? 0;
  const top = Math.max(worst, ruin) * 1.15 || 1;
  const x = (v: number) => (v / top) * W;
  const marks: { v: number | undefined; label: string }[] = [
    { v: d.dd_mean, label: "mean" },
    { v: d.dd_95, label: "p95" },
    { v: d.dd_worst, label: "worst" },
  ];
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full mt-1"
      role="img"
      aria-label="Monte Carlo drawdown distribution vs ruin threshold"
    >
      {/* ruin zone */}
      <rect x={x(ruin)} y={8} width={W - x(ruin)} height={14} fill="#f87171" fillOpacity={0.12} />
      <line x1={x(ruin)} y1={6} x2={x(ruin)} y2={24} stroke="#f87171" strokeWidth="1" strokeDasharray="3 3" />
      <text x={x(ruin)} y={5} textAnchor="middle" fontSize="8" fill="#f87171">
        ruin {pct(ruin)}
      </text>
      <line x1={0} y1={15} x2={W} y2={15} stroke="#1e2633" strokeWidth="1" />
      {marks.map(
        (m) =>
          m.v != null && (
            <g key={m.label}>
              <line x1={x(m.v)} y1={10} x2={x(m.v)} y2={20} stroke="#cbd5e1" strokeWidth="2" />
              <text x={x(m.v)} y={32} textAnchor="middle" fontSize="8" fill="#64748b">
                {m.label} {pct(m.v)}
              </text>
            </g>
          )
      )}
    </svg>
  );
}

/** Fallback for gates this UI doesn't know yet: dump numeric fields as rows. */
function GenericRows({ d }: { d: GateDiag }) {
  const entries = Object.entries(d).filter(([k, v]) => k !== "passed" && typeof v === "number");
  if (!entries.length) return null;
  return (
    <div className="space-y-1">
      {entries.map(([k, v]) => (
        <MetricRow key={k} label={k.replace(/_/g, " ")} value={String(v)} />
      ))}
    </div>
  );
}

function GateBody({ name, d }: { name: string; d: GateDiag }) {
  switch (name) {
    case "gate_signal_edge":
      return (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <MetricRow
              label="Mode"
              value={d.mode ?? "balanced"}
              cmp={d.mode === "loose" ? "discovery" : d.mode === "strict" ? "hard" : "default"}
            />
            <MetricRow
              label="Signal count"
              value={String(d.n_signals ?? "–")}
              cmp={`≥ ${d.min_signals ?? 30}`}
              ok={(d.n_signals ?? 0) >= (d.min_signals ?? 30)}
            />
          </div>
          {d.passed_family && (
            <p className="text-[11px] text-emerald-300 leading-snug">
              Passing horizon family: {d.passed_family.replace(/_/g, " ")}
            </p>
          )}
          {d.reason && <p className="text-[11px] text-slate-500 leading-snug">{d.reason}</p>}
          {d.horizon_families && (
            <div className="grid grid-cols-1 gap-1.5">
              {Object.entries(d.horizon_families).map(([family, f]) => (
                <div key={family} className="rounded border border-ink-600 bg-ink-900/50 p-2">
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="text-slate-300">{family.replace(/_/g, " ")} · {f.horizons.join("/")} bars</span>
                    <PassChip ok={f.passed} />
                  </div>
                  <div className="mt-1 grid grid-cols-2 gap-1 text-[11px] font-mono text-slate-500">
                    <span>favourable {f.favourable}/{f.required_favourable}</span>
                    <span>t≥{num(f.min_t, 1)} {f.t_ok}/{f.required_t_ok}</span>
                    <span>avg t {num(f.avg_t, 2)}</span>
                    <span>required avg t ≥ {num(f.required_avg_t, 2)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
          {d.horizons && (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-left text-slate-500 border-b border-ink-600">
                    <th className="py-1 pr-2 font-medium">Horizon</th>
                    <th className="py-1 pr-2 font-medium">Signals</th>
                    <th className="py-1 pr-2 font-medium">Mean fwd return</th>
                    <th className="py-1 font-medium">t-stat</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(d.horizons)
                    .sort((a, b) => Number(a[0]) - Number(b[0]))
                    .map(([h, v]) => (
                      <tr key={h} className="border-b border-ink-700">
                        <td className="py-1 pr-2 text-slate-400">{h} bars</td>
                        <td className="py-1 pr-2 font-mono text-slate-400">{v.n}</td>
                        <td className={`py-1 pr-2 font-mono ${v.mean > 0 ? "text-emerald-300" : "text-red-300"}`}>{v.mean.toFixed(6)}</td>
                        <td className={`py-1 font-mono ${v.t >= (d.mode === "loose" ? 1 : 2) ? "text-emerald-300" : "text-slate-400"}`}>{v.t.toFixed(2)}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      );
    case "gate0_leakage":
      return (
        <p className="text-[11px] text-slate-500 leading-snug">
          Future-bar perturbation test — trades that closed in the first half of the data must not
          change when the second half is removed.
        </p>
      );
    case "gate1_walkforward":
      if (!d.windows?.length)
        return (
          <MetricRow
            label="Walk-forward windows"
            value={String(d.n_windows ?? 0)}
            cmp={`≥ ${d.min_windows ?? "?"}`}
            ok={false}
          />
        );
      return (
        <div className="space-y-1">
          <MetricRow
            label="Combined forward PF"
            value={num(d.combined_fwd_pf, 3)}
            cmp={`≥ ${d.min_forward_pf}`}
            ok={(d.combined_fwd_pf ?? 0) >= (d.min_forward_pf ?? 0)}
          />
          <MetricRow
            label="Walk-forward efficiency"
            value={num(d.wfe)}
            cmp={`≥ ${d.wfe_threshold}`}
            ok={(d.wfe ?? 0) >= (d.wfe_threshold ?? 0)}
          />
          <MetricRow
            label="Fwd trades / window"
            value={num(d.avg_fwd_trades, 0)}
            cmp={`≥ ${d.min_trades}`}
            ok={(d.avg_fwd_trades ?? 0) >= (d.min_trades ?? 0)}
          />
          <MetricRow label="Window consistency" value={pct(d.window_consistency)} />
          <WindowsChart windows={d.windows} minPf={d.min_forward_pf ?? 1.0} />
        </div>
      );
    case "gate2_robustness":
      return (
        <div className="space-y-1">
          <MetricRow
            label="Profitable fraction of grid"
            value={pct(d.profitable_frac)}
            cmp={`≥ ${pct(d.min_profitable_fraction)}`}
            ok={(d.profitable_frac ?? 0) >= (d.min_profitable_fraction ?? 0)}
          />
          <MetricRow
            label="PF dispersion (CV)"
            value={pct(d.pf_cv)}
            cmp={`≤ ${pct(d.max_pf_cv)}`}
            ok={(d.pf_cv ?? 1) <= (d.max_pf_cv ?? 0)}
          />
          {d.grid_pfs && d.grid_pfs.length > 0 && (
            <>
              <GridPfStrip pfs={d.grid_pfs} />
              <p className="text-[10px] text-slate-600">
                PF of each of the {d.grid_pfs.length} param combos · dashed line = break-even
              </p>
            </>
          )}
        </div>
      );
    case "gate3_mc_ruin":
      return (
        <div className="space-y-1">
          <MetricRow
            label={`P(drawdown > ${pct(d.ruin_dd)})`}
            value={pct(d.p_ruin)}
            cmp={`≤ ${pct(d.max_mc_ruin)}`}
            ok={(d.p_ruin ?? 1) <= (d.max_mc_ruin ?? 0)}
          />
          <MetricRow label="P(profit)" value={pct(d.p_profit)} />
          <DrawdownStrip d={d} />
        </div>
      );
    case "gate4_multiple_testing":
      return (
        <div className="space-y-1">
          <MetricRow
            label="PBO (overfit prob.)"
            value={num(d.pbo)}
            cmp={`≤ ${num(d.max_pbo)}`}
            ok={(d.pbo ?? 1) <= (d.max_pbo ?? 0)}
          />
          <MetricRow
            label="Deflated Sharpe"
            value={num(d.dsr)}
            cmp={`≥ ${num(d.min_deflated_sharpe)}`}
            ok={(d.dsr ?? 0) >= (d.min_deflated_sharpe ?? 0)}
          />
          <p className="text-[10px] text-slate-600">
            corrected for {d.n_trials ?? "?"} trials · Sharpe σ across grid {num(d.sr_std, 3)}
          </p>
        </div>
      );
    default:
      return <GenericRows d={d} />;
  }
}

function NotReached({ name }: { name: string }) {
  return (
    <div className="card p-3 opacity-60">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-slate-500">
          {GATE_LABELS[name] ?? name.replace(/_/g, " ")}
        </span>
        <span className="pill bg-slate-700/50 text-slate-500">NOT REACHED</span>
      </div>
      <p className="text-[11px] text-slate-600 mt-2 leading-snug">
        Earlier fail-fast gate stopped the gauntlet before this expensive test ran.
      </p>
    </div>
  );
}

export function FalsificationBreakdown({
  diagnostics,
}: {
  diagnostics: Record<string, GateDiag>;
}) {
  const present = new Set(Object.keys(diagnostics));
  const firstFailedIdx = GATE_ORDER.findIndex((k) => diagnostics[k]?.passed === false);
  const keys = [
    ...GATE_ORDER.filter((k) => present.has(k)),
    ...Object.keys(diagnostics).filter((k) => !GATE_ORDER.includes(k)).sort(),
  ];
  const notReached = firstFailedIdx >= 0
    ? GATE_ORDER.slice(firstFailedIdx + 1).filter((k) => !present.has(k))
    : [];
  if (keys.length === 0)
    return (
      <p className="text-xs text-slate-500 italic">
        No per-gate diagnostics recorded (pre-Phase-8 run).
      </p>
    );
  return (
    <div className="space-y-2">
      {keys.map((k) => (
        <div key={k} className="card p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-slate-300">
              {GATE_LABELS[k] ?? k.replace(/_/g, " ")}
            </span>
            <PassChip ok={diagnostics[k].passed} />
          </div>
          <GateBody name={k} d={diagnostics[k]} />
        </div>
      ))}
      {notReached.map((k) => <NotReached key={k} name={k} />)}
    </div>
  );
}
