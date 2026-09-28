import type { Provenance } from "@/lib/types";
import type { ProvenanceLabel } from "@/lib/provenance";
import { PROVENANCE_HELP, PROVENANCE_LABEL, formatTimestamp } from "@/lib/format";

/**
 * The chip that must appear beside every number.
 *
 * Its only input is the provenance carried by the data. It takes no page name,
 * no section and no default. There is no way to call this component such that
 * it displays a provenance the API did not send — which is the entire point,
 * because V1's page titles made provenance claims the rows did not support.
 */
export function ProvenanceChip({
  provenance,
  sampleSize,
}: {
  provenance: Provenance;
  sampleSize?: number | null;
}) {
  const label = PROVENANCE_LABEL[provenance];
  const help = PROVENANCE_HELP[provenance];
  const title =
    sampleSize === null || sampleSize === undefined
      ? help
      : `${help} (${sampleSize} observations)`;
  return (
    <span
      className={`prov prov--${provenance}`}
      data-testid="provenance-chip"
      data-provenance={provenance}
      title={title}
    >
      {label}
    </span>
  );
}

/**
 * The chip for an AGGREGATE (a chart, a distribution, a summary) whose label
 * was derived from its rows by `deriveProvenance`.
 *
 * A single provenance renders the ordinary chip. Several render a MIXED chip
 * whose title lists the count per provenance, because labelling a distribution
 * over backtest and paper trades with the first row's label is the V1 page
 * title failure again, one level down. A payload with only a SourceNote shows
 * that note; a payload with nothing shows "unlabelled source", explicitly.
 */
export function ProvenanceLabelChip({ label }: { label: ProvenanceLabel }) {
  switch (label.kind) {
    case "single":
      return (
        <ProvenanceChip provenance={label.provenance} sampleSize={label.count} />
      );
    case "mixed": {
      const breakdown = label.counts
        .map((c) => `${PROVENANCE_LABEL[c.provenance]} ${c.count}`)
        .join(", ");
      return (
        <span
          className="prov prov--mixed"
          data-testid="provenance-chip"
          data-provenance="mixed"
          data-counts={label.counts
            .map((c) => `${c.provenance}:${c.count}`)
            .join(",")}
          title={`Mixed provenance. This aggregate combines ${breakdown}. Read the per-row Source column before comparing values; these are not the same evidence.`}
          aria-label={`Mixed provenance: ${breakdown}`}
        >
          MIXED
        </span>
      );
    }
    case "source":
      return (
        <span
          className="prov prov--source"
          data-testid="provenance-chip"
          data-provenance="source"
          data-source-kind={label.source.kind}
          title={`No per-value provenance was supplied. The API describes this payload as: ${label.source.detail}${
            label.source.as_of ? ` (as of ${formatTimestamp(label.source.as_of)})` : ""
          }`}
        >
          SOURCE {label.source.kind.toUpperCase()}
        </span>
      );
    case "unlabelled":
      return (
        <span
          className="prov prov--unlabelled"
          data-testid="provenance-chip"
          data-provenance="unlabelled"
          title={label.reason}
        >
          unlabelled source
        </span>
      );
  }
}
