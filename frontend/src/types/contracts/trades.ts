export interface PricePoint {
  timestamp: string;
  price: number;
}

export interface TradeMarker {
  trade_id: string;
  strategy_id: string;
  direction: "LONG" | "SHORT";
  entry: PricePoint;
  exit: PricePoint | null;
  stop_loss: PricePoint[];
  take_profit: PricePoint[];
  partial_exits: PricePoint[];
  label: string;
  outcome: string;
}

export interface StrategyAnnotation {
  type: string;
  timestamp: string;
  price: number;
  label: string;
  metadata?: Record<string, unknown>;
}

export interface ChartAnnotationsResponse {
  trade_markers: TradeMarker[];
  strategy_annotations: StrategyAnnotation[];
}

export interface Trade {
  id: number;
  strategy_id: string;
  instrument: string;
  direction: string;
  entry_time: string | null;
  entry_price: number;
  exit_time: string | null;
  exit_price: number;
  exit_reason: string;
  pnl: number;
  bars_in_trade: number;
  backtest_run_id: number;
}

export interface TradeListResponse {
  items: Trade[];
  total: number;
  page: number;
  size: number;
}
