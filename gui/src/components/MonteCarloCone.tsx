import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import type { MonteCarloCone as Cone } from "../lib/types";
import { EmptyState } from "./States";

/** Bootstrap Monte Carlo risk cone for one alpha. Resamples the realized trades
 * with replacement (N histories) so both path and endpoint vary — the spread is
 * the drawdown/PnL risk the single equity number hides. The white line is the
 * one order that actually happened; the band is where the resamples land. */

const W = 520;
const H = 300;
const PADL = 52;
const PADR = 14;
const PADT = 14;
const PADB = 30;
const INK600 = "#1e2633";
const ACCENT = "#5b8def";
const SLATE2 = "#e2e8f0";
const SLATE4 = "#94a3b8";
const SLATE5 = "#64748b";
const GOOD = "#34d399";
const BAD = "#f87171";

// cone color encodes the risk verdict (semantic — matches the app's verdict palette)
function tone(c: Cone): string {
  if (c.p_ruin <= 0.15 && c.p_profit >= 0.6) return GOOD;
  if (c.p_ruin > 0.5 || c.p_profit < 0.45) return BAD;
  return ACCENT;
}

function ConeSvg({ c, accent }: { c: Cone; accent: string }) {
  const all = [...Object.values(c.band).flat(), ...c.realized];
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  const pad = (hi - lo) * 0.06 || 0.1;
  lo = Math.max(0, lo - pad);
  hi = hi + pad;

  const sx = (i: number) => PADL + (i / c.n) * (W - PADL - PADR);
  const sy = (v: number) => PADT + (1 - (v - lo) / (hi - lo)) * (H - PADT - PADB);
  const line = (vals: number[]) =>
    c.xs.map((x, i) => `${sx(x).toFixed(1)},${sy(vals[i]).toFixed(1)}`).join(" ");
  const bandPoly = (a: string, b: string) => {
    const top = c.xs.map((x, i) => `${sx(x).toFixed(1)},${sy(c.band[a][i]).toFixed(1)}`);
    const bot = c.xs
      .map((x, i) => `${sx(x).toFixed(1)},${sy(c.band[b][i]).toFixed(1)}`)
      .reverse();
    return [...top, ...bot].join(" ");
  };

  const yticks = [0, 1, 2, 3, 4].map((k) => lo + ((hi - lo) * k) / 4);
  const xticks = [0, 1, 2, 3, 4].map((k) => Math.round((c.n * k) / 4));

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
         aria-label="Bootstrap Monte Carlo equity cone (stats in the row below)">
      {yticks.map((v, i) => (
        <g key={`y${i}`}>
          <line x1={PADL} y1={sy(v)} x2={W - PADR} y2={sy(v)} stroke={INK600} />
          <text x={PADL - 7} y={sy(v) + 3} textAnchor="end" fontSize="9.5" fill={SLATE5}
                fontFamily="monospace">{v.toFixed(2)}x</text>
        </g>
      ))}
      {xticks.map((v, i) => (
        <text key={`x${i}`} x={sx(v)} y={H - PADB + 15} textAnchor="middle" fontSize="9.5"
              fill={SLATE5} fontFamily="monospace">{v}</text>
      ))}
      {lo < 1 && hi > 1 && (
        <line x1={PADL} y1={sy(1)} x2={W - PADR} y2={sy(1)} stroke={SLATE5} strokeWidth="1"
              strokeDasharray="3 3" strokeOpacity="0.6" />
      )}
      <polygon points={bandPoly("5", "95")} fill={accent} fillOpacity="0.1" />
      <polygon points={bandPoly("25", "75")} fill={accent} fillOpacity="0.2" />
      {c.spaghetti.map((p, i) => (
        <polyline key={i} points={line(p)} fill="none" stroke={SLATE5} strokeWidth="1"
                  strokeOpacity="0.09" />
      ))}
      <polyline points={line(c.band["50"])} fill="none" stroke={accent} strokeWidth="2" />
      <polyline points={line(c.realized)} fill="none" stroke={SLATE2} strokeWidth="2" />
    </svg>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex-1 min-w-[76px]">
      <div className="font-mono text-base font-semibold" style={color ? { color } : undefined}>
        {value}
      </div>
      <div className="font-mono text-[9px] uppercase tracking-wider text-slate-500">{label}</div>
    </div>
  );
}

export function MonteCarloCone({
  hypothesisId,
  refreshKey = 0,
}: {
  hypothesisId: string;
  refreshKey?: number;
}) {
  const { data: c, loading, error } = useAsync(
    () => api.mcCone(hypothesisId),
    [hypothesisId, refreshKey]
  );

  if (loading) return <div className="skeleton h-64 w-full" />;
  // a missing/unusable curve is expected (404), not an alarming error
  if (error || !c)
    return (
      <EmptyState message="No usable equity curve yet — run the gauntlet on this alpha to see its risk cone." />
    );

  const accent = tone(c);
  const pc = c.p_profit >= 0.6 ? GOOD : c.p_profit < 0.45 ? BAD : undefined;
  const rc = c.p_ruin > 0.15 ? BAD : GOOD;

  return (
    <div className="card p-3">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] font-mono text-slate-400 mb-2">
        <span><span style={{ color: SLATE2 }}>──</span> realized</span>
        <span><span style={{ color: accent }}>──</span> median</span>
        <span>▧ 5–95 / 25–75</span>
        <span><span style={{ color: SLATE5 }}>╱</span> sample histories</span>
        <span><span style={{ color: SLATE5 }}>┄</span> break-even</span>
      </div>
      <ConeSvg c={c} accent={accent} />
      <div className="flex flex-wrap gap-3 mt-3 pt-3 border-t border-ink-600">
        <Stat label="P(profit)" value={`${Math.round(c.p_profit * 100)}%`} color={pc} />
        <Stat label="P(ruin)" value={`${Math.round(c.p_ruin * 100)}%`} color={rc} />
        <Stat label="median maxDD" value={`${Math.round(c.dd_med * 100)}%`} />
        <Stat label="95th maxDD" value={`${Math.round(c.dd_95 * 100)}%`} color={SLATE4} />
        <Stat label="median final" value={`${c.final_med.toFixed(2)}x`} />
      </div>
      <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">
        {c.trades_n} trades · {c.experiment_id} · resampled with replacement. Same edge, many
        random orderings — the spread is the risk the single PnL number hides.
      </p>
    </div>
  );
}
