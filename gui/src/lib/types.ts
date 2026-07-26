export type HypothesisStatus =
  | "proposed"
  | "testing"
  | "killed"
  | "survived"
  | "promoted";

export type VerdictType = "survive" | "kill" | "promote";

export interface Hypothesis {
  id: string;
  claim: string;
  mechanism: string;
  rationale: string;
  required_features: string[];
  expected_regime: string;
  novelty_score: number;
  status: HypothesisStatus;
  parent_id: string | null;
  created_at: string;
}

// DSL v0.2 shape (alpha / wrapper / context) — matches popper/artifacts/strategy_spec.py
export interface StrategySpec {
  id: string;
  hypothesis_id: string;
  dsl_version: string;
  name: string;
  alpha: {
    features: string[];
    signal: string;
    signal_short: string | null;
  };
  wrapper: Record<string, unknown>;
  context: {
    universe: string[];
    timeframe: string;
    cost_model: string;
  };
  param_space: Record<string, unknown>;
  created_at: string;
}

export interface ExperimentResult {
  id: string;
  spec_id: string;
  dataset: string;
  period_start: string;
  period_end: string;
  trades_n: number;
  raw_metrics: Record<string, unknown>; // numbers + a `trades` array of trade dicts
  equity_curve: number[];
}

export interface Scorecard {
  id: string;
  experiment_id: string;
  robustness: number;
  regime_stability: number;
  capacity: number;
  execution_realism: number;
  novelty: number;
  deflated_sharpe: number;
  pbo: number;
  diagnostics: Record<string, GateDiag>;
}

/** One walk-forward window from gate 1: [start, end] date pairs + in/out-of-sample PF. */
export interface WalkForwardWindow {
  opt: [string, string];
  fwd: [string, string];
  is_pf: number;
  fwd_pf: number;
}

/** Per-gate glass box from the Falsification Gauntlet. Every gate carries
 * `passed`; the rest of the fields depend on which gate it is (gates that
 * never ran are simply absent from the diagnostics dict). */
export interface HorizonDiag {
  n: number;
  mean: number;
  t: number;
}

export interface HorizonFamilyDiag {
  horizons: number[];
  mode?: "loose" | "balanced" | "strict";
  favourable: number;
  required_favourable: number;
  t_ok: number;
  required_t_ok: number;
  min_t?: number;
  avg_t?: number;
  required_avg_t?: number;
  passed: boolean;
}

export interface GateDiag {
  passed: boolean;
  reason?: string;
  // layer 1 · signal edge
  mode?: "loose" | "balanced" | "strict";
  horizons?: Record<string, HorizonDiag>;
  horizon_families?: Record<string, HorizonFamilyDiag>;
  passed_family?: string | null;
  n_signals?: number;
  min_signals?: number;
  // gate 1 · walk-forward
  windows?: WalkForwardWindow[];
  n_windows?: number;
  min_windows?: number;
  wfe?: number;
  wfe_threshold?: number;
  window_consistency?: number;
  avg_fwd_trades?: number;
  min_trades?: number;
  combined_fwd_pf?: number;
  min_forward_pf?: number;
  // gate 2 · parameter robustness
  profitable_frac?: number;
  min_profitable_fraction?: number;
  pf_cv?: number;
  max_pf_cv?: number;
  grid_pfs?: number[];
  // gate 3 · monte carlo ruin
  p_ruin?: number;
  p_profit?: number;
  dd_mean?: number;
  dd_95?: number;
  dd_worst?: number;
  max_mc_ruin?: number;
  ruin_dd?: number;
  // gate 4 · multiple testing
  pbo?: number;
  max_pbo?: number;
  dsr?: number;
  min_deflated_sharpe?: number;
  sr_std?: number;
  n_trials?: number;
}

export interface Verdict {
  id: string;
  hypothesis_id: string;
  experiment_id: string;
  type: VerdictType;
  reasons: string[];
}

