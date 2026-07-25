import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import { EmptyState, ErrorState, LoadingRows } from "./States";

function rowsLabel(n: number | null): string {
  if (n === null) return "";
  if (n >= 1000) return `${Math.round(n / 1000)}k bars`;
  return `${n} bars`;
}

// Display-only: the market-data parquets THIS instance can actually see. The
// runner can only test (symbol, timeframe) pairs that exist here, so showing the
// real list prevents pointing a run at data that isn't present (a silent failure).
export function DataSources() {
  const { data, loading, error, reload } = useAsync(() => api.dataSources(), [], 30000);

  if (loading) return <LoadingRows rows={2} />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data || data.sources.length === 0) {
    return (
      <EmptyState message="No market data found. Drop a parquet into data/<symbol>/ (schema: time,open,high,low,close,tick_volume,spread,real_volume)." />
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="px-3 py-2 border-b border-ink-600 flex items-center justify-between gap-3">
        <span className="text-[10px] uppercase tracking-wider text-slate-500 font-semibold">
          Data access · this machine
        </span>
        <span className="text-[10px] text-slate-600">only these can be tested</span>
      </div>
      <div className="p-3 flex flex-wrap gap-2">
        {data.sources.map((s) => (
          <div
            key={`${s.symbol}_${s.timeframe}`}
            className="flex items-center gap-2 px-2.5 py-1.5 rounded-md bg-ink-600 border border-ink-500"
          >
            <span className="font-mono text-xs text-accent">{s.symbol}</span>
            <span className="pill bg-ink-700 text-slate-300">{s.timeframe}</span>
            {s.rows !== null && (
              <span className="text-[10px] text-slate-500">{rowsLabel(s.rows)}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
