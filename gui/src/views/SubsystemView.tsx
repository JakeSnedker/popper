import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import type { Subsystem, View } from "../lib/types";
import { SUB_STATUS } from "../lib/status";
import { StatCards } from "../components/StatCards";
import { HypothesisTable } from "../components/HypothesisTable";
import { LoadingRows, ErrorState } from "../components/States";

function IOContract({ s }: { s: Subsystem }) {
  return (
    <div className="card p-4 flex items-center gap-3 text-sm flex-wrap">
      <div className="flex-1 min-w-[120px]">
        <div className="text-[10px] uppercase tracking-wider text-slate-600 mb-1">Consumes</div>
        <div className="text-slate-300">{s.consumes}</div>
      </div>
      <span className="text-slate-600 text-xl">→</span>
      <div className="px-3 py-2 rounded-lg bg-ink-700 text-center shrink-0">
        <div className="text-[10px] uppercase tracking-wider text-slate-600">Subsystem</div>
        <div className="text-slate-100 font-medium">{s.name}</div>
      </div>
      <span className="text-slate-600 text-xl">→</span>
      <div className="flex-1 min-w-[120px] text-right">
        <div className="text-[10px] uppercase tracking-wider text-slate-600 mb-1">Produces</div>
        <div className="text-accent font-medium">{s.produces}</div>
      </div>
    </div>
  );
}

function BuildSpec({ phaseId }: { phaseId: number }) {
  const { data, loading, error, reload } = useAsync(() => api.phases(), []);
  if (loading) return <LoadingRows rows={2} />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  const phase = data?.phases.find((p) => p.id === phaseId);
  if (!phase) return null;
  return (
    <div className="card p-4 border-l-2 border-l-accent">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[10px] font-mono text-slate-600">Phase {phase.id}</span>
        <span className="text-sm font-medium text-slate-200">How this phase works</span>
      </div>
      <p className="text-sm text-slate-400 leading-relaxed">{phase.goal}</p>
      <ol className="grid md:grid-cols-3 gap-2 mt-4">
        {phase.process.map((step, index) => (
          <li key={step} className="rounded-lg bg-ink-900/60 border border-ink-600 p-2.5 text-xs text-slate-400 leading-relaxed">
            <span className="font-mono text-accent mr-1.5">{index + 1}.</span>{step}
          </li>
        ))}
      </ol>
      <div className="grid md:grid-cols-2 gap-3 mt-4 text-xs">
        <div><span className="text-slate-600 uppercase text-[10px]">Output</span><p className="text-slate-300 mt-1">{phase.output}</p></div>
        <div><span className="text-slate-600 uppercase text-[10px]">Human check</span><p className="text-slate-400 mt-1">{phase.inspect}</p></div>
      </div>
      <div className="mt-3 pt-3 border-t border-ink-600 text-xs text-amber-200/80">
        <span className="text-amber-400/70">Current limitation:</span> {phase.current_gap}
      </div>
    </div>
  );
}

export function SubsystemView({
  subsystem,
  onSelectHypothesis,
  onNavigate,
}: {
  subsystem: Subsystem;
  onSelectHypothesis: (id: string) => void;
  onNavigate: (v: View) => void;
}) {
  const st = SUB_STATUS[subsystem.status];
  const stats = useAsync(() => api.stats(), []);

  return (
    <div className="max-w-5xl space-y-6">
      {/* header */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <span className={`pill ${st.chip}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
            {st.label}
          </span>
          <button
            onClick={() => onNavigate({ kind: "roadmap" })}
            className="pill bg-ink-700 text-slate-400 hover:text-slate-200 transition"
          >
            Phase {subsystem.phase} →
          </button>
          {subsystem.metric && (
            <span className="text-xs font-mono text-slate-500 ml-auto">{subsystem.metric}</span>
          )}
        </div>
        <h1 className="text-xl font-semibold text-slate-100">{subsystem.name}</h1>
        <p className="text-sm text-slate-400 mt-1">{subsystem.role}</p>
      </div>

      <IOContract s={subsystem} />
      <BuildSpec phaseId={subsystem.phase} />

      {/* live data for the two subsystems with dedicated drill-down tables */}
      {subsystem.id === "memory" && (
        <>
          {stats.loading ? (
            <LoadingRows rows={2} />
          ) : stats.error ? (
            <ErrorState message={stats.error} onRetry={stats.reload} />
          ) : stats.data ? (
            <StatCards stats={stats.data} />
          ) : null}
          <HypothesisTable onSelect={onSelectHypothesis} />
        </>
      )}

      {subsystem.id === "falsify" && (
        <div>
          <h2 className="text-sm font-semibold text-slate-200 mb-1">Falsification outcomes</h2>
          <p className="text-xs text-slate-500 mb-3">
            Open any hypothesis to see its scorecard and the verdict's kill-reasons.
          </p>
          <HypothesisTable onSelect={onSelectHypothesis} />
        </div>
      )}

    </div>
  );
}
