"use client";

import { formatNumber } from "@/lib/format";
import { familyRows, rowsByKey } from "@/lib/limits-core";
import { deriveProvenance } from "@/lib/provenance";
import type { ApiHandle } from "@/lib/query";
import type { BandsView, Envelope, LimitBoardView, LimitFamilyView, LimitRowView, Provenance } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { CaveatList } from "../CaveatList";
import { ProvenanceChip, ProvenanceLabelChip } from "../ProvenanceChip";
import { ViewStateTag } from "../ui/ViewStateTag";
import { DerivedNote, LimitRowItem, LimitStateTag, pct } from "./LimitRow";

export { DerivedNote, LimitRowItem, LimitStateTag } from "./LimitRow";

export const LIMITS_PATH = "/api/trading/limits";

/**
 * The limit board: "how close are we to any limit?" (plan §4, Risk & Exposure).
 *
 * One read, GET /api/trading/limits. Every limit the API reports is a bar
 * from zero to the limit with the value, the limit, the headroom and a state
 * word; every limit the platform has but does not report is listed as NOT
 * REPORTED with the reason. Rows and families arrive in the API's order
 * (breached, then most used, then absent), with the API's bands; this
 * component sorts, divides and compares nothing.
 */

/** Exposure buckets listed on the board; the matrix below lists every one. */
const EXPOSURE_ON_BOARD = 6;

function familyProvenance(rows: readonly LimitRowView[]) {
  const provenances: Provenance[] = [];
  for (const row of rows) {
    if (row.value) provenances.push(row.value.provenance);
    if (row.limit) provenances.push(row.limit.provenance);
  }
  return provenances.length > 0 ? deriveProvenance(provenances) : null;
}

function FamilyBlock({
  family,
  rows,
  bands,
}: {
  family: LimitFamilyView;
  rows: LimitRowView[];
  bands: BandsView;
}) {
  const isExposure = family.id === "exposure";
  const shown = isExposure ? rows.slice(0, EXPOSURE_ON_BOARD) : rows;
  const hidden = rows.length - shown.length;
  const label = familyProvenance(rows);
  const headingId = `limit-family-${family.id}`;
  const head = (
    <>
      <span className="limit-family__title" id={headingId}>
        {family.title}
      </span>
      {label ? <ProvenanceLabelChip label={label} /> : null}
    </>
  );
  const body = (
    <>
      {isExposure && rows.length === 0 ? (
        <p className="limit-family__problem" data-testid="limit-family-empty">
          <ViewStateTag state="empty" /> The platform answered with no exposure buckets: nothing is open, so no exposure
          limit is in use.
        </p>
      ) : (
        <ul className="limit-family__rows">
          {shown.map((row) => (
            <LimitRowItem key={row.key} row={row} bands={bands} signed={family.id === "loss"} />
          ))}
        </ul>
      )}
      {hidden > 0 ? (
        <p className="limit-family__more">
          {hidden} more bucket{hidden === 1 ? "" : "s"}, less used, in <a href="#exposure">the exposure matrix</a>.
        </p>
      ) : null}
    </>
  );
  if (isExposure) {
    return (
      <figure className="limit-family" data-testid="chart" data-family={family.id} aria-labelledby={headingId}>
        <figcaption className="limit-family__head">{head}</figcaption>
        {body}
      </figure>
    );
  }
  return (
    <section className="limit-family" data-family={family.id} aria-labelledby={headingId}>
      <div className="limit-family__head">{head}</div>
      {body}
    </section>
  );
}

/**
 * Limits the platform enforces but the API does not report (programme P1 12:
 * collapse NOT REPORTED rows): one line with the count, every row and its
 * reason one click away. Never drawn as an empty bar.
 */
function NotReported({ rows }: { rows: LimitRowView[] }) {
  if (rows.length === 0) return null;
  return (
    <details className="limit-absent" data-testid="limit-not-reported">
      <summary>
        <ViewStateTag state="absent">NOT REPORTED</ViewStateTag>{" "}
        <span>
          {rows.length} limit{rows.length === 1 ? "" : "s"} the API does not report:{" "}
          {rows.map((row) => row.label).join(", ")}
        </span>
      </summary>
      <ul className="limit-family__rows">
        {rows.map((row) => (
          <LimitRowItem key={row.key} row={row} bands={null} signed={false} />
        ))}
      </ul>
    </details>
  );
}

function Headline({ board }: { board: LimitBoardView }) {
  const closest = board.closest_key ? rowsByKey(board).get(board.closest_key) : undefined;
  const used = closest?.utilisation_pct.value ?? null;
  return (
    <div className="limit-hero" data-testid="limit-hero" data-state={closest ? closest.band : "absent"}>
      <div className="limit-hero__kicker">Closest to a limit</div>
      {closest && used !== null ? (
        <div className="limit-hero__main">
          <span className="limit-hero__label" data-testid="limit-hero-label">
            {closest.label}
            {closest.kind ? <span className="limit-row__kind">{closest.kind}</span> : null}
          </span>
          <span className="limit-hero__value num" data-testid="limit-hero-used">
            {pct(used)}
            <span className="limit-hero__unit"> of its limit</span>
          </span>
          <LimitStateTag state={closest.band} />
          {closest.value ? <ProvenanceChip provenance={closest.value.provenance} /> : null}
        </div>
      ) : (
        <div className="limit-hero__main">
          <span className="limit-hero__label">No limit is measured right now</span>
          <ViewStateTag state="absent">NOTHING MEASURED</ViewStateTag>
        </div>
      )}
    </div>
  );
}

export function LimitBoardBody({ envelope }: { envelope: Envelope<LimitBoardView> }) {
  const board = envelope.data;
  const warn = board.bands.warn_at.value;
  const critical = board.bands.critical_at.value;
  return (
    <>
      <div className="limit-board__head">
        <p className="limit-board__legend" title={board.bands.detail}>
          Bar ends at the limit; ticks at the platform&apos;s bands. <LimitStateTag state="warn" /> from{" "}
          {warn === null ? "—" : formatNumber(warn, "pct", { decimals: 0 })}, <LimitStateTag state="critical" /> from{" "}
          {critical === null ? "—" : formatNumber(critical, "pct", { decimals: 0 })} of the limit · limit set{" "}
          <span className="mono">{board.limits_version}</span>
        </p>
      </div>
      <CaveatList caveats={envelope.caveats.filter((c) => c.severity !== "info")} />
      <Headline board={board} />
      <div className="limit-board__families">
        {board.families.map((family) => {
          const rows = familyRows(board, family.rows).filter((row) => row.band !== "absent");
          // A family with nothing measured is listed once, below, not drawn.
          if (rows.length === 0 && family.id !== "exposure") return null;
          return <FamilyBlock key={family.id} family={family} rows={rows} bands={board.bands} />;
        })}
      </div>
      <NotReported rows={board.rows.filter((row) => row.band === "absent")} />
      <DerivedNote />
    </>
  );
}

export function LimitBoard({ limits }: { limits: ApiHandle<Envelope<LimitBoardView>> }) {
  return (
    <section className="limit-board" data-testid="limit-board" aria-labelledby="limit-board-title">
      <h2 id="limit-board-title" className="limit-board__title">
        How close are we to any limit?
      </h2>
      <AsyncBoundary state={limits} label="the limit board" onRetry={limits.reload}>
        {(envelope) => <LimitBoardBody envelope={envelope} />}
      </AsyncBoundary>
    </section>
  );
}
