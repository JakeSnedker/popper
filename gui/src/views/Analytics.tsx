import { api } from "../api";
import { useAsync } from "../lib/useAsync";
import type { AnalyticsResponse } from "../lib/types";
import { ErrorState, LoadingRows } from "../components/States";

function time(seconds: number | null): string {
  if (seconds == null) return "Not recorded";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

function Kpi({ label, value, note, tone = "text-slate-100" }: {
  label: string;
  value: string;
  note: string;
  tone?: string;
}) {
  return (
    <div className="card p-4">
      <div className={`text-2xl font-semibold font-mono ${tone}`}>{value}</div>
      <div className="text-xs font-semibold text-slate-300 mt-1">{label}</div>
      <p className="text-[11px] text-slate-600 mt-1 leading-snug">{note}</p>
    </div>
  );
}

function Outcomes({ data }: { data: AnalyticsResponse }) {
  const total = data.totals.tests || 1;
  const killed = 100 * data.totals.killed / total;
  const survived = 100 * data.totals.survived / total;
  const promoted = 100 * data.totals.promoted / total;
  return (
    <div className="card p-4">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">Test outcomes</h2>
          <p className="text-xs text-slate-500 mt-1">
            One test is one verdict. Worker refinements can create more tests than hypotheses.
          </p>
        </div>
        <span className="font-mono text-sm text-slate-300">{data.totals.tests.toLocaleString()} total</span>
      </div>
      <div className="h-4 bg-ink-700 rounded-full overflow-hidden flex" aria-label="Test outcome proportions">
        <div className="bg-red-400" style={{ width: `${killed}%` }} title={`Killed ${killed.toFixed(2)}%`} />
        <div className="bg-emerald-400" style={{ width: `${survived}%` }} title={`Survived ${survived.toFixed(2)}%`} />
        <div className="bg-violet-400" style={{ width: `${promoted}%` }} title={`Promoted ${promoted.toFixed(2)}%`} />
      </div>
      <div className="grid grid-cols-3 gap-3 mt-4">
        <div>
          <div className="text-lg font-mono text-red-300">{data.totals.killed.toLocaleString()}</div>
          <div className="text-xs text-slate-500">Killed ({data.totals.fail_rate.toFixed(2)}%)</div>
        </div>
        <div>
          <div className="text-lg font-mono text-emerald-300">{data.totals.survived.toLocaleString()}</div>
          <div className="text-xs text-slate-500">Survived ({survived.toFixed(2)}%)</div>
        </div>
        <div>
          <div className="text-lg font-mono text-violet-300">{data.totals.promoted.toLocaleString()}</div>
          <div className="text-xs text-slate-500">Promoted ({promoted.toFixed(2)}%)</div>
        </div>
      </div>
    </div>
  );
}

function FailureGates({ data }: { data: AnalyticsResponse }) {
  return (
    <div className="card p-4">
      <h2 className="text-sm font-semibold text-slate-200">Where hypotheses fail</h2>
      <p className="text-xs text-slate-500 mt-1 mb-4">
        First failed gate per killed test. This shows where research quality or system capability needs work.
      </p>
      <div className="space-y-3">
        {data.failure_gates.map((gate) => (
          <div key={gate.key}>
            <div className="flex items-baseline justify-between gap-3 text-xs mb-1">
              <span className="text-slate-300">{gate.label}</span>
              <span className="font-mono text-slate-400">
                {gate.count} / {data.totals.killed} ({gate.pct_of_failures.toFixed(1)}%)
              </span>
            </div>
            <div className="h-2 rounded-full bg-ink-700 overflow-hidden">
              <div className="h-full bg-red-400/80 rounded-full" style={{ width: `${gate.pct_of_failures}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Timing({ data }: { data: AnalyticsResponse }) {
  const d = data.test_duration;
  return (
    <div className="space-y-4">
      <div className="card p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-200">Stored test duration</h2>
            <p className="text-xs text-slate-500 mt-1">StrategySpec creation to verdict persistence.</p>
          </div>
          <span className="text-[10px] font-mono text-slate-600">n={d.count}</span>
        </div>
        <div className="grid grid-cols-3 gap-3 mt-4">
          <div><div className="text-lg font-mono text-cyan-300">{time(d.median_seconds)}</div><div className="text-[10px] text-slate-600 uppercase">Median</div></div>
          <div><div className="text-lg font-mono text-slate-200">{time(d.avg_seconds)}</div><div className="text-[10px] text-slate-600 uppercase">Average</div></div>
          <div><div className="text-lg font-mono text-slate-200">{time(d.p90_seconds)}</div><div className="text-[10px] text-slate-600 uppercase">90th pct.</div></div>
        </div>
      </div>
      <div className="grid sm:grid-cols-2 gap-3">
        {data.stages.map((stage) => (
          <div key={stage.key} className="card p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs font-semibold text-slate-300">{stage.label}</div>
              <span className="text-[10px] font-mono text-slate-600">n={stage.count}</span>
            </div>
            <div className="text-xl font-mono text-slate-100 mt-2">{time(stage.median_seconds)}</div>
            <div className="text-[10px] text-slate-600">median | avg {time(stage.avg_seconds)} | p90 {time(stage.p90_seconds)}</div>
            <p className="text-[11px] text-slate-500 mt-2 leading-snug">{stage.description}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Categories({ data }: { data: AnalyticsResponse }) {
  return (
    <div className="card overflow-hidden">
      <div className="p-4 border-b border-ink-600">
        <h2 className="text-sm font-semibold text-slate-200">Performance by edge category</h2>
        <p className="text-xs text-slate-500 mt-1">Where generation is concentrated and which categories have produced passes.</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500 border-b border-ink-600 bg-ink-900/40">
              <th className="px-4 py-2.5 font-medium">Category</th>
              <th className="px-3 py-2.5 font-medium text-right">Generated</th>
              <th className="px-3 py-2.5 font-medium text-right">Tests</th>
              <th className="px-3 py-2.5 font-medium text-right">Killed</th>
              <th className="px-3 py-2.5 font-medium text-right">Passed</th>
              <th className="px-4 py-2.5 font-medium text-right">Pass rate</th>
            </tr>
          </thead>
          <tbody>
            {data.categories.map((row) => {
              const passes = row.survived + row.promoted;
              return (
                <tr key={row.name} className="border-b border-ink-700">
                  <td className="px-4 py-2.5 text-slate-300 capitalize">{row.name.replace(/_/g, " ")}</td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-400">{row.generated}</td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-400">{row.tests}</td>
                  <td className="px-3 py-2.5 text-right font-mono text-red-300">{row.killed}</td>
                  <td className="px-3 py-2.5 text-right font-mono text-emerald-300">{passes}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-200">{row.pass_rate.toFixed(2)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Models({ data }: { data: AnalyticsResponse }) {
  const config = useAsync(() => api.llmConfig(), []);
  const coverage = data.totals.tests
    ? 100 * data.attribution.attributed_tests / data.totals.tests
    : 0;
  return (
    <div className="grid lg:grid-cols-2 gap-4 items-start">
      <div className="card overflow-hidden">
        <div className="p-4 border-b border-ink-600">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-200">Results by proposer model</h2>
            <span className="text-[10px] font-mono text-slate-600">{coverage.toFixed(1)}% attributed</span>
          </div>
          <p className="text-xs text-slate-500 mt-1">Historical outcome grouped by the model that proposed the claim.</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-slate-500 border-b border-ink-600">
              <th className="px-4 py-2 font-medium">Provider / model</th>
              <th className="px-3 py-2 font-medium text-right">Tests</th>
              <th className="px-3 py-2 font-medium text-right">Passed</th>
              <th className="px-4 py-2 font-medium text-right">Rate</th>
            </tr></thead>
            <tbody>
              {data.models.map((row, index) => (
                <tr key={`${row.provider}-${row.model}-${index}`} className="border-b border-ink-700">
                  <td className="px-4 py-2.5 font-mono text-slate-300">
                    {row.model ? `${row.provider ?? "unknown"} / ${row.model}` : "Not recorded / manual"}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-400">{row.tests}</td>
                  <td className="px-3 py-2.5 text-right font-mono text-emerald-300">{row.passed}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-slate-200">{row.pass_rate.toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.attribution.unattributed_tests > 0 && (
          <div className="p-3 bg-amber-500/10 border-t border-amber-500/20 text-[11px] text-amber-200 leading-snug">
            {data.attribution.unattributed_tests} tests predate per-call attribution or came from manual/seeded runs.
            They are not guessed into a model bucket.
          </div>
        )}
      </div>

      <div className="card p-4">
        <h2 className="text-sm font-semibold text-slate-200">Current model routing</h2>
        <p className="text-xs text-slate-500 mt-1 mb-3">Configured routes for new work, not retroactive attribution.</p>
        {config.loading ? <div className="skeleton h-32" /> : config.error ? (
          <p className="text-xs text-red-400">{config.error}</p>
        ) : config.data ? (
          <div className="space-y-2">
            <div className="flex justify-between text-xs"><span className="text-slate-500">Backend / policy</span><span className="font-mono text-slate-300">{config.data.backend} / {config.data.policy}</span></div>
            {Object.entries(config.data.routes).map(([role, route]) => (
              <div key={role} className="rounded-md bg-ink-900 border border-ink-600 px-3 py-2">
                <div className="text-[10px] uppercase text-slate-600">{role.replace(/_/g, " ")}</div>
                <div className="text-xs font-mono text-slate-300 mt-0.5 break-all">{route}</div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Improvements({ data }: { data: AnalyticsResponse }) {
  const top = data.failure_gates[0];
  const testedCategories = data.categories.filter((row) => row.tests > 0);
  const best = [...testedCategories].sort((a, b) => b.pass_rate - a.pass_rate)[0];
  return (
    <div>
      <h2 className="text-sm font-semibold text-slate-200 mb-3">What the numbers say to improve</h2>
      <div className="grid md:grid-cols-3 gap-3">
        <div className="card p-4 border-l-2 border-l-red-400">
          <div className="text-xs font-semibold text-slate-200">Improve candidate quality first</div>
          <p className="text-xs text-slate-500 mt-2 leading-relaxed">
            {top ? `${top.pct_of_failures.toFixed(1)}% of kills stop at ${top.label.toLowerCase()}. ` : ""}
            Tighten mechanisms, signal frequency, and horizon fit before spending more compute on wrappers.
          </p>
        </div>
        <div className="card p-4 border-l-2 border-l-emerald-400">
          <div className="text-xs font-semibold text-slate-200">Exploit proven pockets carefully</div>
          <p className="text-xs text-slate-500 mt-2 leading-relaxed">
            {best ? `${best.name.replace(/_/g, " ")} currently has the highest recorded pass rate at ${best.pass_rate.toFixed(2)}%. ` : "No category has passed yet. "}
            Keep the generator performance-blind; let Workers, costs, and correlation decide where exploitation belongs.
          </p>
        </div>
        <div className="card p-4 border-l-2 border-l-amber-400">
          <div className="text-xs font-semibold text-slate-200">Close evidence gaps</div>
          <p className="text-xs text-slate-500 mt-2 leading-relaxed">
            Only {data.attribution.attributed_tests} of {data.totals.tests} tests identify a proposer model.
            Rerun legacy survivors with diagnostics and costs before using model or survivor comparisons as proof.
          </p>
        </div>
      </div>
    </div>
  );
}

export function Analytics() {
  const { data, loading, error, reload } = useAsync(() => api.analytics(), []);
  if (loading) return <div className="max-w-6xl"><LoadingRows rows={8} /></div>;
  if (error || !data) return <div className="max-w-3xl"><ErrorState message={error ?? "No analytics data"} onRetry={reload} /></div>;

  const passed = data.totals.survived + data.totals.promoted;
  return (
    <div className="max-w-6xl space-y-8">
      <div>
        <div className="text-[10px] uppercase tracking-[0.2em] text-cyan-400 font-semibold">Research observatory</div>
        <h1 className="text-2xl font-semibold text-slate-100 mt-1">Total Stats</h1>
        <p className="text-sm text-slate-400 mt-2 max-w-3xl leading-relaxed">
          The complete research funnel: volume, outcomes, speed, failure concentration, category yield,
          and model attribution. Counts come from stored artifacts rather than the current run alone.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Kpi label="Generated" value={data.totals.generated.toLocaleString()} note={`${data.totals.awaiting_verdict} awaiting a verdict`} />
        <Kpi label="Tests completed" value={data.totals.tests.toLocaleString()} note={`${data.totals.tested_hypotheses} unique hypotheses tested`} tone="text-cyan-300" />
        <Kpi label="Passing now" value={data.totals.current_passing.toLocaleString()} note={`${data.totals.current_survived} survived + ${data.totals.current_promoted} promoted`} tone="text-emerald-300" />
        <Kpi label="Pass rate" value={`${data.totals.pass_rate.toFixed(2)}%`} note={`${data.totals.fail_rate.toFixed(2)}% failed`} tone="text-emerald-300" />
        <Kpi label="Median test" value={time(data.test_duration.median_seconds)} note={`Average ${time(data.test_duration.avg_seconds)}`} />
      </div>

      {passed !== data.totals.current_passing && (
        <div className="rounded-xl border border-slate-600/40 bg-ink-800/60 px-4 py-3 text-xs text-slate-400">
          <span className="text-slate-200 font-semibold">Why this differs from the funnel:</span>{" "}
          {passed} passing verdict{passed === 1 ? " has" : "s have"} been recorded in total, but{" "}
          {data.totals.current_passing} hypothes{data.totals.current_passing === 1 ? "is is" : "es are"}{" "}
          passing right now. A hypothesis that survived once and was killed on a later
          re-test keeps its historical verdict but loses its status. The funnel and the
          survivors page always show current status.
        </div>
      )}

      <div className="grid lg:grid-cols-2 gap-4 items-start">
        <Outcomes data={data} />
        <FailureGates data={data} />
      </div>

      <Improvements data={data} />

      <section>
        <h2 className="text-sm font-semibold text-slate-200 mb-1">How long the system takes</h2>
        <p className="text-xs text-slate-500 mb-4">
          Stored-test timing covers all persisted verdicts with timestamps. Stage timing is sampled from {data.event_sample_size.toLocaleString()} research events.
        </p>
        <Timing data={data} />
      </section>

      <Categories data={data} />
      <Models data={data} />
    </div>
  );
}
