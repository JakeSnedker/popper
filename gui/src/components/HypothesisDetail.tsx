import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import { ScorecardView } from "./ScorecardView";
import { StatusBadge, VerdictBadge } from "./StatusBadge";
import { ErrorState, LoadingRows } from "./States";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-xs uppercase tracking-wide text-slate-500 mb-2">{title}</h4>
      {children}
    </div>
  );
}

export function HypothesisDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, loading, error, reload } = useAsync(() => api.record(id), [id]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-full max-w-xl h-full bg-ink-800 border-l border-ink-600 overflow-y-auto">
        <div className="sticky top-0 bg-ink-800/95 backdrop-blur border-b border-ink-600 px-5 py-3 flex items-center justify-between">
          <span className="text-xs font-mono text-slate-500">{id}</span>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-200 text-sm px-2"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="p-5 space-y-6">
          {loading ? (
            <LoadingRows rows={6} />
          ) : error ? (
            <ErrorState message={error} onRetry={reload} />
          ) : !data ? null : (
            <>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <StatusBadge status={data.hypothesis.status} />
                  {data.verdicts[0] && <VerdictBadge type={data.verdicts[0].type} />}
                </div>
                <h3 className="text-lg font-medium text-slate-100 leading-snug">
                  {data.hypothesis.claim}
                </h3>
              </div>

              <Section title="Mechanism (why the edge should exist)">
                <p className="text-sm text-slate-300">{data.hypothesis.mechanism}</p>
              </Section>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <div className="card p-2.5">
                  <div className="text-xs text-slate-500">Expected regime</div>
                  <div className="text-slate-200 capitalize">
                    {data.hypothesis.expected_regime.replace("_", " ")}
                  </div>
                </div>
                <div className="card p-2.5">
                  <div className="text-xs text-slate-500">Novelty</div>
                  <div className="text-slate-200 font-mono">
                    {data.hypothesis.novelty_score.toFixed(2)}
                  </div>
                </div>
              </div>

              {data.specs.map((s) => (
                <Section key={s.id} title={`Strategy Spec · ${s.id} (DSL v${s.dsl_version})`}>
                  <div className="card p-3 text-sm space-y-1">
                    <div className="text-slate-200 font-medium">{s.name}</div>
                    <div className="text-xs text-slate-500">
                      {s.universe.join(", ")} · {s.timeframe} · features: {s.features.join(", ")}
                    </div>
                    <pre className="text-xs text-slate-400 bg-ink-900 rounded p-2 mt-2 overflow-x-auto">
                      {JSON.stringify(s.signal, null, 2)}
                    </pre>
                  </div>
                </Section>
              ))}

              {data.experiments.map((e) => (
                <Section key={e.id} title={`Experiment · ${e.id}`}>
                  <div className="card p-3 text-sm">
                    <div className="text-xs text-slate-500 mb-2">
                      {e.dataset} · {e.trades_n} trades
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      {Object.entries(e.raw_metrics).map(([k, v]) => (
                        <div key={k}>
                          <div className="text-xs text-slate-500 capitalize">{k.replace("_", " ")}</div>
                          <div className="text-slate-200 font-mono">{v}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </Section>
              ))}

              {data.scorecards.map((sc) => (
                <Section key={sc.id} title={`Scorecard · ${sc.id} (beyond PnL)`}>
                  <ScorecardView sc={sc} />
                </Section>
              ))}

              {data.verdicts.map((v) => (
                <Section key={v.id} title={`Verdict · ${v.id}`}>
                  <div className="card p-3">
                    <VerdictBadge type={v.type} />
                    <ul className="mt-2 space-y-1">
                      {v.reasons.map((r, i) => (
                        <li key={i} className="text-xs text-slate-400 flex gap-2">
                          <span className="text-slate-600">›</span>
                          {r}
                        </li>
                      ))}
                    </ul>
                  </div>
                </Section>
              ))}

              {data.specs.length === 0 && (
                <p className="text-sm text-slate-500 italic">
                  No experiments yet — this hypothesis is still upstream in the loop.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
