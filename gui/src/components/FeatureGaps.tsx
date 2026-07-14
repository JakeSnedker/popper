import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import { EmptyState, ErrorState, LoadingRows } from "./States";

function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function FeatureGaps() {
  const { data, loading, error, reload } = useAsync(() => api.featureGaps(8), [], 10000);

  if (loading) return <LoadingRows rows={3} />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data || data.feature_gaps.length === 0) {
    return <EmptyState message="No feature gaps logged yet. Invalid-but-useful ideas will appear here." />;
  }

  return (
    <div className="card overflow-hidden">
      <div className="px-3 py-2 border-b border-ink-600 flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
          Feature gaps · auto-refresh 10s
        </span>
        <span className="text-[10px] text-slate-600">unsupported primitives become roadmap memory</span>
      </div>
      <div className="divide-y divide-ink-700 max-h-[300px] overflow-y-auto">
        {data.feature_gaps.map((g) => (
          <div key={g.id} className="px-3 py-2.5">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono text-xs text-accent truncate">{g.name}</span>
                <span className="pill bg-ink-600 text-slate-400">{g.kind}</span>
              </div>
              <span className="text-[10px] text-slate-600 shrink-0">
                {g.count}x · {ago(g.updated_at)}
              </span>
            </div>
            <p className="text-[11px] text-slate-500 mt-1 leading-snug line-clamp-2">{g.detail}</p>
            {g.examples.length > 0 && (
              <p className="text-[10px] text-slate-600 mt-1 line-clamp-1">Example: {g.examples[0]}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
