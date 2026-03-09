export interface ResearchResult {
  id: number;
  run_id: string;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  composite_score: number;
  rank: number;
  metrics_json: Record<string, unknown> | null;
  created_at: string | null;
}

export interface ScoringWeights {
  weight_risk_adjusted: number;
  weight_profit_factor: number;
  weight_return: number;
  weight_drawdown: number;
  weight_sample: number;
  weight_stability: number;
}

export interface ResearchRunSummary {
  run_id: string;
  total_combinations: number;
  completed: number;
  qualified: number;
  min_trades: number;
  scoring_weights: ScoringWeights | null;
  top_result: ResearchResult | null;
}

// Advanced research types

export interface WalkForwardWindow {
  window_index: number;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  train_bars: number;
  test_bars: number;
  train_trades: number;
  test_trades: number;
  train_score: number;
  test_score: number;
  train_net_profit: number;
  test_net_profit: number;
}

export interface WalkForwardResponse {
  strategy_id: string;
  instrument: string;
  timeframe: string;
  total_windows: number;
  avg_test_score: number;
  avg_test_sharpe: number;
  total_test_trades: number;
  score_degradation: number;
  windows: WalkForwardWindow[];
  status: string;
}

export interface OOSResponse {
  strategy_id: string;
  instrument: string;
  timeframe: string;
  split_ratio: number;
  in_sample_bars: number;
  out_of_sample_bars: number;
  is_trades: number;
  is_score: number;
  is_sharpe: number;
  is_net_profit: number;
  oos_trades: number;
  oos_score: number;
  oos_sharpe: number;
  oos_net_profit: number;
  score_degradation: number;
  robust: boolean;
  status: string;
}

export interface MonteCarloResponse {
  strategy_id: string;
  instrument: string;
  timeframe: string;
  num_simulations: number;
  num_trades: number;
  original_net_profit: number;
  mean_net_profit: number;
  median_net_profit: number;
  p5_net_profit: number;
  p95_net_profit: number;
  mean_max_drawdown: number;
  p95_max_drawdown: number;
  profit_probability: number;
  ruin_probability: number;
  robust: boolean;
  status: string;
}

export interface SensitivityPoint {
  param_value: number;
  total_trades: number;
  net_profit: number;
  sharpe_ratio: number;
  composite_score: number;
}

export interface SensitivityResponse {
  strategy_id: string;
  instrument: string;
  timeframe: string;
  param_name: string;
  baseline_value: number;
  score_range: number;
  score_std: number;
  robust: boolean;
  variations: SensitivityPoint[];
  status: string;
}

export interface AdvancedResearchResponse {
  walk_forward: WalkForwardResponse | null;
  oos: OOSResponse | null;
  monte_carlo: MonteCarloResponse | null;
  sensitivity: SensitivityResponse[] | null;
}

// Validation types

export interface ValidationItemRequest {
  strategy_id: string;
  instrument: string;
  timeframe: string;
  original_score: number;
  original_trades?: number;
  original_net_profit?: number;
  original_sharpe?: number;
}

export interface ValidationResultResponse {
  strategy_id: string;
  instrument: string;
  timeframe: string;
  original_score: number;
  validation_score: number;
  score_divergence: number;
  passed: boolean;
  validation_status: string;
  validation_provider: string | null;
}

export interface ValidationBatchResponse {
  total_validated: number;
  total_passed: number;
  total_failed: number;
  total_skipped: number;
  pass_rate: number;
  results: ValidationResultResponse[];
}
