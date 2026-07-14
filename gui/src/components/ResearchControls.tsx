import { useState } from "react";
import { api } from "../api";
import type { ResearchRunSummary, SignalEdgeMode } from "../lib/types";

const modeHelp: Record<SignalEdgeMode, string> = {
  loose: "Discovery: coherent positive family, weak t allowed.",
  balanced: "Default: coherent family plus moderate t-stat evidence.",
  strict: "Hard gate: every horizon in the family needs strong evidence.",
};

export function ResearchControls({ onRunComplete }: { onRunComplete?: (runId: string) => void }) {
  const [iterations, setIterations] = useState(3);
  const [signalEdgeMode, setSignalEdgeMode] = useState<SignalEdgeMode>("balanced");
  const [exitMenu, setExitMenu] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ResearchRunSummary | null>(null);

  async function run() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const summary = await api.runResearch({
        iterations,
        signal_edge_mode: signalEdgeMode,
        exit_menu: exitMenu,
      });
      setResult(summary);
      onRunComplete?.(summary.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">Research Run</h2>
          <p className="text-xs text-slate-500 mt-1">
            Launch the Director from the dashboard. The request waits for completion; use small runs locally.
          </p>
        </div>
        <button
          onClick={run}
          disabled={running}
          className="px-3 py-2 rounded-md bg-accent text-white text-xs font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {running ? "Running..." : "Start run"}
        </button>
      </div>

      <div className="grid sm:grid-cols-3 gap-3">
        <label className="space-y-1 text-xs">
          <span className="text-slate-400">Iterations</span>
          <input
            type="number"
            min={1}
            max={50}
            value={iterations}
            onChange={(e) => setIterations(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            className="w-full rounded-md bg-ink-800 border border-ink-600 px-2 py-1.5 text-slate-200"
          />
        </label>
        <label className="space-y-1 text-xs">
          <span className="text-slate-400">Signal-edge mode</span>
          <select
            value={signalEdgeMode}
            onChange={(e) => setSignalEdgeMode(e.target.value as SignalEdgeMode)}
            className="w-full rounded-md bg-ink-800 border border-ink-600 px-2 py-1.5 text-slate-200"
          >
            <option value="loose">loose</option>
            <option value="balanced">balanced</option>
            <option value="strict">strict</option>
          </select>
        </label>
        <label className="flex items-end gap-2 text-xs text-slate-400 pb-1">
          <input
            type="checkbox"
            checked={exitMenu}
            onChange={(e) => setExitMenu(e.target.checked)}
            className="h-4 w-4 accent-blue-500"
          />
          Run exit-menu search
        </label>
      </div>
      <p className="text-[11px] text-slate-600">{modeHelp[signalEdgeMode]}</p>

      {error && <p className="text-xs text-red-400">{error}</p>}
      {result && (
        <div className="rounded-md border border-ink-600 bg-ink-900/60 p-3 text-xs">
          <div className="mb-2 text-[11px] font-mono text-slate-500">run {result.run_id}</div>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 font-mono">
            <span>proposed {result.proposed}</span>
            <span>killed {result.killed}</span>
            <span>survived {result.survived}</span>
            <span>workers {result.worker_runs}</span>
            <span>skipped {result.skipped}</span>
          </div>
          {result.failures.length > 0 && (
            <p className="mt-2 text-[11px] text-amber-300 line-clamp-3">
              Non-fatal: {result.failures[0]}
            </p>
          )}
          <p className="mt-2 text-[11px] text-slate-600">
            Alpha Map and verdict stream auto-refresh; feature gaps appear below if validation rejected useful primitives.
          </p>
        </div>
      )}
    </div>
  );
}
