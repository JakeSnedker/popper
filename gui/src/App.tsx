import { useState } from "react";
import { api } from "./api";
import { useAsync } from "./lib/useAsync";
import type { View } from "./lib/types";
import { Sidebar } from "./components/layout/Sidebar";
import { ControlCenter } from "./views/ControlCenter";
import { Roadmap } from "./views/Roadmap";
import { SubsystemView } from "./views/SubsystemView";
import { HypothesisDetail } from "./components/HypothesisDetail";
import { LoadingRows, ErrorState } from "./components/States";

function HealthPill() {
  const { data } = useAsync(() => api.health(), []);
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={`h-2 w-2 rounded-full ${data?.ok ? "bg-emerald-400" : "bg-red-400"}`} />
      <span className="text-slate-500">
        {data ? `online · ${data.store} · v${data.version}` : "connecting…"}
      </span>
    </div>
  );
}

function crumb(view: View, name?: string): string {
  if (view.kind === "home") return "Control Center";
  if (view.kind === "roadmap") return "Roadmap";
  return name ?? "Subsystem";
}

export default function App() {
  const [view, setView] = useState<View>({ kind: "home" });
  const [selected, setSelected] = useState<string | null>(null);
  const arch = useAsync(() => api.architecture(), []);

  if (arch.loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-ink-900">
        <div className="w-64">
          <LoadingRows rows={3} />
        </div>
      </div>
    );
  }
  if (arch.error || !arch.data) {
    return (
      <div className="h-screen flex items-center justify-center bg-ink-900 px-6">
        <div className="max-w-md w-full">
          <ErrorState message={arch.error ?? "No architecture data"} onRetry={arch.reload} />
        </div>
      </div>
    );
  }

  const data = arch.data;
  const current =
    view.kind === "subsystem" ? data.subsystems.find((s) => s.id === view.id) : undefined;

  return (
    <div className="h-screen flex bg-ink-900 overflow-hidden">
      <Sidebar
        subsystems={data.subsystems}
        pipeline={data.pipeline}
        cross={data.cross}
        view={view}
        onNavigate={setView}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-12 shrink-0 border-b border-ink-600 px-6 flex items-center justify-between">
          <div className="text-sm text-slate-400">
            <span className="text-slate-600">Popper /</span>{" "}
            <span className="text-slate-200">{crumb(view, current?.name)}</span>
          </div>
          <HealthPill />
        </header>

        <main className="flex-1 overflow-y-auto p-6">
          {view.kind === "home" && (
            <ControlCenter arch={data} onNavigate={setView} onSelectHypothesis={setSelected} />
          )}
          {view.kind === "roadmap" && <Roadmap />}
          {view.kind === "subsystem" && current && (
            <SubsystemView
              subsystem={current}
              onSelectHypothesis={setSelected}
              onNavigate={setView}
            />
          )}
        </main>
      </div>

      {selected && <HypothesisDetail id={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
