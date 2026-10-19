/**
 * Robustness diagnostics contracts (wave 3R): GET/POST /api/research/runs/{id}/robustness…,
 * GET /api/research/strategies/{id}/optimisation-profile, GET /api/research/rankings.
 *
 * Hand-written mirrors of src/fiboki/api/routers/research_robustness.py, checked
 * against the generated OpenAPI types in lib/robustness-contract.ts. Every
 * number is the backend's (research/robustness); nothing here computes one.
 * These diagnostics are ADVISORY: none of them is read by a gate or a verdict.
 */
import type { BacktestRunRow, Figure } from "./types";

export interface HoldoutBoundaryView {
  start: string;
  basis: string;
  detail: string;
  trades_excluded: Figure;
  points_excluded: Figure;
  note: string;
}

export interface DistributionView {
  n: Figure;
  p5: Figure;
  p25: Figure;
  p50: Figure;
  p75: Figure;
  p95: Figure;
  mean: Figure;
}

export interface RunMetricsView {
  n_trades: Figure;
  net_profit: Figure;
  total_return_pct: Figure;
  cagr_pct: Figure;
  sharpe: Figure;
  sortino: Figure;
  calmar: Figure;
  max_drawdown_pct: Figure;
  profit_factor: Figure;
  win_rate_pct: Figure;
  expectancy: Figure;
  total_costs: Figure;
}

export interface RerunBriefView {
  profile: string;
  metrics: RunMetricsView;
  ledger_sha256: string;
  rejections: Record<string, number>;
  signals_emitted: Figure;
}

export interface BandView {
  key: string;
  label: string;
  point: Figure;
  p5: Figure;
  p50: Figure;
  p95: Figure;
  basis: string;
  observations: Figure;
  block_length: Figure;
}

export interface ConfidenceBandsView {
  run_id: string;
  seed: number;
  n_boot: number;
  bands: BandView[];
  boundary: HoldoutBoundaryView;
  method: string;
}

export interface BucketView {
  key: string;
  trades: Figure;
  net_pnl: Figure;
  win_rate_pct: Figure;
  expectancy: Figure;
  profit_factor: Figure;
  avg_bars_held: Figure;
}

export interface BreakdownsView {
  run_id: string;
  account_ccy: string;
  groups: Record<string, BucketView[]>;
  entry_reason_note: string;
  calendar_note: string;
  boundary: HoldoutBoundaryView;
}

export interface SplitView {
  key: string;
  start: string | null;
  end: string | null;
  trades: Figure;
  net_profit: Figure;
  total_return_pct: Figure;
  sharpe: Figure;
  win_rate_pct: Figure;
  profit_factor: Figure;
  positive: boolean | null;
}

export interface ConsistencyView {
  run_id: string;
  k: number;
  periods: SplitView[];
  instruments: SplitView[];
  period_positive_share: Figure;
  period_empty: Figure;
  instrument_positive_share: Figure;
  pooled: boolean;
  boundary: HoldoutBoundaryView;
  method: string;
}

export interface FillTable {
  provenance: Figure["provenance"];
  unit: string;
  trade_id: string[];
  instrument: string[];
  direction: string[];
  entry_time: (string | null)[];
  exit_time: (string | null)[];
  exit_reason: string[];
  recorded: boolean[];
  rules: string[][];
  gross_pnl: (number | null)[];
  spread_cost: (number | null)[];
  slippage_cost: (number | null)[];
  commission: (number | null)[];
  financing_cost: (number | null)[];
  net_pnl: (number | null)[];
}

export interface FillDetailView {
  run_id: string;
  intrabar_policy: string;
  rule_counts: Record<string, Figure>;
  rule_definitions: Record<string, string>;
  trades_recorded: Figure;
  trades_total: Figure;
  table: FillTable;
  boundary: HoldoutBoundaryView;
  note: string;
}

export interface ObjectiveDefView {
  key: string;
  label: string;
  unit: string;
  definition: string;
  neutral: Figure;
}

export interface RankingRowView {
  rank: number;
  run: BacktestRunRow;
  trades: Figure;
  raw: Figure;
  score: Figure;
  penalty_factor: Figure;
  penalised: boolean;
  basis: string;
  holdout_excluded: Figure;
}

export interface RankingView {
  objective: string;
  objectives: ObjectiveDefView[];
  min_trades: number;
  rows: RankingRowView[];
  unranked: string[];
  penalty: string;
  note: string;
}

export interface GridPointView {
  run_id: string;
  params: Record<string, unknown>;
  objective: Figure;
  trades: Figure;
}

export interface OptimisationProfileView {
  strategy_id: string;
  objective: string;
  parameters: string[];
  points: GridPointView[];
  n_points: Figure;
  n_undefined: Figure;
  runs_unknown_params: Figure;
  peak: Figure;
  median: Figure;
  p25: Figure;
  p75: Figure;
  profitable_share: Figure;
  median_over_peak: Figure;
  peak_params: Record<string, unknown>;
  method: string;
}

export type RerunKind = "warmup" | "lookahead" | "execution" | "perturbation" | "random" | "noise";
export type RerunStatus = "done" | "pending" | "running" | "failed" | "dead_letter" | "not_run";

