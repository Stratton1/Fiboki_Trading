export interface DrawingPoint {
  timestamp: number;
  value: number;
}

export interface ChartDrawing {
  id: number;
  instrument: string;
  timeframe: string;
  tool_type: string;
  points: DrawingPoint[];
  styles: Record<string, unknown> | null;
  lock: boolean;
  visible: boolean;
  created_at: string;
  updated_at: string;
}

export interface DrawingCreate {
  instrument: string;
  timeframe: string;
  tool_type: string;
  points: DrawingPoint[];
  styles?: Record<string, unknown> | null;
  lock?: boolean;
  visible?: boolean;
}

export interface DrawingUpdate {
  points?: DrawingPoint[];
  styles?: Record<string, unknown> | null;
  lock?: boolean;
  visible?: boolean;
}
