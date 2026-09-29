import type { ReactNode } from "react";
import type { Figure, SourceNote } from "@/lib/types";
import { formatTimestamp } from "@/lib/format";
import { FigureValue } from "./FigureValue";

export { CaveatList } from "./CaveatList";

export function PageHead({
  title,
  intro,
  children,
}: {
  title: string;
  intro: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-head">
      <h1>{title}</h1>
      <p>{intro}</p>
      {children}
    </header>
  );
}

/**
 * Where a payload came from, rendered on every list and detail view. The page
 * never asserts this; it prints what the API said, including when the API says
 * the payload was produced (`as_of`, in UTC). An absent as-of is stated as
 * absent rather than left blank.
 */
export function SourceBadge({ source }: { source: SourceNote }) {
  return (
    <div className="source-note" data-testid="source-note" data-kind={source.kind}>
      <span className="source-note__kind" data-kind={source.kind}>
        {source.kind.toUpperCase()}
      </span>
      {source.detail}
      <span
        className="source-note__asof"
        data-testid="source-note-as-of"
        data-as-of={source.as_of ?? undefined}
      >
        {source.as_of
          ? `as of ${formatTimestamp(source.as_of)}`
          : "as-of time not supplied"}
      </span>
    </div>
  );
}

export function Tile({
  label,
  figure,
  help,
  colourSign = false,
}: {
  label: string;
  figure: Figure;
  help?: string;
  colourSign?: boolean;
}) {
  return (
    <div className="tile" data-testid="tile" data-label={label}>
      <div className="tile__label">{label}</div>
      <div className="tile__value">
        <FigureValue figure={figure} colourSign={colourSign} glyph={colourSign} asOf="suffix" />
      </div>
      {help ? <div className="tile__help">{help}</div> : null}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const known = ["ok", "degraded", "down"].includes(status);
  return (
    <span
      className={`badge badge--${known ? status : "unknown"}`}
      data-testid="status-badge"
      data-status={status}
    >
      {status.toUpperCase()}
    </span>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card">
      {title ? <h2 className="card__title">{title}</h2> : null}
      {children}
    </section>
  );
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="table-wrap">{children}</div>;
}