export interface RerunStatusView {
  kind: string;
  title: string;
  status: string;
  cache_key: string;
  job_id: string;
  error: string;
  progress: Figure;
  stage: string;
  updated_at: string | null;
  computed_at: string | null;
  options: Record<string, unknown>;
}

export interface RobustnessIndexView {
  run_id: string;
  rerunnable: boolean;
  reason: string;
  binding: Record<string, unknown>;
  kinds: RerunStatusView[];
  can_queue_note: string;
}

export interface RerunProvenanceView {
  window_start: string | null;
  window_end: string | null;
  window_bars: Figure;
  boundary: Record<string, unknown>;
  dataset_version_id: string;
  engine: Record<string, unknown>;
  engine_runs: Figure;
  covers_stored_run: boolean;
  reproduces_stored: boolean | null;
}

export interface IndicatorDriftView {
  column: string;
  max_abs_diff: Figure;
  max_rel_diff: Figure;
  bars_differing: Figure;
  last_differing_bar: string | null;
}

export interface WarmupRunView {
  mode: string;
  compared_from: string | null;
  extra_bars: Figure;
  signals_compared: Figure;
  signals_changed_after_warmup: Figure;
  first_changed_signal: string | null;
  signals_changed_in_cold_region: Figure;
  indicator_columns: IndicatorDriftView[];
  trades_short: Figure;
  trades_long: Figure;
  trades_matched: Figure;
  trades_only_short: Figure;
  trades_only_long: Figure;
  trades_changed: Figure;
  metrics: RunMetricsView;
  indicators_converged: boolean;
  stable: boolean;
}

export interface WarmupView {
  warmup_period: Figure;
  prologue_available: Figure;
  stable: boolean;
  baseline: RerunBriefView;
  runs: WarmupRunView[];
}

export interface LookaheadFlagView {
  bar: string;
  signal_full: string | null;
  signal_truncated: string | null;
  signal_changed: boolean;
  columns_changed: string[];
}

export interface LookaheadView {
  samples: Figure;
  seed: number;
  signals_at_samples: Figure;
  columns_checked: string[];
  flags: LookaheadFlagView[];
  biased: boolean;
  signal_flags: Figure;
}

export interface StressRowView {
  stress: string;
  level: string;
  runs: Figure;
  profile: string;
  net_profit: DistributionView;
  sharpe: DistributionView;
  max_drawdown_pct: DistributionView;
  trades: DistributionView;
  retention: Figure;
  profitable_share: Figure;
}

export interface StressCellView {
  spread_x: string;
  slippage_x: string;
  profile: string;
  metrics: RunMetricsView;
  retention: Figure;
}

export interface ExecutionStressView {
  baseline: RerunBriefView;
  rows: StressRowView[];
  grid: StressCellView[];
  multipliers: string[];
  seed: number;
  notes: string[];
}

export interface PerturbationDrawView {
  params: Record<string, unknown>;
  valid: boolean;
  error: string;
  net_profit: Figure;
  sharpe: Figure;
  trades: Figure;
}

export interface PerturbationView {
  jitter_pct: Figure;
  seed: number;
  parameters: string[];
  baseline: RerunBriefView;
  draws: PerturbationDrawView[];
  invalid: Figure;
  net_profit: DistributionView;
  sharpe: DistributionView;
  profitable_share: Figure;
  baseline_percentile: Figure;
}

export interface RandomBenchmarkView {
  runs: Figure;
  seed: number;
  trades: Figure;
  strategy_total_return: Figure;
  strategy_mean_return: Figure;
  random_total_return: DistributionView;
  percentile: Figure;
  holding_bars: DistributionView;
  long_share: Figure;
  baseline: RerunBriefView;
}

export interface NoiseRowView {
  atr_fraction: string;
  runs: Figure;
  net_profit: DistributionView;
  sharpe: DistributionView;
  trades: DistributionView;
  degradation: Figure;
  profitable_share: Figure;
}

export interface NoiseView {
  baseline: RerunBriefView;
  rows: NoiseRowView[];
  seed: number;
  atr_period: Figure;
}

export interface RerunResultView {
  run_id: string;
  status: RerunStatusView;
  provenance: RerunProvenanceView | null;
  method: string;
  warmup?: WarmupView | null;
  lookahead?: LookaheadView | null;
  execution?: ExecutionStressView | null;
  perturbation?: PerturbationView | null;
  random?: RandomBenchmarkView | null;
  noise?: NoiseView | null;
}

export const robustnessPath = {
  index: (runId: string) => `/api/research/runs/${encodeURIComponent(runId)}/robustness`,
  ledger: (runId: string, what: "bands" | "breakdowns" | "consistency" | "fills", query = "") =>
    `/api/research/runs/${encodeURIComponent(runId)}/robustness/${what}${query ? `?${query}` : ""}`,
  rerun: (runId: string, kind: RerunKind) =>
    `/api/research/runs/${encodeURIComponent(runId)}/robustness/${kind}`,
  profile: (strategyId: string, objective: string) =>
    `/api/research/strategies/${encodeURIComponent(strategyId)}/optimisation-profile?objective=${encodeURIComponent(objective)}`,
  rankings: (strategyId: string, objective: string) =>
    `/api/research/rankings?strategy_id=${encodeURIComponent(strategyId)}&objective=${encodeURIComponent(objective)}`,
};
