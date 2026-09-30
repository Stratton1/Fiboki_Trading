import type { components } from "./generated/openapi";

/** Generated from the API's OpenAPI schema (npm run gen:api). */
type Schemas = components["schemas"];

/**
 * Mirrors of the API's pydantic response models.
 *
 * Wave 2: being replaced by generated types, progressively. A type below that
 * is an alias of `Schemas[...]` is generated; a hand-written interface is
 * checked field-for-field against its schema by lib/api-contract.ts.
 *
 * `Figure` is the single most important type in this file. Every number the API
 * returns arrives inside one, carrying the provenance that says where it came
 * from. The UI never invents a provenance from the page it is on — that was
 * exactly the V1 failure where a page titled "Paper / Backtest" contained only
 * backtest rows.
 */

export const PROVENANCES = [
  "backtest",
  "walkforward",
  "out_of_sample",
  "holdout",
  "paper",
  "shadow",
  "broker_demo",
  "broker_live",
] as const;

export type Provenance = (typeof PROVENANCES)[number];

export type ExecutionMode = "backtest" | "paper" | "shadow" | "demo" | "live";

export interface Caveat {
  code: string;
  severity: "info" | "warning" | "critical";
  message: string;
  affects: string;
  direction: "optimistic" | "pessimistic" | "unknown";
}

/** A number, or an explicit absence. `value: null` is a state, not a zero. */
export interface Figure {
  value: number | null;
  provenance: Provenance;
  unit: string;
  as_of: string | null;
  sample_size: number | null;
  caveats: Caveat[];
  estimated: boolean;
}

export interface SeriesPoint {
  t: string;
  v: number | null;
}

export interface Series {
  name: string;
  provenance: Provenance;
  unit: string;
  points: SeriesPoint[];
  caveats: Caveat[];
}

export interface SourceNote {
  kind: "live" | "seed" | "absent" | "mixed";
  detail: string;
  as_of: string | null;
}

export interface Envelope<T> {
  data: T;
  source: SourceNote;
  caveats: Caveat[];
}

export interface Page<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
  source: SourceNote;
  caveats: Caveat[];
}

export interface ApiErrorBody {
  code: string;
  detail: string;
  correlation_id: string;
  context: Record<string, unknown>;
}

// ----------------------------------------------------------------- system

export interface HealthCheck {
  name: string;
  status: "ok" | "degraded" | "down";
  detail: string;
  critical: boolean;
  latency_ms: number | null;
}

export interface HealthReport {
  status: "ok" | "degraded" | "down";
  checked_at: string;
  build_sha: string | null;
  build_time: string | null;
  migration_revision: string | null;
  execution_mode: string;
  worker_heartbeat_age_seconds: number | null;
  uptime_seconds: number;
  python_version: string;
  checks: HealthCheck[];
  advisory: string;
}

export interface ExecutionModeBanner {
  mode: ExecutionMode;
  provenance: Provenance;
  touches_broker: boolean;
  touches_real_money: boolean;
  severity: "info" | "caution" | "danger";
  headline: string;
  detail: string;
  live_execution_compiled_in: boolean;
  kill_switch_active: boolean;
  kill_switch_mode: string | null;
  as_of: string;
}

export type KillSwitchView = Schemas["KillSwitchView"];

export interface ServiceRow {
  name: string;
  kind: string;
  detail: string;
  healthy: boolean;
  latency: Figure;
}

export interface WorkerRow {
  name: string;
  state: "running" | "stale" | "never_started" | "stopped";
  heartbeat_age: Figure;
  detail: string;
}

export type BrokerHealthView = Schemas["BrokerHealthView"];

export interface SettingsView {
  execution_mode: string;
  live_execution_compiled_in: boolean;
  allowed_origin_count: number;
  cookie_secure: boolean;
  cookie_samesite: string;
  session_ttl_seconds: number;
  limits_version: string;
  limits: Record<string, unknown>;
  realism_models: Record<string, string>;
  data_root_configured: boolean;
  experiment_db_configured: boolean;
  build_sha: string | null;
}

export interface QueueRow {
  name: string;
  depth: number | null;
  available: boolean;
  detail: string;
}

// ---------------------------------------------------------------- trading

export interface TradeRow {
  trade_id: string;
  strategy_id: string;
  instrument: string;
  direction: string;
  /** Provenance is a COLUMN. Never a tab, never a page title. */
  provenance: Provenance;
  entry_time: string;
  exit_time: string;
  exit_reason: string;
  size: Figure;
  entry_price: Figure;
  exit_price: Figure;
  gross_pnl: Figure;
  costs: Figure;
  net_pnl: Figure;
  r_multiple: Figure;
}

