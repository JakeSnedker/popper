import { useState } from "react";
import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import type { HypothesisStatus } from "../lib/types";
import { StatusBadge } from "./StatusBadge";
import { EmptyState, ErrorState, LoadingRows } from "./States";

const FILTERS: (HypothesisStatus | "all")[] = [
  "all",
  "proposed",
  "testing",
  "killed",
  "survived",
  "promoted",
];

export function HypothesisTable({ onSelect }: { onSelect: (id: string) => void }) {
  const [filter, setFilter] = useState<HypothesisStatus | "all">("all");
  const { data, loading, error, reload } = useAsync(
    () => api.hypotheses(filter === "all" ? undefined : filter),
    [filter]
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h2 className="text-sm font-semibold text-slate-200">Alpha Memory</h2>
        <div className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-2.5 py-1 text-xs rounded-md transition capitalize ${
                filter === f
                  ? "bg-accent text-white"
                  : "bg-ink-700 text-slate-400 hover:bg-ink-600"
              }`}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <LoadingRows rows={5} />
      ) : error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : !data || data.hypotheses.length === 0 ? (
        <EmptyState message="No hypotheses for this filter yet." />
      ) : (
        <div className="card divide-y divide-ink-600 overflow-hidden">
          {data.hypotheses.map((h) => (
            <button
              key={h.id}
              onClick={() => onSelect(h.id)}
              className="w-full text-left p-3 hover:bg-ink-700 transition flex items-center gap-3"
            >
              <span className="text-xs font-mono text-slate-600 w-14 shrink-0">{h.id}</span>
              <span className="flex-1 text-sm text-slate-200 truncate" title={h.claim}>
                {h.claim}
              </span>
              <span className="hidden md:inline text-xs text-slate-500 w-20 shrink-0 capitalize">
                {h.expected_regime.replace("_", " ")}
              </span>
              <span className="hidden sm:inline text-xs font-mono text-slate-400 w-10 shrink-0">
                {h.novelty_score.toFixed(2)}
              </span>
              <StatusBadge status={h.status} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
