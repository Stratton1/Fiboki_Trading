import type { Figure } from "@/lib/types";
import { formatFigure, formatTimestamp, roundedSign, signClass } from "@/lib/format";
import { CaveatList } from "./CaveatList";
import { ProvenanceChip } from "./ProvenanceChip";
import { Popover } from "./ui/Popover";

/**
 * A number and its label, rendered together and never apart (v2).
 *
 * When `figure.value` is null this renders an explicit "no data" token. It does
 * NOT render 0, and there is no prop to make it. In V1, `data?.x ?? 0` turned a
 * dead backend into a screen reading "£0.00 balance, 0/0 bots running" beside a
 * hardcoded "Online" badge, and a total outage was indistinguishable from a
 * flat, idle, healthy fleet.
 *
 * v2 adds, without weakening any of that:
 *  - a real minus sign (U+2212) on every negative, and with `colourSign` an
 *    explicit "+" on gains, so direction never rests on colour alone;
 *  - `glyph`: a ▲/▼ beside a signed value (stat tiles);
 *  - `est` as a superscript whose meaning is a focusable popover, not a hover
 *    title;
 *  - the caveat count ⚠n as a button whose popover renders every caveat in
 *    full, reachable by keyboard and touch;
 *  - right alignment in table cells (globals.css: `td:has(> .figure)`);
 *  - direction (colour, sign, glyph) from the value AT ITS DISPLAYED
 *    PRECISION, so a loss that rounds to 0.00 shows neither ▼ nor red;
 *  - `decimals` (a precision the server supplied, e.g. from pip size, or a
 *    grid column's shared precision) and `bare` (the unit is in the column
 *    header) for grid cells.
 *
 * `figure.as_of`, when the API supplies one, is always shown: as the hover
 * title by default (tables), or as a subdued suffix (`asOf="suffix"`, tiles).
 * A figure with no as-of says nothing about time, and no time is invented.
 */
export function FigureValue({
  figure,
  showChip = true,
  colourSign = false,
  glyph = false,
  missingLabel = "no data",
  asOf = "title",
  decimals,
  bare = false,
}: {
  figure: Figure;
  showChip?: boolean;
  /** A signed quantity (P&L, R): colour, explicit sign. */
  colourSign?: boolean;
  /** Add ▲/▼ beside a signed value. Stat tiles only. */
  glyph?: boolean;
  missingLabel?: string;
  asOf?: "title" | "suffix";
  decimals?: number | null;
  bare?: boolean;
}) {
  const text = formatFigure(figure, { signed: colourSign, decimals, bare });
  const warnings = figure.caveats.filter((c) => c.severity !== "info");
  const critical = warnings.some((c) => c.severity === "critical");
  const asOfText = figure.as_of ? `as of ${formatTimestamp(figure.as_of)}` : undefined;
  const sign = figure.value === null ? 0 : roundedSign(figure.value, figure.unit, decimals);
  const direction = !colourSign ? null : sign > 0 ? "up" : sign < 0 ? "down" : null;

  return (
    <span className="figure" data-testid="figure" data-as-of={figure.as_of ?? undefined}>
      {text === null ? (
        <span className="figure__missing" data-testid="figure-missing" title={asOfText}>
          {missingLabel}
        </span>
      ) : (
        <span
          className={`figure__value ${colourSign ? signClass(figure.value, figure.unit, decimals) : ""}`}
          data-testid="figure-value"
          data-direction={direction ?? undefined}
          title={asOfText}
        >
          {glyph && direction ? (
            <span className="figure__glyph" aria-hidden="true">
              {direction === "up" ? "▲" : "▼"}
            </span>
          ) : null}
          {text}
        </span>
      )}
      {asOf === "suffix" && asOfText ? (
        <span className="figure__asof" data-testid="figure-as-of">
          {asOfText}
        </span>
      ) : null}
      {figure.estimated && text !== null ? (
        <Popover
          title="Estimated"
          trigger={
            <button
              type="button"
              className="figure__est"
              data-testid="figure-estimated"
              aria-label="Estimated value. What this means."
            >
              est
            </button>
          }
        >
          <p>Modelled, not observed at a venue.</p>
        </Popover>
      ) : null}
      {warnings.length > 0 ? (
        <Popover
          title={`${warnings.length} caveat${warnings.length === 1 ? "" : "s"} on this figure`}
          testId="figure-caveat-popover"
          trigger={
            <button
              type="button"
              className="figure__flag"
              data-testid="figure-caveat-flag"
              data-severity={critical ? "critical" : "warning"}
              aria-label={`${warnings.length} caveat${warnings.length === 1 ? "" : "s"} on this figure. Show them.`}
            >
              ⚠{warnings.length}
            </button>
          }
        >
          <CaveatList caveats={warnings} />
        </Popover>
      ) : null}
      {showChip ? (
        <ProvenanceChip provenance={figure.provenance} sampleSize={figure.sample_size} />
      ) : null}
    </span>
  );
}