export interface PositionRow {
  position_id: string;
  strategy_id: string;
  instrument: string;
  direction: string;
  provenance: Provenance;
  entry_time: string;
  size: Figure;
  entry_price: Figure;
  mark_price: Figure;
  stop_loss: Figure;
  take_profit: Figure;
  unrealised_pnl: Figure;
  distance_to_stop_pct: Figure;
}

export interface CandidateRow {
  strategy_id: string;
  name: string;
  family: string;
  lifecycle: string;
  content_hash: string;
  trades: Figure;
  win_rate: Figure;
  expectancy_r: Figure;
  net_pnl: Figure;
  sharpe: Figure;
  max_drawdown_pct: Figure;
  eligible_for_ranking: boolean;
  blocking_reasons: string[];
  next_action: string;
  next_action_requires_role: string;
}

/**
 * GET /api/trading/candidates/{id}/promote/preflight. Everything the promote
 * dialog shows comes from here: the caveats the operator must tick one by one,
 * and the consequences of each target, keyed by the value the POST accepts.
 */
export interface PromotePreflightView {
  strategy_id: string;
  execution_mode: string;
  eligible: boolean;
  blocking_reasons: string[];
  caveats: Caveat[];
  consequences: Record<string, string[]>;
}

/** GET /api/trading/preflight/kill-switch-disarm, for the lift-the-halt dialog. */
export type KillSwitchDisarmPreflightView = Schemas["KillSwitchDisarmPreflightView"];

export interface PortfolioView {
  balance: Figure;
  equity: Figure;
  realised_pnl: Figure;
  unrealised_pnl: Figure;
  open_positions: Figure;
  max_drawdown_pct: Figure;
  provenance_mix: Record<string, number>;
  equity_curve: Series;
}

export interface ExposureRow {
  key: string;
  label: string;
  exposure_pct: Figure;
  limit_pct: Figure;
  utilisation_pct: Figure;
  breached: boolean;
}

export interface RiskStateView {
  limits_version: string;
  kill_switch_active: boolean;
  kill_switch_mode: string | null;
  new_risk_permitted: boolean;
  new_risk_reason: string;
  closing_permitted: boolean;
  daily_loss_pct: Figure;
  max_daily_loss_pct: Figure;
  drawdown_pct: Figure;
  max_drawdown_limit_pct: Figure;
  margin_utilisation_pct: Figure;
  breaches: string[];
}

export interface TelemetryRow {
  order_id: string;
  instrument: string;
  mode: string;
  provenance: Provenance;
  requested_price: Figure;
  filled_price: Figure;
  slippage: Figure;
  latency_ms: Figure;
  outcome: string;
}

// --------------------------------------------------------------- research

export interface StrategyRow {
  strategy_id: string;
  name: string;
  family: string;
  author: string;
  hypothesis: string;
  timeframes: string[];
  universe: string[];
  content_hash: string;
  schema_version: string;
  complexity: Figure;
  parameter_count: Figure;
  rule_count: Figure;
  parent_strategy_ids: string[];
  notes: string;
}

export interface HypothesisRow {
  strategy_id: string;
  statement: string;
  family: string;
  structural_keywords: string[];
  tested: boolean;
  supporting_experiments: Figure;
}

export interface ExperimentRow {
  experiment_id: string;
  strategy_id: string;
  actor: string;
  actor_kind: string;
  outcome: string;
  created_at: string | null;
  hypothesis: string;
  dataset_version_id: string;
  verdict: string;
}

export interface ValidationRow {
  strategy_id: string;
  verdict: string;
  report_version: string;
  dataset_version_id: string;
  gate_set_version: string;
  rungs_passed: Figure;
  rungs_total: Figure;
  binding_constraint: string;
  provenance_labels: Record<string, string>;
  available: boolean;
  detail: string;
}

/**
 * GET /api/trading/lifecycle/strategies/{content_hash} (routers/lifecycle.py
 * StrategyStatusView): where one strategy stands on the lifecycle ladder.
 * `health` is 1.0 by construction when nothing has been evaluated; read it
 * only beside `ever_evaluated`.
 */
export interface LifecycleStatusView {
  strategy_id: string;
  strategy_content_hash: string;
  lifecycle: string;
  band: string;
  degraded: boolean;
  ever_evaluated: boolean;
  entered_state_at: string;
  last_evaluated_at: string | null;
  latched_halts: string[];
  missing_rule_registrations: string[];
  health: Figure;
  last_score: Figure;
  last_confidence: Figure;
  n_transitions: Figure;
}

/** GET /api/trading/lifecycle/strategies/{hash}/evaluation: the latest monitoring tick. */
export interface LifecycleRuleView {
  kind: string;
  fired: boolean;
  registration_id: string;
  statistic: Figure;
  threshold: Figure;
  detail: string;
}