export interface HypothesisRecord {
  hypothesis: Hypothesis;
  specs: StrategySpec[];
  experiments: ExperimentResult[];
  scorecards: Scorecard[];
  verdicts: Verdict[];
}

/** Bootstrap Monte Carlo risk cone for one alpha (from /api/hypotheses/{id}/mc-cone). */
export interface MonteCarloCone {
  n: number;
  xs: number[];
  band: Record<string, number[]>; // percentile keys "5" "25" "50" "75" "95"
  spaghetti: number[][];
  realized: number[];
  p_profit: number;
  p_ruin: number;
  dd_med: number;
  dd_95: number;
  final_med: number;
  final_real: number;
  ruin_dd: number;
  experiment_id: string;
  verdict: VerdictType | null;
  trades_n: number;
}

export interface Phase {
  id: number;
  name: string;
  goal: string;
  status: "done" | "in_progress" | "todo";
  done_when: string;
}

export interface PhasesResponse {
  phases: Phase[];
  progress: { done: number; in_progress: number; total: number; pct: number };
}

export interface Stats {
  hypotheses_by_status: Record<string, number>;
  counts: Record<string, number>;
}

export type SignalEdgeMode = "loose" | "balanced" | "strict";

export interface FeatureGap {
  id: string;
  name: string;
  kind: string;
  source: string;
  detail: string;
  examples: string[];
  count: number;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ResearchRunRequest {
  iterations: number;
  signal_edge_mode: SignalEdgeMode;
  exit_menu: boolean;
}

export interface LlmUsage {
  calls: number;
  failed_calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  failovers: number;
  providers: Record<string, number>;
}

export interface ResearchRunSummary {
  run_id: string;
  iterations: number;
  budget_spent: Record<string, number>;
  proposed: number;
  killed: number;
  survived: number;
  worker_runs: number;
  skipped: number;
  failures: string[];
  categories: Record<string, { survivors: number; kills: number; dead: boolean }>;
  llm_usage: LlmUsage;
  provider_paused: boolean;
  retry_after: number;
  stopped_reason: string;
}

export interface ResearchEvent {
  id: string;
  run_id: string;
  iteration: number;
  engine: string;
  role: string;
  stage: string;
  status: string;
  message: string;
  artifact_ids: Record<string, string>;
  payload: Record<string, unknown>;
  created_at: string;
}

export type SubsystemStatus = "built" | "partial" | "next" | "planned";

export interface Subsystem {
  id: string;
  name: string;
  group: "input" | "loop" | "output" | "cross";
  role: string;
  consumes: string;
  produces: string;
  phase: number;
  status: SubsystemStatus;
  metric: string | null;
}

export interface Artifact {
  name: string;
  desc: string;
}

export interface Architecture {
  subsystems: Subsystem[];
  pipeline: string[];
  cross: string[];
  artifacts: Artifact[];
  progress: { done: number; in_progress: number; total: number; pct: number };
}

// --- Phase 8 observability ---

export interface MapPoint {
  id: string;
  claim: string;
  edge_type: string;
  novelty: number;
  verdict: VerdictType | null;
  x: number;
  y: number;
}

export interface MapResponse {
  categories: Record<string, number>;
  whitespace: string[];
  points: MapPoint[];
}

export interface VerdictFeedItem {
  hypothesis_id: string;
  claim: string;
  type: VerdictType;
  reasons: string[];
  created_at: string;
  profit_factor: number | null;
  deflated_sharpe: number | null;
}

export interface TimelinePoint {
  profit_factor: number | null;
  deflated_sharpe: number | null;
  robustness: number | null;
  verdict: VerdictType | null;
}

export interface DataSource {
  symbol: string;
  timeframe: string;
  rows: number | null;
}

export interface DataSourcesResponse {
  data_dir: string;
  sources: DataSource[];
}

// app navigation
export type View =
  | { kind: "home" }
  | { kind: "roadmap" }
  | { kind: "subsystem"; id: string };
