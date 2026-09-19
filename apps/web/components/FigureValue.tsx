import type { Figure } from "@/lib/types";
import { formatFigure, signClass } from "@/lib/format";
import { ProvenanceChip } from "./ProvenanceChip";

/**
 * A number and its label, rendered together and never apart.
 *
 * When `figure.value` is null this renders an explicit "no data" token. It does
 * NOT render 0, and there is no prop to make it. In V1, `data?.x ?? 0` turned a
 * dead backend into a screen reading "£0.00 balance, 0/0 bots running" beside a
 * hardcoded "Online" badge, and a total outage was indistinguishable from a
 * flat, idle, healthy fleet.
 */
export function FigureValue({
  figure,
  showChip = true,
  colourSign = false,
  missingLabel = "no data",
}: {
  figure: Figure;
  showChip?: boolean;
  colourSign?: boolean;
  missingLabel?: string;
}) {
  const text = formatFigure(figure);
  const warnings = figure.caveats.filter((c) => c.severity !== "info");

  return (
    <span className="figure" data-testid="figure">
      {text === null ? (
        <span className="figure__missing" data-testid="figure-missing">
          {missingLabel}
        </span>
      ) : (
        <span
          className={`figure__value ${colourSign ? signClass(figure.value) : ""}`}
          data-testid="figure-value"
        >
          {text}
        </span>
      )}
      {figure.estimated && text !== null ? (
        <span className="figure__flag" title="Modelled, not observed at a venue.">
          est
        </span>
      ) : null}
      {warnings.length > 0 ? (
        <span
          className="figure__flag"
          data-testid="figure-caveat-flag"
          title={warnings.map((c) => c.message).join("\n\n")}
        >
          ⚠{warnings.length}
        </span>
      ) : null}
      {showChip ? (
        <ProvenanceChip
          provenance={figure.provenance}
          sampleSize={figure.sample_size}
        />
      ) : null}
    </span>
  );
}
