import { useState } from "react";
import { api } from "../api";
import type { FleetNode } from "../lib/types";
import { useAsync } from "../lib/useAsync";
import { useAuth } from "./AuthGate";
import { EmptyState, ErrorState, LoadingRows } from "./States";

// The only place an operator can see whether the OTHER machines are alive.
// Local status answers "is my node running"; this answers "is the team running".
const OK_STATES = new Set(["running", "researching", "idle", "starting", "smoke_test", "parked"]);

function age(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 90) return `${seconds}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function tone(node: { healthy: boolean; status: string }): string {
  if (node.healthy && OK_STATES.has(node.status)) return "bg-emerald-500";
  if (node.status.endsWith("backoff")) return "bg-amber-500";
  if (node.status === "crashed") return "bg-rose-500";
  return "bg-slate-600";
}

function SwitchToggle({
  node,
  onDone,
}: {
  node: FleetNode;
  onDone: () => void;
}) {
  const { canWrite } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Only non-viewers can toggle; the API/repo enforce the per-operator scope.
  const enabled = canWrite && node.can_toggle;
  const on = node.switch === "on";

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      await api.setNodeSwitch(node.machine_id, on ? "off" : "on");
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1 shrink-0">
      <button
        onClick={toggle}
        disabled={!enabled || busy}
        title={enabled ? (on ? "Park this node after the current hunt" : "Resume this node") : "Read-only in viewer mode"}
        className={`inline-flex items-center gap-2 rounded-full px-2 py-1 text-[10px] font-semibold transition ${
          enabled ? "hover:brightness-125 cursor-pointer" : "cursor-not-allowed opacity-40"
        } ${on ? "bg-emerald-600/20 text-emerald-300" : "bg-slate-700 text-slate-300"}`}
      >
        <span className={`w-2 h-2 rounded-full ${on ? "bg-emerald-400" : "bg-slate-400"}`} />
        {busy ? "..." : on ? "ON" : "OFF"}
      </button>
      {error && <span className="text-[9px] text-rose-400">{error}</span>}
    </div>
  );
}

export function NodeFleet() {
  const { data, loading, error, reload } = useAsync(() => api.fleet(), [], 30000);

  if (loading) return <LoadingRows rows={3} />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (data?.error) return <ErrorState message={data.error} onRetry={reload} />;
  if (!data || data.nodes.length === 0) {
    return <EmptyState message="No node has reported yet. Start a node with operator\Popper-Start.cmd." />;
  }

  const releases = new Set(data.nodes.map((node) => node.release_id));

  return (
    <div className="card overflow-hidden">
      <div className="px-3 py-2 border-b border-ink-600 flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
          Node fleet · shared
        </span>
        <span className="text-[10px] text-slate-600">
          {data.healthy}/{data.nodes.length} healthy
        </span>
      </div>
      <div className="divide-y divide-ink-600">
        {data.nodes.map((node) => (
          <div key={node.machine_id} className="px-3 py-2 flex items-center gap-3">
            <span className={`w-2 h-2 rounded-full shrink-0 ${tone(node)}`} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-xs text-slate-200 truncate">{node.machine_id}</span>
                <span className="text-[10px] text-slate-500 truncate">{node.operator_label}</span>
              </div>
              {node.last_error && (
                <div className="text-[10px] text-rose-400 truncate" title={node.last_error}>
                  {node.last_error}
                </div>
              )}
            </div>
            <span className="pill bg-ink-700 text-slate-300 shrink-0">{node.status}</span>
            <SwitchToggle node={node} onDone={reload} />
            <span className="text-[10px] text-slate-500 shrink-0 w-16 text-right">
              {age(node.seconds_since_heartbeat)}
            </span>
          </div>
        ))}
      </div>
      {releases.size > 1 && (
        <div className="px-3 py-2 border-t border-ink-600 text-[10px] text-amber-400">
          Nodes are running {releases.size} different releases. Results stay comparable only on one
          tag — update the stragglers.
        </div>
      )}
    </div>
  );
}
