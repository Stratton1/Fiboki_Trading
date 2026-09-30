"use client";

import type { ReactNode } from "react";
import { isStale } from "@/lib/freshness";
import {
  CRITICAL_AT_PCT,
  FAMILIES,
  WARN_AT_PCT,
  closestToLimit,
  exposureRows,
  groupFamilies,
  riskRows,
  unreportedRows,
  type FamilyGroup,
  type FamilyId,
  type LimitRow,
} from "@/lib/limits";
import { deriveProvenance } from "@/lib/provenance";
import type { ApiHandle } from "@/lib/query";
import type { Envelope, ExposureRow, Page, Provenance, RiskStateView } from "@/lib/types";
import { ProvenanceChip, ProvenanceLabelChip } from "../ProvenanceChip";
import { Button } from "../ui/Button";
import { StaleBadge } from "../ui/StaleBadge";
import { ViewStateTag } from "../ui/ViewStateTag";
import { DerivedNote, LimitRowItem, LimitStateTag, pct } from "./LimitRow";

export { DerivedNote, LimitRowItem, LimitStateTag } from "./LimitRow";

/**
 * The limit board: "how close are we to any limit?" (plan §4, Risk & Exposure).
 *
 * Every limit the API reports is a horizontal bar from zero to the limit,
 * with the value, the limit, the headroom and a state word; every limit the
 * platform has but the API does not report is listed as NOT REPORTED with the
 * reason, never drawn as an empty bar. Rows are grouped by family and sorted
 * by how much of the limit is used; families are ordered by their most-used
 * row (lib/limits.ts).
 *
 * The two sources (GET /api/trading/risk and GET /api/trading/exposure) keep
 * their own view states: a family whose source is loading, failed, or stale
 * says so in place, and the headline never claims "nothing is close" while a
 * source is missing.
 *
 * Bars are owned SVG. An OK bar takes the provenance ink of its figure (the
 * chart grammar: LIVE magenta, never loss red); NEAR and CRITICAL take the
 * health tokens; BREACHED fills the track in the critical token. Every state
 * also has a glyph and a word, so none rests on colour.
 */

/** Exposure buckets listed on the board; the matrix below lists every one. */
const EXPOSURE_ON_BOARD = 6;

/** The provenance label for a family: derived from the figures its rows drew. */
function familyProvenance(rows: readonly LimitRow[], exposure: readonly ExposureRow[] | null) {
  if (exposure) {
    // Every exposure and limit figure the bars were drawn from, as the
    // exposure chart always counted them.
    return deriveProvenance(exposure.flatMap((r) => [r.exposure_pct.provenance, r.limit_pct.provenance]));
  }
  const provenances: Provenance[] = [];
  for (const row of rows) {
    if (row.value) provenances.push(row.value.provenance);
    if (row.limit) provenances.push(row.limit.provenance);
  }
  return provenances.length > 0 ? deriveProvenance(provenances) : null;
}

type SourceState<T> = ApiHandle<T>;

function SourceNote<T>({ handle }: { handle: SourceState<T> }) {
  if (handle.status !== "success") return null;
  if (!isStale(handle.freshness) && handle.freshness !== "lagging") return null;
  return (
    <StaleBadge
      info={{
        freshness: handle.freshness,
        asOf: handle.asOf,
        sourceAsOf: handle.sourceAsOf,
        refreshError: handle.refreshError,
      }}
      polling={handle.refreshMs !== undefined}
      onRetry={handle.reload}
    />
  );
}

function SourceProblem<T>({ handle, path }: { handle: SourceState<T>; path: string }) {
  if (handle.status === "loading") {
    return (
      <p className="limit-family__problem" data-testid="limit-family-loading" role="status">
        <ViewStateTag state="loading" /> Reading {path}…
      </p>
    );
  }
  if (handle.status === "error") {
    return (
      <div className="limit-family__problem limit-family__problem--error" data-testid="limit-family-error" role="alert">
        <ViewStateTag state="error" /> Could not read {path}: {handle.error.message} Nothing in this family is known,
        which is not the same as nothing being close.
        <span className="state__code">
          {" "}
          code: {handle.error.code}
          {handle.error.status ? ` · http ${handle.error.status}` : ""}
          {handle.error.correlationId ? ` · correlation ${handle.error.correlationId}` : ""}
        </span>{" "}
        <Button size="sm" onClick={handle.reload}>
          Retry
        </Button>
      </div>
    );
  }
  return null;
}

function FamilyBlock({
  group,
  exposure,
  problem,
  stale,
}: {
  group: FamilyGroup | { meta: (typeof FAMILIES)[number]; rows: LimitRow[]; worst: null };
  exposure: readonly ExposureRow[] | null;
  problem: ReactNode;
  stale: ReactNode;
}) {
  const { meta } = group;
  const isExposure = meta.id === "exposure";
  const rows = isExposure ? group.rows.slice(0, EXPOSURE_ON_BOARD) : group.rows;
  const hidden = group.rows.length - rows.length;
  const label = problem ? null : familyProvenance(group.rows, isExposure ? exposure : null);
  const signed = meta.id === "loss";
  const headingId = `limit-family-${meta.id}`;
  const body = (
    <>
      {problem}
      {!problem && isExposure && group.rows.length === 0 ? (
        <p className="limit-family__problem" data-testid="limit-family-empty">
          <ViewStateTag state="empty" /> The platform answered with no exposure buckets: nothing is open, so no exposure
          limit is in use.
        </p>
      ) : null}
      {!problem && rows.length > 0 ? (
        <ul className="limit-family__rows">
          {rows.map((row) => (
            <LimitRowItem key={row.key} row={row} signed={signed} />
          ))}
        </ul>
      ) : null}
      {hidden > 0 ? (
        <p className="limit-family__more">
          {hidden} more bucket{hidden === 1 ? "" : "s"}, less used, in <a href="#exposure">the exposure matrix</a>.
        </p>
      ) : null}
    </>
  );
  const head = (
    <>
      <span className="limit-family__title" id={headingId}>
        {meta.title}
      </span>
      {label ? <ProvenanceLabelChip label={label} /> : null}
      {stale}
    </>
  );
  // The exposure family is a chart of the API's exposure rows: it carries the
  // one derived provenance chip, as the exposure bar chart did.
  if (isExposure) {
    return (
      <figure className="limit-family" data-testid="chart" data-family={meta.id} aria-labelledby={headingId}>
        <figcaption className="limit-family__head">{head}</figcaption>
        {body}
      </figure>
    );
  }
  return (
    <section className="limit-family" data-family={meta.id} aria-labelledby={headingId}>
      <div className="limit-family__head">{head}</div>
      {body}
    </section>
  );
}

