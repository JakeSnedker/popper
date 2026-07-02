import type { Subsystem } from "../lib/types";
import { SUB_STATUS } from "../lib/status";

function Node({ s, onOpen }: { s: Subsystem; onOpen: (id: string) => void }) {
  const st = SUB_STATUS[s.status];
  return (
    <button
      onClick={() => onOpen(s.id)}
      className={`card border-l-2 ${st.border} p-3 w-full text-left hover:bg-ink-700 transition group`}
    >
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-sm font-medium text-slate-100 leading-tight">{s.name}</span>
        <span className={`h-2 w-2 rounded-full shrink-0 ${st.dot}`} />
      </div>
      <p className="text-[11px] text-slate-500 leading-snug line-clamp-2 mb-2">{s.role}</p>
      <div className="flex items-center justify-between">
        <span className={`pill ${st.chip}`}>{st.label}</span>
        <span className="text-[10px] font-mono text-slate-600">P{s.phase}</span>
      </div>
      {s.metric && (
        <div className="mt-2 pt-2 border-t border-ink-600 text-[11px] font-mono text-slate-400">
          {s.metric}
        </div>
      )}
    </button>
  );
}

function Flow({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center px-1 shrink-0 self-center">
      <span className="text-[9px] text-slate-600 font-mono mb-0.5 whitespace-nowrap">{label}</span>
      <span className="text-slate-600 text-lg leading-none">→</span>
    </div>
  );
}

export function PipelineMap({
  subsystems,
  pipeline,
  cross,
  onOpen,
}: {
  subsystems: Subsystem[];
  pipeline: string[];
  cross: string[];
  onOpen: (id: string) => void;
}) {
  const byId = (id: string) => subsystems.find((s) => s.id === id)!;

  return (
    <div className="space-y-5">
      {/* core research loop */}
      <div className="flex items-stretch gap-1 overflow-x-auto pb-2">
        {pipeline.map((id, i) => {
          const s = byId(id);
          return (
            <div key={id} className="flex items-stretch">
              <div className="w-44 shrink-0">
                <Node s={s} onOpen={onOpen} />
              </div>
              {i < pipeline.length - 1 && <Flow label={s.produces} />}
            </div>
          );
        })}
      </div>

      {/* feedback loop note */}
      <div className="flex items-center gap-2 text-[11px] text-slate-500 pl-1">
        <span className="text-red-400">↩</span>
        <span>
          <span className="text-red-300">KILL</span> verdicts feed back to the Hypothesis Engine —
          failure sharpens the next hypothesis (the self-improving loop).
        </span>
      </div>

      {/* cross-cutting systems */}
      <div>
        <div className="text-[10px] uppercase tracking-wider text-slate-600 font-semibold mb-2">
          Cross-cutting — span every stage
        </div>
        <div className="grid sm:grid-cols-2 gap-3">
          {cross.map((id) => (
            <Node key={id} s={byId(id)} onOpen={onOpen} />
          ))}
        </div>
      </div>
    </div>
  );
}
