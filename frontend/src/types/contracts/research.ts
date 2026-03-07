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

export interface ResearchRunSummary {
  run_id: string;
  total_combinations: number;
  completed: number;
  top_result: ResearchResult | null;
}
