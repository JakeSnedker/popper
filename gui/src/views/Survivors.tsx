import { useDeferredValue, useState } from "react";
import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import type { SurvivorTest } from "../lib/types";
import { StatusBadge } from "../components/StatusBadge";
import { EmptyState, ErrorState, LoadingRows } from "../components/States";

function number(value: number | null, digits = 2): string {
  return value == null ? "Not recorded" : value.toFixed(digits);
}

function duration(seconds: number | null): string {
  if (seconds == null) return "Not recorded";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function modelName(row: SurvivorTest): string {
  if (!row.proposer.model) return "Not recorded / manual";
  return `${row.proposer.provider ?? "provider unknown"} / ${row.proposer.model}`;
}

function Metric({ label, value, tone = "text-slate-100" }: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="rounded-lg bg-ink-900/70 border border-ink-600 px-3 py-2">
      <div className={`font-mono text-sm font-semibold ${tone}`}>{value}</div>
      <div className="text-[10px] uppercase tracking-wide text-slate-600 mt-0.5">{label}</div>
    </div>
  );
}

function Evidence({ row }: { row: SurvivorTest }) {
  if (!row.evidence.diagnostics_recorded) {
    return (
      <div className="rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2">
        <div className="text-xs font-semibold text-amber-300">Legacy evidence</div>
        <p className="text-[11px] text-slate-400 mt-1">
          No gate-by-gate diagnostics were stored. Keep this result visible, but rerun it before
          treating it as verified research evidence.
        </p>
      </div>
    );
  }
  if (!row.evidence.costs_recorded) {
    return (
      <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/10 px-3 py-2">
        <div className="text-xs font-semibold text-cyan-300">Gauntlet-auditable, gross only</div>
        <p className="text-[11px] text-slate-400 mt-1">
          {row.evidence.gates_recorded} gates recorded. Net cost metrics are still missing.
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2">
      <div className="text-xs font-semibold text-emerald-300">Auditable survivor with costs</div>
      <p className="text-[11px] text-slate-400 mt-1">
        {row.evidence.gates_recorded} gates and net cost metrics are recorded. Paper/live approval is
        still a separate phase.
      </p>
    </div>
  );
}

function SurvivorCard({ row, onSelect }: { row: SurvivorTest; onSelect: (id: string) => void }) {
  const m = row.metrics;
  return (
    <article className={`card overflow-hidden ${row.status === "promoted" ? "border-violet-500/30" : "border-emerald-500/25"}`}>
      <div className="p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={row.status} />
          <span className="pill bg-ink-700 text-slate-300 capitalize">
            {row.edge_type.replace(/_/g, " ")}
          </span>
          <span className="pill bg-ink-700 text-slate-400 capitalize">
            {row.expected_regime.replace(/_/g, " ")}
          </span>
          <span className="ml-auto text-[11px] font-mono text-slate-600">
            {row.hypothesis_id}
          </span>
        </div>

        <div>
          <h2 className="text-base font-semibold text-slate-100 leading-snug">{row.claim}</h2>
          <p className="text-sm text-slate-400 leading-relaxed mt-2">{row.mechanism}</p>
        </div>

        <Evidence row={row} />

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          <Metric label="Profit factor" value={number(m.profit_factor)} />
          <Metric
            label="Net expectancy"
            value={m.expectancy_net == null ? "Not recorded" : `${m.expectancy_net.toFixed(3)} R`}
            tone={m.expectancy_net == null ? "text-slate-500" : m.expectancy_net > 0 ? "text-emerald-300" : "text-red-300"}
          />
          <Metric label="Deflated Sharpe" value={number(m.deflated_sharpe)} />
          <Metric label="PBO" value={number(m.pbo)} tone={m.pbo != null && m.pbo <= 0.5 ? "text-emerald-300" : "text-slate-100"} />
          <Metric label="Robustness" value={number(m.robustness)} />
          <Metric label="Trades" value={row.trades_n.toLocaleString()} />
        </div>

        <div className="grid md:grid-cols-3 gap-3 text-xs">
          <div>
            <div className="text-slate-600 uppercase tracking-wide text-[10px]">Test data</div>
            <div className="text-slate-300 mt-1">{row.dataset ?? "Not recorded"}</div>
          </div>
          <div>
            <div className="text-slate-600 uppercase tracking-wide text-[10px]">Proposed by</div>
            <div className="text-slate-300 mt-1 font-mono">{modelName(row)}</div>
          </div>
          <div>
            <div className="text-slate-600 uppercase tracking-wide text-[10px]">Test time</div>
            <div className="text-slate-300 mt-1 font-mono">{duration(row.duration_seconds)}</div>
          </div>
        </div>

        {row.reasons.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wide text-slate-600 mb-1.5">
              Recorded verdict evidence
            </div>
            <div className="flex flex-wrap gap-1.5">
              {row.reasons.slice(0, 5).map((reason) => (
                <span key={reason} className="pill bg-ink-700 text-slate-400">{reason}</span>
              ))}
            </div>
          </div>
        )}
      </div>
      <button
        onClick={() => onSelect(row.hypothesis_id)}
        className="w-full border-t border-ink-600 px-5 py-3 text-left text-xs font-semibold text-accent hover:bg-ink-700 transition"
      >
        Open complete hypothesis audit
      </button>
    </article>
  );
}

