"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { CommandSummaryView, Envelope, Figure } from "@/lib/types";
import { CaveatPopover } from "../CaveatPopover";
import { FigureValue } from "../FigureValue";
import { ProvenanceChip } from "../ProvenanceChip";
import { StaleBadge } from "../ui/StaleBadge";

/**
 * COMMAND · the money strip: equity, day and week P&L, unrealised, open
 * positions, bots by state, research in flight, and whether new risk is
 * permitted. One read, GET /api/command/summary, where every figure is
 * computed; nothing here adds, divides or decides.
 *
 * Every figure has the deployment's provenance, so the chip is shown ONCE at
 * the start of the strip rather than on every cell (programme item 15). A
 * figure whose provenance differs from the strip's keeps its own chip.
 *
 * The strip has a fixed height in every state (loading, error, success), so
 * nothing below it moves when it fills in.
 */

export const SUMMARY_PATH = "/api/command/summary";
const SUMMARY_REFRESH_MS = 10_000;

function Cell({
  label,
  href,
  testId,
  children,
  sub,
  tone,
}: {
  label: string;
  href?: string;
  testId: string;
  children: ReactNode;
  sub?: ReactNode;
  tone?: "ok" | "warn" | "critical";
}) {
  // The label is the link; the value is not inside it, because a figure's
  // own popovers (est, caveats) are buttons and a button inside a link is
  // neither valid nor operable.
  return (
    <li className="money__cell" data-testid={testId} data-tone={tone}>
      <div className="money__body">
        {href ? (
          <Link href={href} className="money__label money__link">
            {label}
          </Link>
        ) : (
          <span className="money__label">{label}</span>
        )}
        <span className="money__value">{children}</span>
        <span className="money__sub">{sub ?? " "}</span>
      </div>
    </li>
  );
}

function Fig({
  figure,
  signed = false,
  provenance,
}: {
  figure: Figure;
  signed?: boolean;
  provenance: string;
}) {
  return (
    <FigureValue
      figure={figure}
      colourSign={signed}
      glyph={signed}
      showChip={figure.provenance !== provenance}
    />
  );
}

function count(figure: Figure): string {
  return figure.value === null ? "–" : String(Math.round(figure.value));
}

