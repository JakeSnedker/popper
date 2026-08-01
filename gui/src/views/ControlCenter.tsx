import { useState } from "react";
import type { Architecture, View } from "../lib/types";
import { AlphaMap } from "../components/AlphaMap";
import { DataSources } from "../components/DataSources";
import { FeatureGaps } from "../components/FeatureGaps";
import { NodeFleet } from "../components/NodeFleet";
import { PipelineMap } from "../components/PipelineMap";
import { ResearchControls } from "../components/ResearchControls";
import { ResearchTrace } from "../components/ResearchTrace";
import { SurvivorSpotlight } from "../components/SurvivorSpotlight";
import { VerdictStream } from "../components/VerdictStream";
import { useAuth } from "../components/AuthGate";

function ProgressRing({ pct }: { pct: number }) {
  const r = 30;
  const c = 2 * Math.PI * r;
  return (
    <svg width="80" height="80" viewBox="0 0 80 80" className="shrink-0">
      <circle cx="40" cy="40" r={r} fill="none" stroke="#1e2633" strokeWidth="8" />
      <circle
        cx="40" cy="40" r={r} fill="none" stroke="#5b8def" strokeWidth="8" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)} transform="rotate(-90 40 40)"
      />
      <text x="40" y="45" textAnchor="middle" className="fill-slate-100 text-sm font-semibold">
        {pct}%
      </text>
    </svg>
  );
}

export function ControlCenter({
  arch,
  onNavigate,
  onSelectHypothesis,
}: {
  arch: Architecture;
  onNavigate: (v: View) => void;
  onSelectHypothesis: (id: string) => void;
}) {
  const open = (id: string) => onNavigate({ kind: "subsystem", id });
  const [activeRunId, setActiveRunId] = useState<string | undefined>();
  const auth = useAuth();

  return (
    <div className="space-y-8 max-w-6xl">
      {/* hero */}
      <div className="card p-5 flex items-center gap-5">
        <ProgressRing pct={arch.progress.pct} />
        <div className="flex-1">
          <h1 className="text-xl font-semibold text-slate-100">Control Center</h1>
          <p className="text-sm text-slate-400 mt-1">
            The full research-to-production architecture. Each station is a subsystem you can open
            and work on independently — click any node below, or use the sidebar.
          </p>
        </div>
        <div className="text-right shrink-0">
          <button
            onClick={() => onNavigate({ kind: "roadmap" })}
            className="text-xs px-3 py-1.5 rounded-md bg-ink-600 hover:bg-ink-500 transition text-slate-200"
          >
            {arch.progress.done}/{arch.progress.total} phases · view roadmap →
          </button>
        </div>
      </div>

      <SurvivorSpotlight
        onSelect={onSelectHypothesis}
        onOpenAll={() => onNavigate({ kind: "survivors" })}
      />

      {/* operator controls */}
      <section className="grid lg:grid-cols-3 gap-4 items-start">
        <div className="lg:col-span-2 space-y-4">
          <ResearchControls canWrite={auth.canWrite} onRunComplete={setActiveRunId} />
          <DataSources />
        </div>
        <div className="space-y-4">
          <NodeFleet />
          <FeatureGaps />
        </div>
      </section>

      <section>
        <ResearchTrace runId={activeRunId} />
      </section>

      {/* alpha map + verdict stream */}
      <section>
        <h2 className="text-sm font-semibold text-slate-200 mb-1">Alpha Map</h2>
        <p className="text-xs text-slate-500 mb-4">
          Every hypothesis in alpha memory, embedded into 2D — clusters are crowded ideas, verdict
          colors show what survived, dashed chips mark unexplored edge-types. Click a point to open
          its full record.
        </p>
        <div className="grid lg:grid-cols-3 gap-4 items-start">
          <div className="lg:col-span-2">
            <AlphaMap onSelect={onSelectHypothesis} />
          </div>
          <VerdictStream onSelect={onSelectHypothesis} />
        </div>
      </section>

      {/* the map */}
      <section>
        <h2 className="text-sm font-semibold text-slate-200 mb-1">Research Loop</h2>
        <p className="text-xs text-slate-500 mb-4">
          Artifacts flow left to right; the loop closes when verdicts feed back into hypotheses.
        </p>
        <PipelineMap
          subsystems={arch.subsystems}
          pipeline={arch.pipeline}
          cross={arch.cross}
          onOpen={open}
        />
      </section>

      {/* artifact contracts */}
      <section>
        <h2 className="text-sm font-semibold text-slate-200 mb-1">Artifact Contracts</h2>
        <p className="text-xs text-slate-500 mb-3">
          The immutable records that flow between subsystems — the interfaces that let any station be
          rebuilt without touching the others.
        </p>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {arch.artifacts.map((a, i) => (
            <div key={a.name} className="card p-3">
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono text-slate-600">{i + 1}</span>
                <span className="text-sm font-medium text-accent">{a.name}</span>
              </div>
              <p className="text-xs text-slate-500 mt-1 leading-snug">{a.desc}</p>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
