"use client";

import Link from "next/link";
import { formatNumber } from "@/lib/format";
import { CRITICAL_AT_PCT, WARN_AT_PCT, barLength, type LimitRow, type LimitState } from "@/lib/limits-core";
import { FigureValue } from "../FigureValue";
import { ProvenanceChip } from "../ProvenanceChip";

/**
 * One limit row and its pieces (the bar, the state word, the † note), split
 * out of LimitBoard so Command can draw the same rows without the whole board
 * in the Overview's first load. The board and Command both render these, so
 * the two screens cannot draw one limit two ways.
 *
 * An OK bar takes the provenance ink of its figure (LIVE magenta, never loss
 * red); NEAR and CRITICAL take the health tokens; BREACHED fills the track in
 * the critical token. Every state also has a glyph and a word.
 */

const STATE_META: Record<LimitState, { glyph: string; word: string }> = {
  ok: { glyph: "✓", word: "OK" },
  warn: { glyph: "◐", word: "NEAR" },
  critical: { glyph: "◆", word: "CRITICAL" },
  breached: { glyph: "✕", word: "BREACHED" },
  absent: { glyph: "⊘", word: "NOT REPORTED" },
};


export function LimitStateTag({ state }: { state: LimitState }) {
  const meta = STATE_META[state];
  return (
    <span className="limit-state" data-state={state} data-testid="limit-state">
      <span aria-hidden="true">{meta.glyph}</span>
      {meta.word}
    </span>
  );
}

export function pct(value: number, decimals = 1): string {
  return formatNumber(value, "pct", { decimals });
}

/** Headroom in percentage points; a negative headroom is "over by". */
function headroomText(row: LimitRow): string {
  if (row.headroom === null) return "not known";
  if (row.headroom < 0) return `over by ${formatNumber(-row.headroom, "pct", { bare: true })} pp`;
  return `${formatNumber(row.headroom, "pct", { bare: true })} pp`;
}

function Bar({ row }: { row: LimitRow }) {
  const length = barLength(row);
  const ink = row.state === "ok" ? row.provenance : null;
  const fillClass =
    row.state === "breached"
      ? "chart__bar chart__bar--breach"
      : row.state === "critical"
        ? "chart__bar limit-bar__fill limit-bar__fill--critical"
        : row.state === "warn"
          ? "chart__bar limit-bar__fill limit-bar__fill--warn"
          : "chart__bar chart-ink limit-bar__fill";
  return (
    <svg
      className="limit-bar"
      viewBox="0 0 100 12"
      preserveAspectRatio="none"
      aria-hidden="true"
      data-testid="limit-bar"
      data-length={length.toFixed(1)}
    >
      <rect className="limit-bar__track" x="0" y="0" width="100" height="12" />
      <rect className={fillClass} data-ink={ink ?? undefined} x="0" y="0" width={length} height="12" />
      <line className="limit-bar__tick" x1={WARN_AT_PCT} x2={WARN_AT_PCT} y1="0" y2="12" />
      <line className="limit-bar__tick" x1={CRITICAL_AT_PCT} x2={CRITICAL_AT_PCT} y1="0" y2="12" />
      <line className="limit-bar__limit" x1="99.6" x2="99.6" y1="0" y2="12" />
    </svg>
  );
}

/**
 * One limit row: label, utilisation, state, the bar and its numbers. Also
 * rendered on Command (app/page.tsx) for daily loss and drawdown, so the two
 * screens can never draw the same limit differently.
 */
export function LimitRowItem({
  row,
  signed,
  showChip = false,
}: {
  row: LimitRow;
  signed: boolean;
  /** A chip on the row itself, where no family heading carries one (Command). */
  showChip?: boolean;
}) {
  const util = row.utilisation;
  return (
    <li
      className="limit-row"
      data-testid="limit-row"
      data-key={row.key}
      data-family={row.family}
      data-state={row.state}
      data-utilisation={util === null ? undefined : util.toFixed(1)}
      data-derived={row.utilisationFrom === "derived" || undefined}
    >
      <div className="limit-row__head">
        <span className="limit-row__label">
          {row.label}
          {row.kind ? <span className="limit-row__kind">{row.kind}</span> : null}
        </span>
        {util !== null ? (
          <span className="limit-row__used num" data-testid="limit-used">
            {pct(util)}
            {row.utilisationFrom === "derived" ? (
              <sup className="limit-row__dagger" aria-label="computed here, see the note below the board">
                †
              </sup>
            ) : null}
            <span className="limit-row__used-word"> used</span>
          </span>
        ) : null}
        <LimitStateTag state={row.state} />
        {showChip && row.provenance ? <ProvenanceChip provenance={row.provenance} /> : null}
      </div>
      {row.state === "absent" && util === null ? (
        <p className="limit-row__absent" data-testid="limit-absent">
          {row.value && row.value.value !== null ? (
            <>
              <FigureValue figure={row.value} showChip={false} />{" "}
            </>
          ) : null}
          {row.absentReason}
          {row.seeAlso ? (
            <>
              {" "}
              <Link href={row.seeAlso.href}>Open {row.seeAlso.label}</Link>
            </>
          ) : null}
        </p>
      ) : (
        <>
          <Bar row={row} />
          <dl className="limit-row__nums">
            <div>
              <dt>{signed ? "day P&L" : "now"}</dt>
              <dd data-testid="limit-value">
                {row.value ? <FigureValue figure={row.value} showChip={false} colourSign={signed} /> : "—"}
              </dd>
            </div>
            <div>
              <dt>limit</dt>
              <dd data-testid="limit-limit">
                {row.limit ? <FigureValue figure={row.limit} showChip={false} /> : "—"}
              </dd>
            </div>
            <div>
              <dt>headroom</dt>
              <dd className="num" data-testid="limit-headroom">
                {headroomText(row)}
              </dd>
            </div>
          </dl>
        </>
      )}
    </li>
  );
}

/** The dagger's explanation, shared by every screen that draws a derived utilisation. */
export function DerivedNote({ exposure = true }: { exposure?: boolean }) {
  return (
    <p className="limit-board__note" data-testid="limit-derived-note">
      † Computed in the workstation from the two API figures shown beside it, by the rule the API applies when it
      lists a breach: daily loss used = the day&apos;s loss ÷ the limit, headroom = day P&amp;L + limit; drawdown used =
      drawdown ÷ limit, headroom = limit − drawdown.{exposure ? " Exposure utilisation is the API's own." : ""}
    </p>
  );
}
