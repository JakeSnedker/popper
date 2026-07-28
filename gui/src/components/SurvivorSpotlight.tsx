import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import { StatusBadge } from "./StatusBadge";
import { EmptyState, ErrorState, LoadingRows } from "./States";

export function SurvivorSpotlight({
  onSelect,
  onOpenAll,
}: {
  onSelect: (id: string) => void;
  onOpenAll: () => void;
}) {
  const { data, loading, error, reload } = useAsync(() => api.survivors(), []);
  const rows = data?.survivors ?? [];

  return (
    <section>
      <div className="flex items-end justify-between gap-4 mb-3">
        <div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-emerald-400 font-semibold">
            Validated pool
          </div>
          <h2 className="text-base font-semibold text-slate-100 mt-1">Hypotheses that survived</h2>
          <p className="text-xs text-slate-500 mt-1">
            Kept separate from the full map so the rare passes never disappear inside the kill volume.
          </p>
        </div>
        <button onClick={onOpenAll} className="text-xs font-semibold text-accent hover:text-blue-300 transition shrink-0">
          Explore all survivors
        </button>
      </div>

      {loading ? (
        <LoadingRows rows={2} />
      ) : error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : rows.length === 0 ? (
        <EmptyState message="No hypothesis has survived the gauntlet yet." />
      ) : (
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
          {rows.slice(0, 3).map((row) => (
            <button
              key={row.hypothesis_id}
              onClick={() => onSelect(row.hypothesis_id)}
              className={`card p-4 text-left hover:bg-ink-700 transition border-t-2 ${row.status === "promoted" ? "border-t-violet-400" : "border-t-emerald-400"}`}
            >
              <div className="flex items-center justify-between gap-2">
                <StatusBadge status={row.status} />
                <span className="text-[10px] font-mono text-slate-600">{row.hypothesis_id}</span>
              </div>
              <p className="text-sm font-medium text-slate-100 mt-3 leading-snug line-clamp-3">{row.claim}</p>
              <div className="grid grid-cols-3 gap-2 mt-4 text-xs">
                <div><div className="font-mono text-slate-200">{row.metrics.deflated_sharpe?.toFixed(2) ?? "-"}</div><div className="text-[9px] uppercase text-slate-600">DSR</div></div>
                <div><div className="font-mono text-slate-200">{row.metrics.pbo?.toFixed(2) ?? "-"}</div><div className="text-[9px] uppercase text-slate-600">PBO</div></div>
                <div><div className="font-mono text-slate-200">{row.trades_n.toLocaleString()}</div><div className="text-[9px] uppercase text-slate-600">Trades</div></div>
              </div>
              <div className={`mt-3 text-[10px] ${row.evidence.diagnostics_recorded ? "text-emerald-300" : "text-amber-300"}`}>
                {row.evidence.diagnostics_recorded
                  ? `${row.evidence.gates_recorded} recorded gates${row.evidence.costs_recorded ? " + costs" : ""}`
                  : "Legacy record: rerun for gate evidence"}
              </div>
            </button>
          ))}
          {rows.length > 3 && (
            <button onClick={onOpenAll} className="card p-4 text-left border-dashed hover:bg-ink-700 transition">
              <div className="text-3xl font-mono text-emerald-300">+{rows.length - 3}</div>
              <div className="text-sm font-semibold text-slate-200 mt-1">More survivors</div>
              <p className="text-xs text-slate-500 mt-2">Search, compare evidence, and open their full audit trails.</p>
            </button>
          )}
        </div>
      )}
    </section>
  );
}
