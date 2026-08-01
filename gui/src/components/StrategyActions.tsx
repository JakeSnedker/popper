import { useState } from "react";
import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import type { HypothesisRecord, MonteCarloDiagnosticResult } from "../lib/types";
import { useAuth } from "./AuthGate";

export function StrategyActions({
  record,
  onDiagnosticComplete,
}: {
  record: HypothesisRecord;
  onDiagnosticComplete: () => void;
}) {
  const id = record.hypothesis.id;
  const auth = useAuth();
  const status = useAsync(() => api.diagnostics(id), [id]);
  const diagnostic = status.data?.diagnostics.find((item) => item.name === "monte_carlo");
  const [running, setRunning] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [result, setResult] = useState<MonteCarloDiagnosticResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runMonteCarlo = async () => {
    setRunning(true);
    setError(null);
    try {
      const completed = await api.runMonteCarlo(id);
      setResult(completed);
      status.reload();
      onDiagnosticComplete();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Monte Carlo failed");
    } finally {
      setRunning(false);
    }
  };

  const downloadForge = async () => {
    setDownloading(true);
    setError(null);
    try {
      await api.downloadForge(id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Strategy Forge failed");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h4 className="text-sm font-medium text-slate-200">Targeted actions</h4>
          <p className="text-xs text-slate-500 mt-1">Run one missing diagnostic or export this exact promoted spec.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={runMonteCarlo}
            disabled={!auth.canWrite || running || status.loading || !diagnostic?.runnable}
            className="rounded-md bg-ink-600 px-3 py-1.5 text-xs text-slate-200 hover:bg-ink-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {running ? "Running Monte Carlo..." : diagnostic?.completed ? "Re-run Monte Carlo" : "Run Monte Carlo"}
          </button>
          {record.hypothesis.status === "promoted" && (
            <button
              onClick={downloadForge} disabled={downloading}
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white hover:brightness-110 disabled:opacity-50"
            >
              {downloading ? "Building exports..." : "Download Pine + MQL5"}
            </button>
          )}
        </div>
      </div>
      {!auth.canWrite && (
        <p className="text-xs text-slate-500 mt-3">Viewer access cannot run or re-run diagnostics.</p>
      )}
      {diagnostic && !diagnostic.runnable && <p className="text-xs text-amber-400 mt-3">{diagnostic.reason}</p>}
      {result && (
        <p className={`text-xs mt-3 ${result.cone.p_ruin > 0.15 ? "text-red-400" : "text-emerald-400"}`}>
          Monte Carlo complete: P(profit) {Math.round(result.cone.p_profit * 100)}%, P(ruin) {Math.round(result.cone.p_ruin * 100)}%.
        </p>
      )}
      {(error || status.error) && <p className="text-xs text-red-400 mt-3">{error ?? status.error}</p>}
    </div>
  );
}
