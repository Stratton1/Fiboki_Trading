import type { Provenance } from "@/lib/types";
import type { ProvenanceLabel } from "@/lib/provenance";
import {
  PROVENANCE_EXECUTED,
  PROVENANCE_HELP,
  PROVENANCE_LABEL,
  PROVENANCE_SHORT,
  formatTimestamp,
} from "@/lib/format";
import { Popover } from "./ui/Popover";

/** The outline that tells the hollow (simulated) chips apart without colour. */
const OUTLINE: Record<Provenance, string> = {
  backtest: "dashed",
  walkforward: "solid",
  out_of_sample: "solid-flagged",
  holdout: "double",
  paper: "filled",
  shadow: "filled",
  broker_demo: "filled-hatched",
  broker_live: "filled-money",
};

/**
 * The chip that must appear beside every number (v2, shape grammar).
 *
 * Hollow = simulated: dashed BT, solid WF, solid with a filled corner flag
 * OOS (so WF and OOS differ in shape, report G W-13), double HOLD, all in one
 * neutral hue. Filled = executed: PAPER, SHADOW, DEMO (hatched) and LIVE (with
 * a £ glyph), in their execution mode's hue. Readable in greyscale.
 *
 * Its only input is the provenance carried by the data. It takes no page name,
 * no section and no default. There is no way to call this component such that
 * it displays a provenance the API did not send, which is the entire point:
 * V1's page titles made provenance claims the rows did not support.
 */
export function ProvenanceChip({
  provenance,
  sampleSize,
}: {
  provenance: Provenance;
  sampleSize?: number | null;
}) {
  const help = PROVENANCE_HELP[provenance];
  const title =
    sampleSize === null || sampleSize === undefined ? help : `${help} (${sampleSize} observations)`;
  const executed = PROVENANCE_EXECUTED[provenance];
  return (
    <span
      className={`prov prov--${provenance}`}
      data-testid="provenance-chip"
      data-provenance={provenance}
      data-shape={executed ? "filled" : "hollow"}
      data-outline={OUTLINE[provenance]}
      title={title}
    >
      {provenance === "broker_live" ? (
        <span className="prov__money" aria-hidden="true">
          £
        </span>
      ) : null}
      <span aria-hidden="true">{PROVENANCE_SHORT[provenance]}</span>
      <span className="sr-only">
        {PROVENANCE_LABEL[provenance].toLowerCase()} provenance
        {provenance === "broker_live" ? ", real money" : ""}
      </span>
    </span>
  );
}

/**
 * The chip for an AGGREGATE (a chart, a distribution, a summary) whose label
 * was derived from its rows by `deriveProvenance`.
 *
 * A single provenance renders the ordinary chip. Several render a MIXED chip
 * whose popover lists the count per provenance, because labelling a
 * distribution over backtest and paper trades with the first row's label is
 * the V1 page-title failure again, one level down. A payload with only a
 * SourceNote shows that note; a payload with nothing shows "unlabelled
 * source", explicitly.
 */
export function ProvenanceLabelChip({ label }: { label: ProvenanceLabel }) {
  switch (label.kind) {
    case "single":
      return <ProvenanceChip provenance={label.provenance} sampleSize={label.count} />;
    case "mixed": {
      const breakdown = label.counts
        .map((c) => `${PROVENANCE_LABEL[c.provenance]} ${c.count}`)
        .join(", ");
      const total = label.counts.reduce((sum, c) => sum + c.count, 0);
      return (
        <Popover
          title="Mixed provenance"
          testId="provenance-mixed-popover"
          trigger={
            <button
              type="button"
              className="prov prov--mixed"
              data-testid="provenance-chip"
              data-provenance="mixed"
              data-counts={label.counts.map((c) => `${c.provenance}:${c.count}`).join(",")}
              title={`Mixed provenance. This aggregate combines ${breakdown}. Read the per-row Source column before comparing values; these are not the same evidence.`}
              aria-label={`Mixed provenance: ${breakdown}. Show counts.`}
            >
              MIXED
            </button>
          }
        >
          <p>
            This aggregate combines {total} values of different evidence. They are not the same
            evidence: read the per-row Source column before comparing values.
          </p>
          <ul className="mt-2 flex list-none flex-col gap-1.5 ps-0">
            {label.counts.map((c) => (
              <li key={c.provenance} className="flex items-center gap-2">
                <ProvenanceChip provenance={c.provenance} />
                <span className="num text-fg">{c.count}</span>
              </li>
            ))}
          </ul>
        </Popover>
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
