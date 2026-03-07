export interface EquityCurvePoint {
  bar_index: number;
  equity: number;
}

export interface BacktestSummary {
  id: number;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  start_date: string | null;
  end_date: string | null;
  total_trades: number;
  net_profit: number;
  sharpe_ratio: number | null;
  max_drawdown_pct: number | null;
}

export interface BacktestDetail extends BacktestSummary {
  config_json: Record<string, unknown> | null;
  metrics_json: Record<string, unknown> | null;
}