export function Survivors({ onSelect }: { onSelect: (id: string) => void }) {
  const { data, loading, error, reload } = useAsync(() => api.survivors(), []);
  const [query, setQuery] = useState("");
  const [evidence, setEvidence] = useState<"all" | "auditable" | "legacy">("all");
  const [sort, setSort] = useState<"status" | "newest" | "quality">("status");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const rows = data?.survivors ?? [];

  const filtered = rows
    .filter((row) => {
      const searchable = `${row.hypothesis_id} ${row.claim} ${row.mechanism} ${row.edge_type} ${row.expected_regime}`.toLowerCase();
      const matchesQuery = !deferredQuery || searchable.includes(deferredQuery);
      const matchesEvidence = evidence === "all"
        || (evidence === "auditable" && row.evidence.diagnostics_recorded)
        || (evidence === "legacy" && !row.evidence.diagnostics_recorded);
      return matchesQuery && matchesEvidence;
    })
    .sort((a, b) => {
      if (sort === "newest") return b.completed_at.localeCompare(a.completed_at);
      if (sort === "quality") {
        const qa = Number(a.evidence.diagnostics_recorded) + Number(a.evidence.costs_recorded) + (a.metrics.deflated_sharpe ?? 0);
        const qb = Number(b.evidence.diagnostics_recorded) + Number(b.evidence.costs_recorded) + (b.metrics.deflated_sharpe ?? 0);
        return qb - qa;
      }
      return Number(b.status === "promoted") - Number(a.status === "promoted");
    });

  const promoted = rows.filter((row) => row.status === "promoted").length;
  const auditable = rows.filter((row) => row.evidence.diagnostics_recorded).length;
  const withCosts = rows.filter((row) => row.evidence.costs_recorded).length;
  const legacy = rows.length - auditable;

  return (
    <div className="max-w-6xl space-y-6">
      <div>
        <div className="text-[10px] uppercase tracking-[0.2em] text-emerald-400 font-semibold">
          Validated pool
        </div>
        <h1 className="text-2xl font-semibold text-slate-100 mt-1">Survivors & Promoted</h1>
        <p className="text-sm text-slate-400 mt-2 max-w-3xl leading-relaxed">
          The small set that made it through Popper's recorded falsification process. Survival is a
          research outcome, not live-trading approval. Evidence coverage below shows what still needs
          to be rerun, cost-tested, exported, or forward-tested.
        </p>
      </div>

      {loading ? (
        <LoadingRows rows={6} />
      ) : error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Metric label="Current survivors" value={rows.length.toLocaleString()} tone="text-emerald-300" />
            <Metric label="Promoted" value={promoted.toLocaleString()} tone="text-violet-300" />
            <Metric label="Gate-auditable" value={`${auditable}/${rows.length}`} tone="text-cyan-300" />
            <Metric label="Cost metrics recorded" value={`${withCosts}/${rows.length}`} />
          </div>

          {legacy > 0 && (
            <div className="rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
              {legacy} survivor record{legacy === 1 ? "" : "s"} predate gate diagnostics. They remain
              discoverable, but should be rerun before being counted as current proof.
            </div>
          )}

          <div className="card p-3 flex flex-col md:flex-row gap-3">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search claim, mechanism, ID, edge type, or regime"
              className="flex-1 min-w-0 rounded-md bg-ink-900 border border-ink-600 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600"
            />
            <select
              value={evidence}
              onChange={(event) => setEvidence(event.target.value as typeof evidence)}
              className="rounded-md bg-ink-900 border border-ink-600 px-3 py-2 text-sm text-slate-300"
            >
              <option value="all">All evidence</option>
              <option value="auditable">Gate-auditable</option>
              <option value="legacy">Legacy / incomplete</option>
            </select>
            <select
              value={sort}
              onChange={(event) => setSort(event.target.value as typeof sort)}
              className="rounded-md bg-ink-900 border border-ink-600 px-3 py-2 text-sm text-slate-300"
            >
              <option value="status">Promoted first</option>
              <option value="quality">Strongest evidence</option>
              <option value="newest">Newest verdict</option>
            </select>
          </div>

          {filtered.length === 0 ? (
            <EmptyState message={rows.length ? "No survivors match these filters." : "No hypothesis has survived yet."} />
          ) : (
            <div className="space-y-4">
              <div className="text-xs text-slate-500">Showing {filtered.length} of {rows.length}</div>
              {filtered.map((row) => (
                <SurvivorCard key={row.hypothesis_id} row={row} onSelect={onSelect} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
