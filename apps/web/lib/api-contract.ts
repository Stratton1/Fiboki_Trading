/**
 * Compile-time drift checks between the hand-written mirrors in lib/types.ts
 * and the types generated from the API's OpenAPI schema (npm run gen:api).
 *
 * Wave 2 replaces hand mirrors with generated types progressively (those
 * already replaced are aliases in lib/types.ts). For every mirror not yet
 * replaced, its field NAMES must equal the schema's: a field the API renamed,
 * added or removed fails `npm run typecheck` here instead of rendering as
 * "no data" at run time. Field types stay hand-written where the local type is
 * deliberately narrower (a status union where the schema says `string`).
 *
 * Figure, SourceNote, Caveat and Provenance are the authoritative LOCAL
 * contract; they are checked here, never replaced.
 *
 * Nothing imports this module, so it adds nothing to any bundle.
 */
import type { components } from "./generated/openapi";
import type * as T from "./types";

type S = components["schemas"];

/** true when A and B have exactly the same keys. */
type SameKeys<A, B> = [Exclude<keyof A, keyof B>, Exclude<keyof B, keyof A>] extends [never, never]
  ? true
  : { onlyLocal: Exclude<keyof A, keyof B>; onlySchema: Exclude<keyof B, keyof A> };

/** true when A and B are mutually assignable. */
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;

export type Checks = [
  SameKeys<T.Figure, S["Figure"]>,
  SameKeys<T.Caveat, S["Caveat"]>,
  SameKeys<T.SourceNote, S["SourceNote"]>,
  SameKeys<T.Series, S["Series"]>,
  SameKeys<T.SeriesPoint, S["SeriesPoint"]>,
  Same<T.Provenance, S["Provenance"]>,
  Same<T.ExecutionMode, S["ExecutionMode"]>,
  SameKeys<T.HealthCheck, S["HealthCheck"]>,
  SameKeys<T.HealthReport, S["HealthReport"]>,
  SameKeys<T.ExecutionModeBanner, S["ExecutionModeBanner"]>,
  SameKeys<T.ServiceRow, S["ServiceRow"]>,
  SameKeys<T.WorkerRow, S["WorkerRow"]>,
  SameKeys<T.SettingsView, S["SettingsView"]>,
  SameKeys<T.TradeRow, S["TradeRowView"]>,
  SameKeys<T.PositionRow, S["PositionRowView"]>,
  SameKeys<T.CandidateRow, S["CandidateView"]>,
  SameKeys<T.PromotePreflightView, S["PromotePreflightView"]>,
  SameKeys<T.PortfolioView, S["PortfolioView"]>,
  SameKeys<T.ExposureRow, S["ExposureRow"]>,
  SameKeys<T.RiskStateView, S["RiskStateView"]>,
  SameKeys<T.TelemetryRow, S["TelemetryRow"]>,
  SameKeys<T.StrategyRow, S["StrategyView"]>,
  SameKeys<T.HypothesisRow, S["HypothesisView"]>,
  SameKeys<T.ExperimentRow, S["ExperimentView"]>,
  SameKeys<T.ValidationRow, S["ValidationSummary"]>,
  SameKeys<T.ParameterRow, S["ParameterRow"]>,
  SameKeys<T.ParameterLabView, S["ParameterLabView"]>,
  SameKeys<T.InstrumentRow, S["InstrumentView"]>,
  SameKeys<T.RegimeRow, S["RegimeView"]>,
  SameKeys<T.DataQualityRow, S["DataQualityRow"]>,
  SameKeys<T.AuditIntegrityView, S["AuditIntegrityView"]>,
  SameKeys<T.ResearchMemoryView, S["ResearchMemoryView"]>,
  // Chart workstation (GET /api/markets/overlays/{symbol}). BarsView has no
  // schema to check against: the bars route's response model is Envelope[dict].
  SameKeys<T.OverlayView, S["OverlayView"]>,
  SameKeys<T.OverlaySource, S["OverlaySource"]>,
  SameKeys<T.SignalOverlay, S["SignalOverlay"]>,
  SameKeys<T.FillOverlay, S["FillOverlay"]>,
  SameKeys<T.LevelOverlay, S["LevelOverlay"]>,
  SameKeys<T.RegimeOverlay, S["RegimeOverlay"]>,
  SameKeys<T.SeriesOverlay, S["SeriesOverlay"]>,
  SameKeys<T.EventOverlay, S["EventOverlay"]>,
  SameKeys<T.HeadlineOverlay, S["HeadlineOverlay"]>,
  SameKeys<T.SectionStatus, S["SectionStatus"]>,
];

type AllTrue<L extends unknown[]> = L[number] extends true ? true : false;

/** Fails to compile, naming the offending keys, if any check above is not `true`. */
export const CONTRACT_HOLDS: AllTrue<Checks> = true;
