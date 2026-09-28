import type { Figure } from "@/lib/types";
import { formatFigure, formatTimestamp, signClass } from "@/lib/format";
import { ProvenanceChip } from "./ProvenanceChip";

/**
 * A number and its label, rendered together and never apart.
 *
 * When `figure.value` is null this renders an explicit "no data" token. It does
 * NOT render 0, and there is no prop to make it. In V1, `data?.x ?? 0` turned a
 * dead backend into a screen reading "£0.00 balance, 0/0 bots running" beside a
 * hardcoded "Online" badge, and a total outage was indistinguishable from a
 * flat, idle, healthy fleet.
 *
 * `figure.as_of`, when the API supplies one, is always shown: as the hover
 * title by default (tables), or as a subdued suffix (`asOf="suffix"`, tiles).
 * A figure with no as-of says nothing about time, and no time is invented.
 */
export function FigureValue({
  figure,
  showChip = true,
  colourSign = false,
  missingLabel = "no data",
  asOf = "title",
}: {
  figure: Figure;
  showChip?: boolean;
  colourSign?: boolean;
  missingLabel?: string;
  asOf?: "title" | "suffix";
}) {
  const text = formatFigure(figure);
  const warnings = figure.caveats.filter((c) => c.severity !== "info");
  const asOfText = figure.as_of ? `as of ${formatTimestamp(figure.as_of)}` : undefined;

  return (
    <span
      className="figure"
      data-testid="figure"
      data-as-of={figure.as_of ?? undefined}
    >
      {text === null ? (
        <span
          className="figure__missing"
          data-testid="figure-missing"
          title={asOfText}
        >
          {missingLabel}
        </span>
      ) : (
        <span
          className={`figure__value ${colourSign ? signClass(figure.value) : ""}`}
          data-testid="figure-value"
          title={asOfText}
        >
          {text}
        </span>
      )}
      {asOf === "suffix" && asOfText ? (
        <span className="figure__asof" data-testid="figure-as-of">
          {asOfText}
        </span>
      ) : null}
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
