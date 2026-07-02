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

export interface StrategySpec {
  id: string;
  hypothesis_id: string;
  dsl_version: string;
  name: string;
  universe: string[];
  timeframe: string;
  features: string[];
  signal: Record<string, unknown>;
  entry: Record<string, unknown>;
  exit: Record<string, unknown>;
  sizing: Record<string, unknown>;
  constraints: Record<string, unknown>;
  param_space: Record<string, unknown>;
}

export interface ExperimentResult {
  id: string;
  spec_id: string;
  dataset: string;
  period_start: string;
  period_end: string;
  trades_n: number;
  raw_metrics: Record<string, number>;
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

// app navigation
export type View =
  | { kind: "home" }
  | { kind: "roadmap" }
  | { kind: "subsystem"; id: string };
