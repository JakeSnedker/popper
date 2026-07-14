import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import type { ResearchEvent } from "../lib/types";
import { EmptyState, ErrorState } from "./States";

const STATUS_CLASS: Record<string, string> = {
  running: "bg-blue-500/15 text-blue-300 border-blue-500/20",
  ok: "bg-emerald-500/15 text-emerald-300 border-emerald-500/20",
  done: "bg-emerald-500/15 text-emerald-300 border-emerald-500/20",
  survive: "bg-amber-500/15 text-amber-300 border-amber-500/20",
  promote: "bg-emerald-500/15 text-emerald-300 border-emerald-500/20",
  kill: "bg-rose-500/15 text-rose-300 border-rose-500/20",
  rejected: "bg-slate-500/15 text-slate-300 border-slate-500/20",
  skipped: "bg-slate-500/15 text-slate-300 border-slate-500/20",
  error: "bg-rose-500/15 text-rose-300 border-rose-500/20",
};

function statusClass(status: string) {
  return STATUS_CLASS[status] ?? "bg-slate-500/15 text-slate-300 border-slate-500/20";
}

function eventTime(e: ResearchEvent) {
  return new Date(e.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ResearchTrace({ runId }: { runId?: string }) {
  const { data, loading, error, reload } = useAsync(() => api.researchEvents(runId, 80), [runId], 3000);
  const events = data?.events ?? [];

  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">Research Trace</h2>
          <p className="text-xs text-slate-500 mt-1">
            Live event ledger from the Director. This shows the real sequence: agent stage, artifact IDs, verdict, and failures.
          </p>
        </div>
        <button onClick={reload} className="text-[11px] px-2 py-1 rounded-md bg-ink-700 hover:bg-ink-600 text-slate-300">
          Refresh
        </button>
      </div>

      {runId && <p className="text-[11px] font-mono text-slate-600 mb-2">focused run: {runId}</p>}
      {loading && <div className="skeleton h-48 w-full" />}
      {error && <ErrorState message={error} onRetry={reload} />}
      {!loading && !error && events.length === 0 && (
        <EmptyState message="No research events yet. Start a research run to populate the trace." />
      )}
      {!loading && !error && events.length > 0 && (
        <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
          {events.map((e) => (
            <div key={e.id} className="rounded-lg border border-ink-600 bg-ink-900/60 p-3">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="font-mono text-slate-600">{eventTime(e)}</span>
                <span className="font-mono text-slate-500">iter {e.iteration}</span>
                <span className="pill bg-ink-700 text-slate-300">{e.engine}</span>
                <span className="pill bg-ink-700 text-slate-300">{e.role}.{e.stage}</span>
                <span className={`pill border ${statusClass(e.status)}`}>{e.status}</span>
              </div>
              <p className="text-xs text-slate-300 mt-2 leading-snug">{e.message}</p>
              {Object.keys(e.artifact_ids).length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {Object.entries(e.artifact_ids).map(([k, v]) => (
                    <span key={k} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-ink-700 text-slate-500">
                      {k}: {v}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
