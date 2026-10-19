import { formatTimestamp, formatUtcTime } from "@/lib/format";
import type { SourceNote } from "@/lib/types";

/**
 * A panel's freshness and provenance stamp, one compact line: the API's own
 * source kind and as-of (never this browser's clock), with the full source
 * note on hover and for screen readers. Every panel carries one.
 */
export function PanelStamp({ source, extra }: { source: SourceNote; extra?: string }) {
  const asOf = source.as_of;
  return (
    <p className="ws-stamp" data-testid="panel-stamp" data-kind={source.kind} title={source.detail}>
      <span className="ws-stamp__kind">{source.kind.toUpperCase()}</span>
      <span className="ws-stamp__asof">{asOf ? `as of ${formatUtcTime(asOf)}` : "as-of not supplied"}</span>
      {extra ? <span className="ws-stamp__extra">{extra}</span> : null}
      <span className="sr-only">
        {source.detail} {asOf ? formatTimestamp(asOf) : ""}
      </span>
    </p>
  );
}
