import type { ReactNode } from "react";
import { roundedSign } from "@/lib/format";
import type { Figure } from "@/lib/types";
import { FigureValue } from "./FigureValue";

/**
 * A stat tile (Wave 3 `Stat`): a label, one Figure with its provenance chip
 * and as-of, and for a signed quantity the ▲/▼ glyph AND an explicit sign,
 * both decided from the value at its displayed precision, so a P&L that
 * rounds to £0.00 shows neither ▼ nor red (report G §2.5).
 *
 * Direction never rests on colour alone: the sign is in the text, the glyph
 * beside it, and `data-direction` on the tile for tests and styling. A null
 * value renders "no data" with no glyph, never 0.
 */
export function Stat({
  label,
  figure,
  help,
  signed = false,
  children,
}: {
  label: string;
  figure: Figure;
  help?: ReactNode;
  /** A signed quantity (P&L, R): glyph, sign, P&L colour. */
  signed?: boolean;
  children?: ReactNode;
}) {
  const sign = figure.value === null ? null : roundedSign(figure.value, figure.unit);
  const direction =
    !signed || sign === null ? undefined : sign > 0 ? "up" : sign < 0 ? "down" : "flat";
  return (
    <div className="tile stat" data-testid="tile" data-label={label} data-direction={direction}>
      <div className="tile__label">{label}</div>
      <div className="tile__value">
        <FigureValue figure={figure} colourSign={signed} glyph={signed} asOf="suffix" />
      </div>
      {help ? <div className="tile__help">{help}</div> : null}
      {children}
    </div>
  );
}
