/**
 * Research analytics contracts (GET /api/research/runs, …/tearsheet,
 * …/monte-carlo, /compare, /strategies/{id}/overview, …/walk-forward,
 * /parameter-lab/{id}/surface, /jobs, /correlations).
 *
 * Hand-written mirrors of the API's response models in
 * src/fiboki/api/routers/research_analytics.py, using the authoritative local
 * Figure/Series/Caveat contract. lib/research-contract.ts holds the
 * compile-time key checks against the generated OpenAPI types, so a renamed
 * field fails `npm run typecheck`.
 *
 * Every number here was computed by the backend (research/tearsheet.py); the
 * workstation only draws it. Nothing in this module computes a statistic.
 */
import type { BacktestRunRow, Figure, Provenance, Series } from "./types";

export interface DefinitionView {
  label: string;
  definition: string;
  formula: string;
}

export interface GateCheckView {
  name: string;
  metric: string;
  /** pass | fail | not_evaluated | not_applicable */
  status: string;
  passed: boolean;
  value: Figure;
  threshold: number | null;
  comparison: string;
  rung: number | null;
  detail: string;
}

export interface GateReportView {
  report_id: string;
  backtest_id: string;
  created_at: string | null;
  verdict: string;
  kind: string;
  gate_set_version: string;
  gate_set_fingerprint: string;
  checks: GateCheckView[];
  caveats: string[];
  psr: Figure;
  deflated_sharpe: Figure;
  trial_count: Figure;
  trial_count_known: boolean;
}

export interface StatView {
  key: string;
  label: string;
  figure: Figure;
  definition: string;
  formula: string;
  check: GateCheckView | null;
  source: string;
}

export interface DrawdownPeriodView {
  rank: number;
  peak_at: string;
  trough_at: string;
  recovered_at: string | null;
  depth_pct: Figure;
  peak_equity: Figure;
  trough_equity: Figure;
  decline_days: Figure;
  recovery_days: Figure;
  length_days: Figure;
}

export interface MonthlyCellView {
  year: number;
  month: number;
  return_pct: Figure;
}

export interface YearReturnView {
  year: number;
  return_pct: Figure;
}

export interface HistogramView {
  basis: string;
  provenance: Provenance;
  unit: string;
  edges: number[];
  counts: number[];
  n: number;
  mean: Figure;
  median: Figure;
}

/** Per-trade columns (one entry per trade); provenance and units declared once. */
export interface ExcursionTable {
  provenance: Provenance;
  pnl_unit: string;
  r_unit: string;
  trade_id: string[];
  instrument: string[];
  direction: string[];
  entry_time: (string | null)[];
  exit_time: (string | null)[];
  exit_reason: string[];
  bars_held: (number | null)[];
  net_pnl: (number | null)[];
  r_multiple: (number | null)[];
  mae: (number | null)[];
  mfe: (number | null)[];
  mae_r: (number | null)[];
  mfe_r: (number | null)[];
}

export interface InstrumentBreakdownView {
  instrument: string;
  trades: Figure;
  net_pnl: Figure;
  win_rate_pct: Figure;
  profit_factor: Figure;
  expectancy: Figure;
  expectancy_r: Figure;
}

export interface ProvenanceBlock {
  run_id: string;
  strategy_id: string;
  strategy_hash: string;
  parameters: Record<string, unknown>;
  parameters_source: string;
  dataset_version_ids: string[];
  data_fingerprint: Record<string, unknown>;
  timeframe: string;
  instruments: string[];
  account_ccy: string;
  initial_balance: number | null;
  cost_profile: string;
  spread_model: string;
  slippage_model: string;
  commission_model: string;
  financing_model: string;
  sizing: Record<string, unknown>;
  exit_policy: Record<string, unknown>;
  intrabar_policy: string;
  calendar_version: string;
  calendar_note: string;
  gate_set_version: string;
  gate_set_fingerprint: string;
  engine_version: string;
  engine_current: boolean;
  ledger_sha256: string;
  job_id: string;
  run_created_at: string | null;
  tearsheet_version: string;
}

export interface TearsheetView {
  run: BacktestRunRow;
  account_ccy: string;
  stats: StatView[];
  equity: Series;
  benchmark: Series | null;
  benchmark_kind: string;
  benchmark_note: string;
  underwater: Series;
  drawdowns: DrawdownPeriodView[];
  monthly: MonthlyCellView[];
  yearly: YearReturnView[];
  trade_histogram: HistogramView;
  excursions: ExcursionTable;
  excursions_note: string;
  rolling_sharpe: Series;
  rolling_window_days: number;
  by_instrument: InstrumentBreakdownView[];
  pooled: boolean;
  gate_report: GateReportView | null;
  provenance: ProvenanceBlock;
  definitions: Record<string, DefinitionView>;
  chart_links: Record<string, string>;
  strategy_link: string;
}

export interface RunSummaryView {
  run: BacktestRunRow;
  total_return_pct: Figure;
  sharpe: Figure;
  max_drawdown_pct: Figure;
  profit_factor: Figure;
  tearsheet: string;
}

export interface ComparedRunView {
  run: BacktestRunRow;
  rebased: Series;
  stats: StatView[];
  daily_returns: number;
}

export interface CorrelationCellView {
  a: string;
  b: string;
  rho: Figure;
  n_common: number;
}

export interface ComparisonView {
  runs: ComparedRunView[];
  correlations: CorrelationCellView[];
  min_common_days: number;
  definitions: Record<string, DefinitionView>;
}

