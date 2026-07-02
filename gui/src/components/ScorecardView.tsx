import type { Scorecard } from "../lib/types";

const BARS: { key: keyof Scorecard; label: string }[] = [
  { key: "robustness", label: "Robustness" },
  { key: "regime_stability", label: "Regime stability" },
  { key: "capacity", label: "Capacity" },
  { key: "execution_realism", label: "Execution realism" },
  { key: "novelty", label: "Novelty" },
];

function Bar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.6 ? "bg-emerald-400" : value >= 0.4 ? "bg-amber-400" : "bg-red-400";
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-slate-400">{label}</span>
        <span className="text-slate-300 font-mono">{value.toFixed(2)}</span>
      </div>
      <div className="h-1.5 bg-ink-700 rounded-full overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function ScorecardView({ sc }: { sc: Scorecard }) {
  const pboBad = sc.pbo > 0.5;
  return (
    <div className="space-y-3">
      {BARS.map((b) => (
        <Bar key={b.key} label={b.label} value={sc[b.key] as number} />
      ))}
      <div className="grid grid-cols-2 gap-3 pt-1">
        <div className="card p-2.5">
          <div className="text-xs text-slate-500">Deflated Sharpe</div>
          <div
            className={`text-lg font-mono ${sc.deflated_sharpe >= 1 ? "text-emerald-300" : "text-amber-300"}`}
          >
            {sc.deflated_sharpe.toFixed(2)}
          </div>
        </div>
        <div className="card p-2.5">
          <div className="text-xs text-slate-500">PBO (overfit prob.)</div>
          <div className={`text-lg font-mono ${pboBad ? "text-red-300" : "text-emerald-300"}`}>
            {sc.pbo.toFixed(2)}
          </div>
        </div>
      </div>
    </div>
  );
}