export interface LifecycleDivergenceView {
  dimension: string;
  diverged: boolean;
  expected: Figure;
  observed: Figure;
}

export interface LifecycleEvaluationView {
  strategy_content_hash: string;
  at: string;
  state_before: string;
  state_after: string;
  demoted: boolean;
  summary: string;
  score: Figure;
  confidence: Figure;
  rule_evaluations: LifecycleRuleView[];
  divergence: LifecycleDivergenceView[];
  latched_halts: string[];
  notes: string[];
}

/**
 * GET /api/system/kill-switch/history: one kill-switch journal event (the
 * route returns Page[dict], so this has no generated schema). `action` is the
 * journal's `activate` or `deactivate`; `mode` is `pause` or `flatten` on an
 * activation.
 */
export interface KillSwitchEvent {
  action: string;
  mode: string | null;
  operator: string;
  reason: string;
  at: string;
  positions_open: number | null;
}

export interface ParameterRow {
  name: string;
  current: Figure;
  minimum: Figure;
  maximum: Figure;
  step: Figure;
  kind: string;
  description: string;
}

export interface ParameterLabView {
  strategy_id: string;
  parameters: ParameterRow[];
  search_space_size: Figure;
  deflation_warning: string;
}

export interface DatasetRow {
  version_id: string;
  short_id: string;
  describe: string;
}

// ---------------------------------------------------------------- markets

export interface InstrumentRow {
  symbol: string;
  asset_class: string;
  base: string;
  quote: string;
  trading_hours: string;
  pip_size: Figure;
  contract_size: Figure;
  min_size: Figure;
  size_step: Figure;
  typical_spread_pips: Figure;
  retail_leverage: Figure;
  annual_financing_bps: Figure;
}

export interface RegimeRow {
  instrument: string;
  available: boolean;
  detail: string;
  volatility: string | null;
  direction: string | null;
  liquidity: string | null;
  stress: string | null;
  persistence: string | null;
}

export interface DataQualityRow {
  instrument: string;
  available: boolean;
  quality: string;
  detail: string;
  bars: Figure;
  gaps: Figure;
  stale_runs: Figure;
}

export interface CorrelationView {
  instruments: string[];
  matrix: number[][];
  window_bars: number | null;
  available: boolean;
}

// ------------------------------------------------- markets: chart workstation

/**
 * One bar as `GET /api/markets/bars/{symbol}` serialises it (routers/markets.py
 * `bars`): open time `t` (ISO, UTC) and raw OHLC floats. The route's response
 * model is `Envelope[dict]`, so the OpenAPI schema says nothing about these
 * fields and lib/api-contract.ts cannot check them; this mirror is read from
 * the Python. Bars are market data, not a result, so they carry no per-value
 * provenance: the envelope's SourceNote is their label.
 *
 * `v` is present only when the dataset carries volume (`volume_kind`), and is
 * null for a bar whose volume is missing. The API sends no "forming" flag:
 * every bar it serves is treated as closed, and none is drawn as forming.
 */
export interface Bar {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v?: number | null;
}

/** `volume` is exchange volume, `tick_volume` a count of price updates, null neither. */
export type VolumeKind = "volume" | "tick_volume";

export interface BarsView {
  symbol: string;
  timeframe: string;
  dataset_version_id: string;
  volume_kind: VolumeKind | null;
  bars: Bar[];
}

/** Where an overlay item came from (bar_store, marketstate, paper_journal, …). */
export interface OverlaySource {
  kind: string;
  detail: string;
}

/** A gateway attempt from a paper session's telemetry, at its decision time. */
export interface SignalOverlay {
  kind: string;
  t: string;
  side: "long" | "short" | "unknown";
  strategy_id: string;
  outcome: "accepted" | "blocked";
  reason: string;
  signal_id: string;
  session_id: string;
  timeframe: string;
  requested_price: Figure;
  provenance: Provenance;
  dataset_version_id: string | null;
  source: OverlaySource;
}

export interface FillOverlay {
  kind: string;
  t: string;
  role: "entry" | "exit";
  side: "buy" | "sell";
  price: Figure;
  trade_id: string;
  strategy_id: string;
  session_id: string;
  exit_reason: string | null;
  net_pnl: Figure | null;
  provenance: Provenance;
  dataset_version_id: string | null;
  source: OverlaySource;
}

export interface LevelOverlay {
  kind: string;
  role: "entry" | "stop" | "target";
  price: Figure;
  /** Serialised by alias: the Python field is `from_`. */
  from: string;
  to: string | null;
  position_id: string;
  strategy_id: string;
  provenance: Provenance;
  dataset_version_id: string | null;
  source: OverlaySource;
}

