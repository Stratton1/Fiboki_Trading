export interface CandleBar {
  timestamp: number; // Unix ms
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IchimokuPoint {
  timestamp: number;
  tenkan: number | null;
  kijun: number | null;
  senkou_a: number | null;
  senkou_b: number | null;
  chikou: number | null;
}

export interface FibLevel {
  level: number; // 0.236, 0.382, 0.5, 0.618, 0.786, 1.0
  price: number;
  label: string;
}

export interface MarketDataResponse {
  instrument: string;
  timeframe: string;
  candles: CandleBar[];
  ichimoku: IchimokuPoint[];
}