export interface MonteCarloView {
  run_id: string;
  basis: string;
  seed: number;
  n_paths: number;
  n_trades: number;
  block_length: Figure;
  risk_fraction: number;
  initial_equity: Figure;
  ruin_fraction: number;
  ruin_probability: Figure;
  provenance: Provenance;
  fan_steps: number[];
  fan: Record<string, number[]>;
  final_equity_percentiles: Record<string, Figure>;
  drawdown_percentiles: Record<string, Figure>;
  realised_max_drawdown_pct: Figure;
  realised_final_equity: Figure;
  account_ccy: string;
  method: string;
  notes: string[];
}

export interface RungView {
  index: number;
  name: string;
  outcome: string;
  reason: string;
}

export interface LadderEntryView {
  source: string;
  experiment_id: string | null;
  created_at: string | null;
  verdict: string;
  gate_set_version: string;
  report_version: string;
  dataset_version_id: string;
  strategy_content_hash: string;
  binding_constraint: string;
  trial_count: Figure;
  rungs: RungView[];
  has_walk_forward: boolean;
}

export interface LifecycleBrief {
  content_hash: string;
  state: string;
  degraded: boolean;
  link: string;
}

export interface StrategyParameter {
  kind: string;
  default: unknown;
  min: number | null;
  max: number | null;
  step: number | null;
}

export interface StrategyOverviewView {
  strategy_id: string;
  name: string;
  family: string;
  hypothesis: string;
  content_hash: string;
  timeframes: string[];
  universe: string[];
  parameters: Record<string, StrategyParameter>;
  runs: RunSummaryView[];
  runs_total: number;
  gate_reports: GateReportView[];
  ladder_history: LadderEntryView[];
  lifecycle: LifecycleBrief[];
  lifecycle_note: string;
  links: Record<string, string>;
}

export interface FoldView {
  fold: number;
  train_start: string | null;
  train_end: string | null;
  test_start: string | null;
  test_end: string | null;
  is_rate: Figure;
  oos_rate: Figure;
  is_net_profit: Figure;
  oos_net_profit: Figure;
  oos_trades: Figure;
  oos_sharpe: Figure;
  efficiency: Figure;
  fixed_param_oos_rate: Figure;
  selected_params: Record<string, unknown>;
}

export interface WalkForwardView {
  strategy_id: string;
  source: string;
  source_ref: string;
  scheme: string;
  outcome: string;
  reason: string;
  folds: FoldView[];
  n_folds: number;
  walk_forward_efficiency: Figure;
  oos_profitable_fraction: Figure;
  mean_is_rate: Figure;
  mean_oos_rate: Figure;
  oos_rate_sd: Figure;
  oos_rate_cv: Figure;
  parameter_repeats: number;
  parameter_stability_across_folds: unknown;
  gates: GateCheckView[];
  gate_set_version: string;
  rate_unit: string;
  fold_runs: BacktestRunRow[];
  available: string[];
  definitions: Record<string, DefinitionView>;
}

export interface SurfaceCellView {
  x: number;
  y: number;
  score: Figure;
  n_runs: number;
  run_ids: string[];
  point_plateau_ratio: Figure;
  neighbourhood_median_ratio: Figure;
  neighbourhood_min: Figure;
  on_plateau: boolean;
  isolated_peak: boolean;
  plateau_checks: Record<string, boolean>;
}

export interface PlateauGateView {
  name: string;
  metric: string;
  comparison: string;
  threshold: number;
  rationale: string;
}

export interface ParameterSurfaceView {
  strategy_id: string;
  parameters: string[];
  x: string;
  y: string;
  metric: string;
  metrics_available: string[];
  fixed: Record<string, unknown>;
  x_values: number[];
  y_values: number[];
  cells: SurfaceCellView[];
  radius: number;
  plateau_gates: PlateauGateView[];
  gate_set_version: string;
  runs_considered: number;
  runs_used: number;
  runs_other_params: number;
  runs_unknown_params: number;
  plateau_definition: string;
  notes: string[];
}

export interface JobView {
  job_id: string;
  job_type: string;
  queue: string;
  status: string;
  submitted_at: string | null;
  started_at: string | null;
  next_attempt_at: string | null;
  attempts: number;
  max_attempts: number;
  error: string;
  strategy_id: string;
  instrument: string;
  elapsed_seconds: Figure;
  eta_seconds: Figure;
  outputs: Record<string, string>;
}

export interface ShardView {
  study: string;
  stage: string;
  path: string;
  unit: string;
  done: number;
  total: number | null;
  partial: boolean;
  progress: Figure;
  seconds_per_unit: Figure;
  eta_seconds: Figure;
  updated_at: string | null;
  age_seconds: Figure;
  error: string;
}

export interface JobQueueView {
  counts: Record<string, number>;
  jobs: JobView[];
  shards: ShardView[];
  job_ledger: string;
  shards_root: string;
  eta_method: string;
}

export interface CorrelationMatrixView {
  timeframe: string;
  provenance: Provenance;
  window: number;
  windows: number[];
  timeframes: string[];
  instruments: string[];
  matrix: (number | null)[][];
  bars_used: number;
  first_common: string | null;
  last_common: string | null;
  dataset_version_ids: Record<string, string>;
  excluded: Record<string, string>;
  method: string;
}

/** Route builders: the one place research URLs are spelt. */
export const researchHref = {
  run: (runId: string) => `/research/runs/${encodeURIComponent(runId)}`,
  strategy: (id: string) => `/research/strategies/${encodeURIComponent(id)}`,
  compare: (runIds: string[]) => `/research/compare?runs=${runIds.map(encodeURIComponent).join(",")}`,
  walkForward: (id: string) => `/research/walk-forward?strategy=${encodeURIComponent(id)}`,
  chart: (symbol: string, runId: string) =>
    `/markets/${encodeURIComponent(symbol)}?run=${encodeURIComponent(runId)}`,
};
