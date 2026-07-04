import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import { VerdictBadge } from "./StatusBadge";
import { EmptyState, ErrorState, LoadingRows } from "./States";

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const mono = (v: number | null, d = 2) => (v == null ? "–" : v.toFixed(d));

/** Compact newest-first feed of gauntlet verdicts; click opens the hypothesis. */
export function VerdictStream({ onSelect }: { onSelect: (id: string) => void }) {
  const { data, loading, error, reload } = useAsync(() => api.verdicts(8), []);

  if (loading) return <LoadingRows rows={4} />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data || data.verdicts.length === 0)
    return <EmptyState message="No verdicts yet — the gauntlet hasn't judged anything." />;

  return (
    <div className="card overflow-hidden">
      <div className="px-3 py-2 border-b border-ink-600 text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
        Latest verdicts
      </div>
      <div className="divide-y divide-ink-700 max-h-[420px] overflow-y-auto">
        {data.verdicts.map((v, i) => (
          <button
            key={`${v.hypothesis_id}-${v.created_at}-${i}`}
            onClick={() => onSelect(v.hypothesis_id)}
            className="w-full text-left px-3 py-2.5 hover:bg-ink-700 transition block"
          >
            <div className="flex items-center justify-between gap-2">
              <VerdictBadge type={v.type} />
              <span className="text-[10px] font-mono text-slate-600 shrink-0">
                {v.hypothesis_id} · {ago(v.created_at)}
              </span>
            </div>
            <p className="text-xs text-slate-300 mt-1 leading-snug line-clamp-2">{v.claim}</p>
            <div className="text-[10px] font-mono text-slate-500 mt-1">
              PF {mono(v.profit_factor)} · DSR {mono(v.deflated_sharpe)}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
