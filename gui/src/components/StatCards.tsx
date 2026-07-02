import type { Stats } from "../lib/types";

const CARDS: { key: string; label: string; accent: string }[] = [
  { key: "proposed", label: "Proposed", accent: "text-slate-300" },
  { key: "testing", label: "Testing", accent: "text-amber-300" },
  { key: "killed", label: "Killed", accent: "text-red-300" },
  { key: "survived", label: "Survived", accent: "text-emerald-300" },
  { key: "promoted", label: "Promoted", accent: "text-violet-300" },
];

export function StatCards({ stats }: { stats: Stats }) {
  const killed = stats.hypotheses_by_status.killed ?? 0;
  const total = stats.counts.hypotheses ?? 0;
  const killRate = total ? Math.round((100 * killed) / total) : 0;

  return (
    <div>
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">Research Funnel</h2>
        <span className="text-xs text-slate-500">
          {stats.counts.experiments ?? 0} experiments · {killRate}% kill rate
        </span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {CARDS.map((c) => (
          <div key={c.key} className="card p-3">
            <div className={`text-2xl font-semibold ${c.accent}`}>
              {stats.hypotheses_by_status[c.key] ?? 0}
            </div>
            <div className="text-xs text-slate-500 mt-0.5">{c.label}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
