"use client";

import Link from "next/link";
import { formatNumber } from "@/lib/format";
import { barLength, type LimitState } from "@/lib/limits-core";
import type { BandsView, LimitRowView } from "@/lib/types";
import { FigureValue } from "../FigureValue";
import { ProvenanceChip } from "../ProvenanceChip";

/**
 * One limit row and its pieces (the bar, the state word), shared by the limit
 * board and Command, so the two screens cannot draw one limit two ways.
 *
 * Everything on the row is the API's (GET /api/trading/limits): utilisation,
 * headroom, the band, the breach and the band ticks. An OK bar takes the
 * provenance ink of its figure (LIVE magenta, never loss red); NEAR and
 * CRITICAL take the health tokens; BREACHED fills the track in the critical
 * token. Every state also has a glyph and a word.
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

/** Headroom in percentage points; on a breached row the API's headroom is how far over. */
function headroomText(row: LimitRowView): string {
  const value = row.headroom.value;
  if (value === null) return "not known";
  const amount = formatNumber(Math.abs(value), "pct", { bare: true });
  return row.breached ? `over by ${amount} pp` : `${amount} pp`;
}

function Bar({ row, bands }: { row: LimitRowView; bands: BandsView | null }) {
  const length = barLength(row);
  const ink = row.band === "ok" ? (row.value?.provenance ?? null) : null;
  const fillClass =
    row.band === "breached"
      ? "chart__bar chart__bar--breach"
      : row.band === "critical"
        ? "chart__bar limit-bar__fill limit-bar__fill--critical"
        : row.band === "warn"
          ? "chart__bar limit-bar__fill limit-bar__fill--warn"
          : "chart__bar chart-ink limit-bar__fill";
  const warn = bands?.warn_at.value ?? null;
  const critical = bands?.critical_at.value ?? null;
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
      {warn !== null ? <line className="limit-bar__tick" x1={warn} x2={warn} y1="0" y2="12" /> : null}
      {critical !== null ? <line className="limit-bar__tick" x1={critical} x2={critical} y1="0" y2="12" /> : null}
      <line className="limit-bar__limit" x1="99.6" x2="99.6" y1="0" y2="12" />
    </svg>
  );
}

/** One limit row: label, utilisation, state, the bar and its numbers. */
export function LimitRowItem({
  row,
  bands,
  signed,
  showChip = false,
  compact = false,
}: {
  row: LimitRowView;
  bands: BandsView | null;
  signed: boolean;
  /** A chip on the row itself, where no family heading carries one (Command). */
  showChip?: boolean;
  /** One line: label, used, state, bar (Command). */
  compact?: boolean;
}) {
  const util = row.utilisation_pct.value;
  return (
    <li
      className={compact ? "limit-row limit-row--compact" : "limit-row"}
      data-testid="limit-row"
      data-key={row.key}
      data-family={row.family}
      data-state={row.band}
      data-utilisation={util === null ? undefined : util.toFixed(1)}
      title={row.formula ?? undefined}
    >
      <div className="limit-row__head">
        <span className="limit-row__label">
          {row.label}
          {row.kind ? <span className="limit-row__kind">{row.kind}</span> : null}
        </span>
        {util !== null ? (
          <span className="limit-row__used num" data-testid="limit-used">
            {pct(util)}
            <span className="limit-row__used-word"> used</span>
          </span>
        ) : null}
        <LimitStateTag state={row.band} />
        {showChip && row.value ? <ProvenanceChip provenance={row.value.provenance} /> : null}
        {compact && util !== null ? <Bar row={row} bands={bands} /> : null}
      </div>
      {row.band === "absent" || (row.breached && util === null) ? (
        <p className="limit-row__absent" data-testid="limit-absent">
          {row.value && row.value.value !== null ? (
            <>
              <FigureValue figure={row.value} showChip={false} />{" "}
            </>
          ) : null}
          {row.absent_reason}
          {row.see_also ? (
            <>
              {" "}
              <Link href={row.see_also}>Open</Link>
            </>
          ) : null}
        </p>
      ) : compact ? null : (
        <>
          <Bar row={row} bands={bands} />
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

/** Where the numbers on a limit bar come from, said once under a board. */
export function DerivedNote() {
  return (
    <p className="limit-board__note" data-testid="limit-derived-note">
      Utilisation, headroom, band and breach are the API&apos;s (GET /api/trading/limits); the attention queue
      lists the same rows. Hover a row for its formula.
    </p>
  );
}
