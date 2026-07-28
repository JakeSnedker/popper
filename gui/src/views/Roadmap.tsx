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
    <div className="max-w-5xl">
      <div className="mb-6">
        <div className="text-[10px] uppercase tracking-[0.2em] text-cyan-400 font-semibold">System guide</div>
        <h1 className="text-2xl font-semibold text-slate-100 mt-1">System Phases</h1>
        <p className="text-sm text-slate-400 mt-2 max-w-3xl leading-relaxed">
          How Popper was built and what each layer contributes to a trustworthy research result.
          These are platform phases, not the individual falsification gates shown inside a hypothesis audit.
        </p>
      </div>

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
                  <p className="text-sm text-slate-400 mt-2 leading-relaxed">{p.goal}</p>

                  <div className="grid md:grid-cols-2 gap-3 mt-4 pt-4 border-t border-ink-600">
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-slate-600 font-semibold mb-2">What happens</div>
                      <ol className="space-y-2">
                        {p.process.map((step, index) => (
                          <li key={step} className="flex gap-2 text-xs text-slate-400 leading-relaxed">
                            <span className="font-mono text-slate-600 shrink-0">{index + 1}.</span>
                            <span>{step}</span>
                          </li>
                        ))}
                      </ol>
                    </div>
                    <div className="space-y-3">
                      <div>
                        <div className="text-[10px] uppercase tracking-wide text-slate-600 font-semibold">Output</div>
                        <p className="text-xs text-slate-300 mt-1 leading-relaxed">{p.output}</p>
                      </div>
                      <div>
                        <div className="text-[10px] uppercase tracking-wide text-slate-600 font-semibold">What a human should inspect</div>
                        <p className="text-xs text-slate-400 mt-1 leading-relaxed">{p.inspect}</p>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-lg border border-amber-500/15 bg-amber-500/5 px-3 py-2.5 mt-4">
                    <div className="text-[10px] uppercase tracking-wide text-amber-400/80 font-semibold">Current limitation</div>
                    <p className="text-xs text-slate-400 mt-1 leading-relaxed">{p.current_gap}</p>
                  </div>

                  <p className="text-[11px] text-slate-600 mt-3">
                    <span className="text-slate-500">Completion gate:</span> {p.done_when}
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
