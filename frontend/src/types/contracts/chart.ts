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
  total_bars: number;
  from_date: string | null;
  to_date: string | null;
  source: string | null;
  mode: "historical" | "live";
}

export interface LiveStatusResponse {
  available: boolean;
  reason: string | null;
}

export interface ManifestDataset {
  provider: string;
  symbol: string;
  timeframe: string;
  bars: number;
  from_date: string;
  to_date: string;
  file_path: string;
  size_bytes: number;
  modified_at: string;
}

export interface DataManifest {
  generated_at: string;
  canonical_dir: string;
  datasets: ManifestDataset[];
}

export interface DataAvailability {
  available: boolean;
  rows: number;
  start?: string;
  end?: string;
}
