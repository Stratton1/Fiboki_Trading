/**
 * Compile-time drift checks for the research analytics mirrors (lib/research.ts)
 * against the generated OpenAPI types, the same way lib/api-contract.ts does
 * for the core mirrors. Nothing imports this module; `npm run typecheck`
 * evaluates it.
 */
import type { components } from "./generated/openapi";
import type * as R from "./research";

type S = components["schemas"];

type SameKeys<A, B> = [Exclude<keyof A, keyof B>, Exclude<keyof B, keyof A>] extends [never, never]
  ? true
  : { onlyLocal: Exclude<keyof A, keyof B>; onlySchema: Exclude<keyof B, keyof A> };

type Assert<T extends true> = T;

export type ResearchChecks = [
  Assert<SameKeys<R.DefinitionView, S["DefinitionView"]>>,
  Assert<SameKeys<R.GateCheckView, S["GateCheckView"]>>,
  Assert<SameKeys<R.GateReportView, S["GateReportView"]>>,
  Assert<SameKeys<R.StatView, S["StatView"]>>,
  Assert<SameKeys<R.DrawdownPeriodView, S["DrawdownPeriodView"]>>,
  Assert<SameKeys<R.MonthlyCellView, S["MonthlyCellView"]>>,
  Assert<SameKeys<R.YearReturnView, S["YearReturnView"]>>,
  Assert<SameKeys<R.HistogramView, S["HistogramView"]>>,
  Assert<SameKeys<R.ExcursionTable, S["ExcursionTable"]>>,
  Assert<SameKeys<R.InstrumentBreakdownView, S["InstrumentBreakdownView"]>>,
  Assert<SameKeys<R.ProvenanceBlock, S["ProvenanceBlock"]>>,
  Assert<SameKeys<R.TearsheetView, S["TearsheetView"]>>,
  Assert<SameKeys<R.RunSummaryView, S["RunSummaryView"]>>,
  Assert<SameKeys<R.ComparedRunView, S["ComparedRunView"]>>,
  Assert<SameKeys<R.CorrelationCellView, S["CorrelationCellView"]>>,
  Assert<SameKeys<R.ComparisonView, S["ComparisonView"]>>,
  Assert<SameKeys<R.MonteCarloView, S["MonteCarloView"]>>,
  Assert<SameKeys<R.RungView, S["RungView"]>>,
  Assert<SameKeys<R.LadderEntryView, S["LadderEntryView"]>>,
  Assert<SameKeys<R.LifecycleBrief, S["LifecycleBrief"]>>,
  Assert<SameKeys<R.StrategyOverviewView, S["StrategyOverviewView"]>>,
  Assert<SameKeys<R.FoldView, S["FoldView"]>>,
  Assert<SameKeys<R.WalkForwardView, S["WalkForwardView"]>>,
  Assert<SameKeys<R.SurfaceCellView, S["SurfaceCellView"]>>,
  Assert<SameKeys<R.PlateauGateView, S["PlateauGateView"]>>,
  Assert<SameKeys<R.ParameterSurfaceView, S["ParameterSurfaceView"]>>,
  Assert<SameKeys<R.JobView, S["JobView"]>>,
  Assert<SameKeys<R.ShardView, S["ShardView"]>>,
  Assert<SameKeys<R.JobQueueView, S["JobQueueView"]>>,
  Assert<SameKeys<R.CorrelationMatrixView, S["CorrelationMatrixView"]>>,
];