function Headline({
  rows,
  missing,
}: {
  rows: readonly LimitRow[];
  missing: string[];
}) {
  const closest = closestToLimit(rows);
  return (
    <div className="limit-hero" data-testid="limit-hero" data-state={closest ? closest.state : "absent"}>
      <div className="limit-hero__kicker">Closest to a limit</div>
      {closest && closest.utilisation !== null ? (
        <div className="limit-hero__main">
          <span className="limit-hero__label" data-testid="limit-hero-label">
            {closest.label}
            {closest.kind ? <span className="limit-row__kind">{closest.kind}</span> : null}
          </span>
          <span className="limit-hero__value num" data-testid="limit-hero-used">
            {pct(closest.utilisation)}
            <span className="limit-hero__unit"> of its limit</span>
          </span>
          <LimitStateTag state={closest.state} />
          {closest.provenance ? <ProvenanceChip provenance={closest.provenance} /> : null}
        </div>
      ) : (
        <div className="limit-hero__main">
          <span className="limit-hero__label">No limit is measured right now</span>
          <ViewStateTag state="absent">NOTHING MEASURED</ViewStateTag>
        </div>
      )}
      {missing.length > 0 ? (
        <p className="limit-hero__caveat" data-testid="limit-hero-incomplete" role="status">
          Incomplete: {missing.join(" and ")} {missing.length === 1 ? "is" : "are"} not loaded, so a closer limit may
          exist.
        </p>
      ) : null}
    </div>
  );
}

export function LimitBoard({
  risk,
  exposure,
}: {
  risk: ApiHandle<Envelope<RiskStateView>>;
  exposure: ApiHandle<Page<ExposureRow>>;
}) {
  const riskData = risk.status === "success" ? risk.data.data : null;
  const exposureItems = exposure.status === "success" ? exposure.data.items : null;

  const all: LimitRow[] = unreportedRows();
  if (riskData) all.push(...riskRows(riskData));
  if (exposureItems) all.push(...exposureRows(exposureItems));

  const loaded: FamilyId[] = FAMILIES.filter(
    (f) =>
      f.source === "none" ||
      (f.source === "risk" && riskData !== null) ||
      (f.source === "exposure" && exposureItems !== null),
  ).map((f) => f.id);
  const groups = groupFamilies(all, loaded);
  const pending = FAMILIES.filter((f) => !loaded.includes(f.id));
  const failed = pending.filter((f) =>
    f.source === "risk" ? risk.status === "error" : exposure.status === "error",
  );
  const waiting = pending.filter((f) => !failed.includes(f));

  const missing: string[] = [];
  if (!riskData) missing.push("/api/trading/risk");
  if (!exposureItems) missing.push("/api/trading/exposure");

  const problemFor = (source: "risk" | "exposure" | "none") =>
    source === "risk" ? (
      <SourceProblem handle={risk} path="/api/trading/risk" />
    ) : source === "exposure" ? (
      <SourceProblem handle={exposure} path="/api/trading/exposure" />
    ) : null;
  const staleFor = (source: "risk" | "exposure" | "none") =>
    source === "risk" ? (
      <SourceNote handle={risk} />
    ) : source === "exposure" ? (
      <SourceNote handle={exposure} />
    ) : null;

  const block = (group: FamilyGroup | { meta: (typeof FAMILIES)[number]; rows: LimitRow[]; worst: null }) => (
    <FamilyBlock
      key={group.meta.id}
      group={group}
      exposure={group.meta.id === "exposure" ? exposureItems : null}
      problem={loaded.includes(group.meta.id) ? null : problemFor(group.meta.source)}
      stale={loaded.includes(group.meta.id) ? staleFor(group.meta.source) : null}
    />
  );

  return (
    <section className="limit-board" data-testid="limit-board" aria-labelledby="limit-board-title">
      <div className="limit-board__head">
        <h2 id="limit-board-title" className="limit-board__title">
          How close are we to any limit?
        </h2>
        <p className="limit-board__legend">
          Bar ends at the limit; ticks at {WARN_AT_PCT}% and {CRITICAL_AT_PCT}% of it.{" "}
          <LimitStateTag state="warn" /> from {WARN_AT_PCT}%, <LimitStateTag state="critical" /> from{" "}
          {CRITICAL_AT_PCT}%: the workstation&apos;s display bands, because the API supplies none.
        </p>
      </div>
      <Headline rows={all} missing={missing} />
      <div className="limit-board__families">
        {/* A family whose source failed is unknown, and unknown goes first. */}
        {failed.map((meta) => block({ meta, rows: [], worst: null }))}
        {groups.map((group) => block(group))}
        {waiting.map((meta) => block({ meta, rows: [], worst: null }))}
      </div>
      <DerivedNote />
    </section>
  );
}
