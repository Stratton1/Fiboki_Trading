"use client";

import type { PositionRow } from "@/lib/types";
import { Tooltip } from "../ui/Tooltip";

/**
 * Distance to stop, as a short owned-SVG meter per open position (V-5).
 *
 * The number is the API's own `distance_to_stop_pct`, shown beside the meter
 * by the caller. The meter PLACES the mark between the stop and the entry,
 * which is arithmetic done here, so it is marked † with the formula:
 *
 *     position = (mark − stop) ÷ (entry − stop)
 *
 * 0 is the stop, 1 the entry; above 1 the position is in profit, below 0 the
 * mark is through the stop. The same formula serves a short (both signs flip).
 * The track runs from the stop to twice the entry-to-stop distance; a mark
 * outside that is pinned to the end with an arrow. Nothing is decided from
 * this: it colours nothing by threshold and it sorts nothing.
 *
 * With any of the three prices missing, or an entry equal to its stop, there
 * is no meter, and the reason is written instead. An estimated mark keeps its
 * "est" beside the price in the table.
 */

export const STOP_METER_FORMULA = "position = (mark − stop) ÷ (entry − stop); 0 is the stop, 1 the entry";

const W = 112;
const H = 14;
/** The track spans 0 (stop) to MAX_SPAN (twice the entry-to-stop distance). */
const MAX_SPAN = 2;

export function stopPosition(row: Pick<PositionRow, "entry_price" | "mark_price" | "stop_loss">): number | null {
  const entry = row.entry_price.value;
  const mark = row.mark_price.value;
  const stop = row.stop_loss.value;
  if (entry === null || mark === null || stop === null || entry === stop) return null;
  return (mark - stop) / (entry - stop);
}

function reasonMissing(row: Pick<PositionRow, "entry_price" | "mark_price" | "stop_loss">): string {
  if (row.stop_loss.value === null) return "no stop reported";
  if (row.mark_price.value === null) return "no mark reported";
  if (row.entry_price.value === null) return "no entry reported";
  return "entry equals stop";
}

export function StopMeter({ row }: { row: PositionRow }) {
  const position = stopPosition(row);
  if (position === null) {
    return (
      <span className="stopmeter stopmeter--none muted" data-testid="stop-meter" data-state="none">
        {reasonMissing(row)}
      </span>
    );
  }
  const clamped = Math.min(MAX_SPAN, Math.max(0, position));
  const x = (clamped / MAX_SPAN) * W;
  const entryX = W / MAX_SPAN;
  const through = position < 0;
  const beyond = position > MAX_SPAN;
  return (
    <span
      className="stopmeter"
      data-testid="stop-meter"
      data-state={through ? "through" : "between"}
      data-position={position.toFixed(3)}
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width={W}
        height={H}
        role="img"
        aria-label={`Mark at ${position.toFixed(2)} of the way from the stop (0) to the entry (1)${through ? ", through the stop" : ""}.`}
      >
        <rect className="stopmeter__track" x={0} y={5} width={W} height={4} rx={2} />
        <rect className="stopmeter__fill" x={0} y={5} width={x} height={4} rx={2} />
        <line className="stopmeter__stop" x1={0.75} x2={0.75} y1={1} y2={13} />
        <line className="stopmeter__entry" x1={entryX} x2={entryX} y1={2} y2={12} />
        <path
          className="stopmeter__mark"
          data-testid="stop-meter-mark"
          d={
            beyond
              ? `M ${W - 6} 2 L ${W} 7 L ${W - 6} 12 Z`
              : through
                ? `M 6 2 L 0 7 L 6 12 Z`
                : `M ${x - 4} 1 L ${x + 4} 1 L ${x} 7 Z`
          }
        />
      </svg>
      <Tooltip label={`† computed here: ${STOP_METER_FORMULA}`}>
        <button
          type="button"
          className="stopmeter__dagger"
          aria-label={`Computed in the workstation: ${STOP_METER_FORMULA}`}
          data-testid="stop-meter-dagger"
        >
          †
        </button>
      </Tooltip>
    </span>
  );
}

/** The foot note for a table that draws stop meters. */
export function StopMeterNote() {
  return (
    <p className="muted stopmeter__note" data-testid="stop-meter-note">
      † The meter is placed in the workstation from three API prices: {STOP_METER_FORMULA}. The percentage beside it is
      the API&apos;s own distance to stop.
    </p>
  );
}
