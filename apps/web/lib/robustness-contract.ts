/**
 * Compile-time drift checks for the robustness mirrors (lib/robustness.ts)
 * against the generated OpenAPI types, as lib/research-contract.ts does for the
 * research analytics mirrors. Nothing imports this module; `npm run typecheck`
 * evaluates it, so a renamed field fails the build.
 */
import type { components } from "./generated/openapi";
import type * as R from "./robustness";

type S = components["schemas"];

type SameKeys<A, B> = [Exclude<keyof A, keyof B>, Exclude<keyof B, keyof A>] extends [never, never]
  ? true
  : { onlyLocal: Exclude<keyof A, keyof B>; onlySchema: Exclude<keyof B, keyof A> };

type Assert<T extends true> = T;

export type RobustnessChecks = [
  Assert<SameKeys<R.HoldoutBoundaryView, S["HoldoutBoundaryView"]>>,
  Assert<SameKeys<R.DistributionView, S["DistributionView"]>>,
  Assert<SameKeys<R.RunMetricsView, S["RunMetricsView"]>>,
  Assert<SameKeys<R.RerunBriefView, S["RerunBriefView"]>>,
  Assert<SameKeys<R.BandView, S["BandView"]>>,
  Assert<SameKeys<R.ConfidenceBandsView, S["ConfidenceBandsView"]>>,
  Assert<SameKeys<R.BucketView, S["BucketView"]>>,
  Assert<SameKeys<R.BreakdownsView, S["BreakdownsView"]>>,
  Assert<SameKeys<R.SplitView, S["SplitView"]>>,
  Assert<SameKeys<R.ConsistencyView, S["ConsistencyView"]>>,
  Assert<SameKeys<R.FillTable, S["FillTable"]>>,
  Assert<SameKeys<R.FillDetailView, S["FillDetailView"]>>,
  Assert<SameKeys<R.ObjectiveDefView, S["ObjectiveDefView"]>>,
  Assert<SameKeys<R.RankingRowView, S["RankingRowView"]>>,
  Assert<SameKeys<R.RankingView, S["RankingView"]>>,
  Assert<SameKeys<R.GridPointView, S["GridPointView"]>>,
  Assert<SameKeys<R.OptimisationProfileView, S["OptimisationProfileView"]>>,
  Assert<SameKeys<R.RerunStatusView, S["RerunStatusView"]>>,
  Assert<SameKeys<R.RobustnessIndexView, S["RobustnessIndexView"]>>,
  Assert<SameKeys<R.RerunProvenanceView, S["RerunProvenanceView"]>>,
  Assert<SameKeys<R.IndicatorDriftView, S["IndicatorDriftView"]>>,
  Assert<SameKeys<R.WarmupRunView, S["WarmupRunView"]>>,
  Assert<SameKeys<R.WarmupView, S["WarmupView"]>>,
  Assert<SameKeys<R.LookaheadFlagView, S["LookaheadFlagView"]>>,
  Assert<SameKeys<R.LookaheadView, S["LookaheadView"]>>,
  Assert<SameKeys<R.StressRowView, S["StressRowView"]>>,
  Assert<SameKeys<R.StressCellView, S["StressCellView"]>>,
  Assert<SameKeys<R.ExecutionStressView, S["ExecutionStressView"]>>,
  Assert<SameKeys<R.PerturbationDrawView, S["PerturbationDrawView"]>>,
  Assert<SameKeys<R.PerturbationView, S["PerturbationView"]>>,
  Assert<SameKeys<R.RandomBenchmarkView, S["RandomBenchmarkView"]>>,
  Assert<SameKeys<R.NoiseRowView, S["NoiseRowView"]>>,
  Assert<SameKeys<R.NoiseView, S["NoiseView"]>>,
  Assert<SameKeys<R.RerunResultView, S["RerunResultView"]>>,
];
