import { useMemo, useState } from "react";
import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import { VERDICT_HEX } from "../lib/status";
import type { MapPoint } from "../lib/types";
import { VerdictBadge } from "./StatusBadge";
import { EmptyState, ErrorState } from "./States";

/** Alpha Map — every hypothesis embedded into 2D (PCA of its embedding),
 * colored by its latest verdict. Clusters = crowded ideas; empty space +
 * whitespace edge-types = where the loop hasn't looked yet. */

const W = 640;
const H = 380;
const PAD = 40;

type Placed = MapPoint & { px: number; py: number };

function place(points: MapPoint[]): Placed[] {
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const rx = x1 - x0 || 1;
  const ry = y1 - y0 || 1;
  // ponytail: identical embeddings (duplicate claims) stack on one pixel — fan
  // repeats out in a small ring so each stays hoverable; beeswarm if maps get dense.
  const seen = new Map<string, number>();
  return points.map((p) => {
    let px = PAD + ((p.x - x0) / rx) * (W - 2 * PAD);
    let py = PAD + ((p.y - y0) / ry) * (H - 2 * PAD);
    const key = `${px.toFixed(0)},${py.toFixed(0)}`;
    const n = seen.get(key) ?? 0;
    seen.set(key, n + 1);
    if (n > 0) {
      px += 11 * Math.cos(n * 2.4);
      py += 11 * Math.sin(n * 2.4);
    }
    return { ...p, px, py };
  });
}

const VERDICT_LEGEND: { key: "promote" | "survive" | "kill" | "none"; label: string }[] = [
  { key: "promote", label: "Promote" },
  { key: "survive", label: "Survive" },
  { key: "kill", label: "Kill" },
  { key: "none", label: "Untested" },
];

function Tooltip({ p }: { p: Placed }) {
  const xPct = (p.px / W) * 100;
  const shiftX = xPct > 70 ? "-100%" : xPct < 30 ? "0%" : "-50%";
  const below = p.py < 110;
  return (
    <div
      className="absolute z-10 pointer-events-none w-60 card p-2.5 shadow-xl"
      style={{
        left: `${xPct}%`,
        top: `${(p.py / H) * 100}%`,
        transform: `translate(${shiftX}, ${below ? "16px" : "calc(-100% - 14px)"})`,
      }}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="text-[10px] font-mono text-slate-500">{p.id}</span>
        {p.verdict ? (
          <VerdictBadge type={p.verdict} />
        ) : (
          <span className="pill bg-slate-700/60 text-slate-400">untested</span>
        )}
      </div>
      <p className="text-xs text-slate-200 leading-snug line-clamp-3">{p.claim}</p>
      <div className="text-[10px] font-mono text-slate-500 mt-1">
        {p.edge_type} · novelty {p.novelty.toFixed(2)}
      </div>
    </div>
  );
}

export function AlphaMap({ onSelect }: { onSelect: (id: string) => void }) {
  const { data, loading, error, reload } = useAsync(() => api.map(), []);
  const [hover, setHover] = useState<Placed | null>(null);
  const placed = useMemo(() => (data ? place(data.points) : []), [data]);

  if (loading) return <div className="skeleton h-80 w-full" />;
  if (error) return <ErrorState message={error} onRetry={reload} />;
  if (!data || data.points.length === 0)
    return (
      <EmptyState message="Alpha memory is empty — run `popper seed` or the research loop to put hypotheses on the map." />
    );

  return (
    <div className="card p-4">
      {/* legend: verdict colors (semantic, never the accent) */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-2">
        {VERDICT_LEGEND.map((v) => (
          <span key={v.key} className="flex items-center gap-1.5 text-xs text-slate-400">
            <span className="h-2 w-2 rounded-full" style={{ background: VERDICT_HEX[v.key] }} />
            {v.label}
          </span>
        ))}
        <span className="ml-auto text-[10px] font-mono text-slate-600">
          {data.points.length} hypotheses · 2D PCA of embedding space
        </span>
      </div>

      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full bg-ink-900 rounded-lg border border-ink-600"
          role="img"
          aria-label="Scatter of hypotheses in embedding space, colored by verdict (table view below)"
          onMouseLeave={() => setHover(null)}
        >
          {placed.map((p) => (
            <circle
              key={p.id}
              cx={p.px}
              cy={p.py}
              r={hover?.id === p.id ? 8 : 6}
              fill={VERDICT_HEX[p.verdict ?? "none"]}
              fillOpacity={p.verdict ? 0.9 : 0.6}
              stroke="#0a0e14"
              strokeWidth="2"
              className="cursor-pointer"
              onMouseEnter={() => setHover(p)}
              onClick={() => onSelect(p.id)}
            />
          ))}
        </svg>
        {hover && <Tooltip p={hover} />}
      </div>

      {/* coverage + whitespace: which edge-types the loop has / hasn't explored */}
      <div className="flex flex-wrap gap-1.5 mt-3">
        {Object.entries(data.categories)
          .sort((a, b) => b[1] - a[1])
          .map(([name, count]) =>
            data.whitespace.includes(name) ? (
              <span
                key={name}
                className="pill border border-dashed border-slate-600 text-slate-500"
              >
                {name} · unexplored
              </span>
            ) : (
              <span key={name} className="pill bg-ink-700 text-slate-300">
                {name} ×{count}
              </span>
            )
          )}
      </div>

      {/* accessible fallback: same data as a table */}
      <details className="mt-3">
        <summary className="text-xs text-slate-500 cursor-pointer hover:text-slate-300 select-none">
          Table view
        </summary>
        <div className="overflow-x-auto mt-2">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-slate-500 border-b border-ink-600">
                <th className="py-1.5 pr-3 font-medium">ID</th>
                <th className="py-1.5 pr-3 font-medium">Claim</th>
                <th className="py-1.5 pr-3 font-medium">Edge type</th>
                <th className="py-1.5 pr-3 font-medium">Novelty</th>
                <th className="py-1.5 font-medium">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {data.points.map((p) => (
                <tr
                  key={p.id}
                  className="border-b border-ink-700 hover:bg-ink-700 cursor-pointer"
                  onClick={() => onSelect(p.id)}
                >
                  <td className="py-1.5 pr-3 font-mono text-slate-500">
                    <button className="hover:text-slate-200">{p.id}</button>
                  </td>
                  <td className="py-1.5 pr-3 text-slate-300 max-w-xs truncate">{p.claim}</td>
                  <td className="py-1.5 pr-3 text-slate-400">{p.edge_type}</td>
                  <td className="py-1.5 pr-3 font-mono text-slate-400">{p.novelty.toFixed(2)}</td>
                  <td className="py-1.5">
                    {p.verdict ? (
                      <VerdictBadge type={p.verdict} />
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
