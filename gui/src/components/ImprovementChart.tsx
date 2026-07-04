import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import { VERDICT_HEX } from "../lib/status";
import type { TimelinePoint, VerdictType } from "../lib/types";
import { ErrorState } from "./States";

/** Alpha improvement — how PF / DSR / robustness moved across successive
 * experiments on one hypothesis (the refinement trend). Small multiples:
 * the three metrics live on different scales, so one shared axis would lie. */

const METRICS: { key: keyof TimelinePoint; label: string }[] = [
  { key: "profit_factor", label: "Profit factor" },
  { key: "deflated_sharpe", label: "Deflated Sharpe" },
  { key: "robustness", label: "Robustness" },
];

const SW = 250;
const SH = 44;
const SPAD = 9;

function Spark({
  values,
  verdicts,
  label,
}: {
  values: (number | null)[];
  verdicts: (VerdictType | null)[];
  label: string;
}) {
  const pts = values
    .map((v, i) => ({ v, i }))
    .filter((d): d is { v: number; i: number } => d.v != null);
  if (pts.length === 0) return <span className="text-xs text-slate-600">not recorded</span>;
  const lo = Math.min(...pts.map((d) => d.v));
  const hi = Math.max(...pts.map((d) => d.v));
  const range = hi - lo || 1;
  const x = (i: number) =>
    values.length === 1 ? SW / 2 : SPAD + (i / (values.length - 1)) * (SW - 2 * SPAD);
  const y = (v: number) => SPAD + (1 - (v - lo) / range) * (SH - 2 * SPAD);
  return (
    <svg viewBox={`0 0 ${SW} ${SH}`} className="w-full" role="img" aria-label={`${label} across experiments`}>
      {pts.length > 1 && (
        <polyline
          points={pts.map((d) => `${x(d.i)},${y(d.v)}`).join(" ")}
          fill="none"
          stroke="#475569"
          strokeWidth="2"
          strokeLinejoin="round"
        />
      )}
      {pts.map((d) => (
        <circle
          key={d.i}
          cx={x(d.i)}
          cy={y(d.v)}
          r={4}
          fill={VERDICT_HEX[verdicts[d.i] ?? "none"]}
          stroke="#0f141d"
          strokeWidth="1.5"
        >
          <title>{`experiment #${d.i + 1} · ${label} ${d.v.toFixed(3)} · ${verdicts[d.i] ?? "no verdict"}`}</title>
        </circle>
      ))}
    </svg>
  );
}

export function ImprovementChart({ hypothesisId }: { hypothesisId: string }) {
  const { data, loading, error, reload } = useAsync(
    () => api.timeline(hypothesisId),
    [hypothesisId]
  );

  if (loading) return <div className="skeleton h-24 w-full" />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  const exps = data?.experiments ?? [];
  if (exps.length === 0)
    return <p className="text-xs text-slate-500 italic">No experiments recorded yet.</p>;

  return (
    <div className="card p-3 space-y-3">
      {METRICS.map((m) => {
        const values = exps.map((e) => e[m.key] as number | null);
        const nums = values.filter((v): v is number => v != null);
        const last = nums.length ? nums[nums.length - 1] : null;
        const delta = nums.length > 1 ? nums[nums.length - 1] - nums[0] : null;
        return (
          <div key={m.key} className="flex items-center gap-3">
            <div className="w-36 shrink-0">
              <div className="text-xs text-slate-400">{m.label}</div>
              <div className="font-mono text-sm text-slate-200">
                {last == null ? "–" : last.toFixed(2)}
                {delta != null && (
                  <span
                    className={`text-[10px] ml-1.5 ${delta >= 0 ? "text-emerald-300" : "text-red-300"}`}
                  >
                    {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(2)}
                  </span>
                )}
              </div>
            </div>
            <div className="flex-1 min-w-0">
              <Spark
                values={values}
                verdicts={exps.map((e) => e.verdict)}
                label={m.label}
              />
            </div>
          </div>
        );
      })}
      <p className="text-[10px] text-slate-600 pt-1 border-t border-ink-700">
        {exps.length === 1
          ? "1 experiment — no refinements yet. Dots take the experiment's verdict color."
          : `${exps.length} experiments, oldest → newest. Dots take each experiment's verdict color.`}
      </p>
    </div>
  );
}
