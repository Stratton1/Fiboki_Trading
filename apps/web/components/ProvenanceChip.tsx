import type { Provenance } from "@/lib/types";
import { PROVENANCE_HELP, PROVENANCE_LABEL } from "@/lib/format";

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