/**
 * A run of one regime key. `to` is the first bar of the NEXT run for every
 * segment but the last, whose `to` is the window's last bar (routers/markets.py
 * `overlays`). Market state, not a result: `provenance` is null.
 */
export interface RegimeOverlay {
  kind: string;
  from: string;
  to: string;
  label: "trend" | "range" | "stress" | "unknown";
  regime_key: string;
  axes: Record<string, string>;
  provenance?: Provenance | null;
  dataset_version_id: string | null;
  classifier_fingerprint: string;
  source: OverlaySource;
}

/**
 * One output column of a backend indicator (`fiboki.indicators`). `pane` is
 * "price" (its values are prices), "state:<indicator>" (a state or ratio
 * column of a price indicator) or the indicator's own name (its own pane).
 * The API supplies no colour or style; the chart assigns neutral series
 * roles by order. `display_only` series are NOT causal (the displayed chikou
 * span) and are never read by a strategy.
 */
export interface SeriesOverlay {
  kind: string;
  pane: string;
  name: string;
  indicator_id: string;
  indicator_key: string;
  params: Record<string, unknown>;
  display_only: boolean;
  points: SeriesPoint[];
  provenance?: Provenance | null;
  dataset_version_id: string | null;
  source: OverlaySource;
}

export interface EventOverlay {
  kind: string;
  t: string;
  window_end: string | null;
  time_known: boolean;
  currency: string;
  name: string;
  impact: string;
  event_id: string;
  source_url: string;
  provenance?: Provenance | null;
  dataset_version_id: string | null;
  source: OverlaySource;
}

export interface HeadlineOverlay {
  kind: string;
  t: string;
  vendor_published_at: string | null;
  news_source: string;
  currency: string | null;
  title: string;
  url: string;
  provenance?: Provenance | null;
  dataset_version_id: string | null;
  source: OverlaySource;
}

export interface SectionStatus {
  available: boolean;
  detail: string;
}

/** GET /api/markets/overlays/{symbol} (Envelope). Everything the chart draws over candles. */
export interface OverlayView {
  symbol: string;
  timeframe: string;
  window_from: string | null;
  window_to: string | null;
  bars_dataset_version_id: string | null;
  signals: SignalOverlay[];
  fills: FillOverlay[];
  levels: LevelOverlay[];
  regimes: RegimeOverlay[];
  series: SeriesOverlay[];
  events: EventOverlay[];
  headlines: HeadlineOverlay[];
  /** Per section: available, and why not when it is not. */
  sections: Record<string, SectionStatus>;
}

// ----------------------------------------------------------- intelligence

export type AgentRoleRow = Schemas["AgentRoleView"];

export type AuditEntryRow = Schemas["AuditEntryView"];

export interface AuditIntegrityView {
  intact: boolean;
  entries: Figure;
  first_broken_sequence: number;
  detail: string;
}

export interface ResearchMemoryView {
  available: boolean;
  detail: string;
  structures_recorded: Figure;
  rediscovery_rate: Figure;
}

export type PrincipalView = Schemas["PrincipalView"];

// ---------------------------------------------------------------- command
//
// Hand-written from the backend's in-progress routers (src/fiboki/api/
// routers/command.py and incidents.py, not yet in the OpenAPI snapshot).
// Replace with generated aliases once `npm run gen:api` reads a schema that
// has them.

export type Severity = "info" | "warning" | "error" | "critical";

/**
 * GET /api/command/attention: one item in the server-ranked attention queue.
 * The array order IS the ranking; the workstation never re-sorts it.
 */
export interface AttentionItem {
  id: string;
  category: string;
  severity: Severity;
  title: string;
  reason: string;
  /** An in-app path to the screen that resolves it. */
  deep_link: string;
  as_of: string | null;
  /** The server's ranking score, labelled like every number. */
  score: Figure;
}

export type IncidentStatus = "open" | "acknowledged" | "resolved";

export interface IncidentTimelineEntry {
  at: string;
  kind: "occurrence" | "resolved" | "ack" | "note";
  severity: string | null;
  actor: string;
  text: string;
  correlation_id: string;
}

/** GET /api/system/incidents (Page) and the `incidents` stream topic (entities keyed by `id`). */
export interface IncidentRow {
  id: string;
  key: string;
  event: string;
  source: string;
  title: string;
  severity: Severity;
  status: IncidentStatus;
  first_seen: string;
  last_seen: string;
  occurrences: Figure;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  resolved_at: string | null;
  deep_link: string;
  as_of: string;
  timeline: IncidentTimelineEntry[];
}
