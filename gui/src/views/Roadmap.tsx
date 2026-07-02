import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import { ErrorState, LoadingRows } from "../components/States";

const DOT: Record<string, string> = {
  done: "bg-emerald-400 border-emerald-400",
  in_progress: "bg-accent border-accent animate-pulse",
  todo: "bg-transparent border-ink-500",
};
const LABEL: Record<string, string> = {
  done: "text-emerald-300",
  in_progress: "text-accent",
  todo: "text-slate-300",
};
const TAG: Record<string, string> = {
  done: "bg-emerald-500/15 text-emerald-300",
  in_progress: "bg-accent/15 text-accent",
  todo: "bg-slate-700/60 text-slate-500",
};

export function Roadmap() {
  const { data, loading, error, reload } = useAsync(() => api.phases(), []);

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-semibold text-slate-100 mb-1">Roadmap</h1>
      <p className="text-sm text-slate-400 mb-6">
        The build plan to the complete platform. Status lives in <code className="text-slate-500">popper/phases.py</code>{" "}
        and drives the whole app.
      </p>

      {loading ? (
        <LoadingRows rows={6} />
      ) : error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : !data ? null : (
        <>
          <div className="h-1.5 w-full bg-ink-700 rounded-full overflow-hidden mb-6">
            <div
              className="h-full bg-gradient-to-r from-accent to-emerald-400"
              style={{ width: `${data.progress.pct}%` }}
            />
          </div>
          <ol className="space-y-4">
            {data.phases.map((p, i) => (
              <li key={p.id} className="flex gap-4">
                <div className="flex flex-col items-center pt-1">
                  <span className={`h-3.5 w-3.5 rounded-full border-2 ${DOT[p.status]}`} />
                  {i < data.phases.length - 1 && <span className="w-px flex-1 bg-ink-600 mt-1" />}
                </div>
                <div className="card p-3 flex-1 mb-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-mono text-slate-600">Phase {p.id}</span>
                    <span className={`text-sm font-medium ${LABEL[p.status]}`}>{p.name}</span>
                    <span className={`pill ${TAG[p.status]} ml-auto`}>
                      {p.status.replace("_", " ")}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400 mt-1.5">{p.goal}</p>
                  <p className="text-[11px] text-slate-600 mt-1.5">
                    <span className="text-slate-500">Done when:</span> {p.done_when}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