export function MoneyStrip() {
  const state = useApi<Envelope<CommandSummaryView>>(SUMMARY_PATH, {
    refreshMs: SUMMARY_REFRESH_MS,
  });

  if (state.status !== "success") {
    return (
      <ul
        className="money"
        data-testid="money-strip"
        data-state={state.status}
        aria-busy={state.status === "loading"}
      >
        <li
          className="money__cell money__cell--wide"
          role={state.status === "error" ? "alert" : "status"}
        >
          <div className="money__body">
            <span className="money__label">Book</span>
            <span className="money__value muted">
              {state.status === "error"
                ? `Could not read the summary: ${state.error.message}`
                : "Reading the book…"}
            </span>
            <span className="money__sub">
              {state.status === "error" ? (
                <button
                  type="button"
                  className="linklike"
                  onClick={state.reload}
                >
                  Retry
                </button>
              ) : (
                " "
              )}
            </span>
          </div>
        </li>
      </ul>
    );
  }

  const { data, source, caveats } = state.data;
  const p = data.provenance;
  const bots = data.bots;
  const any = (figure: Figure) => figure.value !== null && figure.value > 0;
  const botTone =
    any(bots.stale) || any(bots.failed)
      ? "critical"
      : any(bots.halted)
        ? "warn"
        : undefined;
  return (
    <div className="money-wrap" data-testid="money-strip-wrap">
      <ul
        className="money"
        data-testid="money-strip"
        data-state="success"
        data-freshness={state.freshness}
        aria-label="Book and fleet summary"
      >
        <li
          className="money__cell money__cell--prov"
          data-testid="money-provenance"
        >
          <div className="money__body">
            <span className="money__label">Book</span>
            <span className="money__value">
              <ProvenanceChip provenance={p} />
            </span>
            <span className="money__sub">
              <CaveatPopover
                caveats={caveats}
                reasons={[source.detail]}
                title="About these figures"
                testId="money-caveats"
                label={`ⓘ${caveats.length}`}
              />
            </span>
          </div>
        </li>
        <Cell
          label="Equity"
          testId="money-equity"
          href={data.links.equity}
          sub={data.currency ?? undefined}
        >
          <Fig figure={data.equity} provenance={p} />
        </Cell>
        <Cell
          label="Day P&L"
          testId="money-day"
          href={data.links.day_pnl}
          sub={
            <>
              <Fig figure={data.day_pnl_pct} provenance={p} signed />
              {data.pnl_day ? (
                <span className="muted"> {data.pnl_day}</span>
              ) : null}
            </>
          }
        >
          <Fig figure={data.day_pnl} provenance={p} signed />
        </Cell>
        <Cell
          label="Week P&L"
          testId="money-week"
          href={data.links.day_pnl}
          sub={data.pnl_week_start ? `from ${data.pnl_week_start}` : undefined}
        >
          <Fig figure={data.week_pnl} provenance={p} signed />
        </Cell>
        <Cell
          label="Unrealised"
          testId="money-unrealised"
          href={data.links.positions}
        >
          <Fig figure={data.unrealised_pnl} provenance={p} signed />
        </Cell>
        <Cell
          label="Open positions"
          testId="money-positions"
          href={data.links.positions}
        >
          <Fig figure={data.open_positions} provenance={p} />
        </Cell>
        <Cell
          label="Bots"
          testId="money-bots"
          href={data.links.bots}
          tone={botTone}
          sub={
            <span data-testid="money-bots-breakdown">
              {count(bots.stale)} stale · {count(bots.halted)} halted ·{" "}
              {count(bots.idle)} idle
            </span>
          }
        >
          {bots.total.value === null ? (
            <FigureValue figure={bots.total} showChip={false} />
          ) : (
            <span className="num" data-testid="money-bots-running">
              {count(bots.running)} running / {count(bots.total)}
            </span>
          )}
        </Cell>
        <Cell
          label="Research jobs"
          testId="money-research"
          href={data.links.research}
        >
          <Fig figure={data.research_in_flight} provenance={p} />
        </Cell>
        <Cell
          label="New risk"
          testId="money-risk"
          href={data.links.risk}
          tone={
            data.kill_switch_active
              ? "critical"
              : data.new_risk_permitted
                ? undefined
                : "warn"
          }
          sub={
            data.kill_switch_active
              ? `kill switch ${data.kill_switch_mode ?? "armed"}`
              : "kill switch disarmed"
          }
        >
          <span
            className={`badge badge--${data.new_risk_permitted ? "ok" : "down"}`}
          >
            {data.new_risk_permitted ? "PERMITTED" : "BLOCKED"}
          </span>
        </Cell>
      </ul>
      {(data.books?.length ?? 0) > 1 ? (
        <ul className="money money--books" data-testid="money-books" aria-label="Each paper book">
          {data.books?.map((book) => (
            <li
              key={book.session_id}
              className="money__cell"
              data-testid="money-book"
              data-session={book.session_id}
              data-currency={book.currency}
              data-tone={book.abandoned.length > 0 ? "warn" : undefined}
            >
              <div className="money__body">
                <span className="money__label">{book.instruments || book.session_id}</span>
                <span className="money__value">
                  <Fig figure={book.equity} provenance={p} />
                  <span className="muted"> {book.currency}</span>
                </span>
                <span className="money__sub">
                  <span>
                    day <Fig figure={book.day_pnl} provenance={p} signed />
                    {" · "}
                    {book.open_positions.value ?? "–"} open
                  </span>
                  {book.abandoned.length > 0 ? (
                    <span
                      className="money__abandon"
                      data-testid="money-abandoned"
                      title={book.abandoned
                        .map(
                          (row) =>
                            `${row.instrument} ${row.direction} ${row.size.value ?? "no data"} @ ${row.entry_price.value ?? "no data"} (${row.at})`,
                        )
                        .join("; ")}
                    >
                      {book.abandoned.length} abandoned, P&L not carried
                    </span>
                  ) : null}
                </span>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="money__meta">
        {state.freshness === "stale" ||
        state.freshness === "disconnected" ||
        state.freshness === "lagging" ? (
          <StaleBadge
            info={{
              freshness: state.freshness,
              asOf: state.asOf,
              sourceAsOf: state.sourceAsOf,
              refreshError: state.refreshError,
            }}
            polling
            onRetry={state.reload}
          />
        ) : null}
        <span className="muted" title={source.detail}>
          as of {formatTimestamp(source.as_of)}
        </span>
      </div>
    </div>
  );
}
