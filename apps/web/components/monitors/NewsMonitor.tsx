"use client";

import Link from "next/link";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { CaveatList } from "@/components/CaveatList";
import { PanelStamp } from "@/components/workspace/PanelStamp";
import { useClock } from "@/lib/clock";
import { formatAge, formatTimestamp, formatUtcTime, parseUtc } from "@/lib/format";
import { NEWS_CURRENCIES, NEWS_HOURS, newsPath, type NewsView } from "@/lib/monitors";
import { useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";

export interface NewsFilter {
  instrument: string | null;
  currencies: string[];
  source: string | null;
  hours: number;
}

const STATE_WORD = { live: "RECORDING", stale: "STALE", never: "NOT RECORDING" } as const;

/**
 * NWS: recorded headlines, newest first BY OUR CLOCK (observed_at), filtered
 * by instrument, currency and source on the server. Each headline links to
 * the charts of the instruments its currency moves; the chart workstation
 * draws the same headlines as overlays. The recorder's state is the API's.
 */
export function NewsMonitor({
  filter,
  onFilter,
  onSymbol,
  compact = false,
}: {
  filter: NewsFilter;
  onFilter: (patch: Partial<NewsFilter>) => void;
  /** In a workspace tile: publish the chosen chart symbol to the link group. */
  onSymbol?: (symbol: string) => void;
  compact?: boolean;
}) {
  const path = newsPath(filter);
  const state = useApi<Envelope<NewsView>>(path, { refreshMs: 60_000 });
  const now = useClock();
  const available = state.status === "success" ? state.data.data.available_sources : [];
  return (
    <div className="nws" data-testid="news">
      <div className="ws-panel__bar" role="group" aria-label="News filters">
        {filter.instrument ? (
          <span className="mono ws-chart__sym" data-testid="news-instrument">
            {filter.instrument}
            <button type="button" className="ws-icon" aria-label="Clear the instrument filter" onClick={() => onFilter({ instrument: null })}>
              ×
            </button>
          </span>
        ) : null}
        <span className="ws-inline" role="group" aria-label="Currencies">
          {NEWS_CURRENCIES.map((c) => {
            const on = filter.currencies.includes(c);
            return (
              <button
                key={c}
                type="button"
                className="ws-btn ws-btn--ghost"
                aria-pressed={on}
                onClick={() => onFilter({ currencies: on ? filter.currencies.filter((x) => x !== c) : [...filter.currencies, c] })}
                data-testid={`news-ccy-${c}`}
              >
                {c}
              </button>
            );
          })}
        </span>
        <label className="ws-inline">
          <span className="sr-only">Source</span>
          <select value={filter.source ?? ""} onChange={(e) => onFilter({ source: e.target.value || null })} data-testid="news-source">
            <option value="">All sources</option>
            {available.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="ws-inline">
          <span className="sr-only">Window</span>
          <select value={filter.hours} onChange={(e) => onFilter({ hours: Number(e.target.value) })} data-testid="news-hours">
            {NEWS_HOURS.map((h) => (
              <option key={h} value={h}>
                {h < 24 ? `${h}h` : `${h / 24}d`}
              </option>
            ))}
          </select>
        </label>
      </div>
      <AsyncBoundary
        state={state}
        label="the news monitor"
        onRetry={state.reload}
        isEmpty={(e) => e.data.headlines.length === 0}
        emptyTitle="No headlines in the window"
        emptyBody="The headline store answered with nothing observed in this window for these filters. If the recorder is not recording, the stamp above says so."
      >
        {(envelope) => {
          const view = envelope.data;
          return (
            <>
              <PanelStamp
                source={envelope.source}
                extra={`${STATE_WORD[view.recorder_state]} · ${view.headlines.length} headline(s)`}
              />
              {view.recorder_state !== "live" ? (
                <p className="nws__recorder" data-state={view.recorder_state} data-testid="news-recorder" role="status">
                  {STATE_WORD[view.recorder_state]}: {view.recorder_detail}
                </p>
              ) : null}
              <CaveatList caveats={envelope.caveats.filter((c) => c.severity !== "info" || !compact)} />
              <ol className="nws__list" data-testid="news-list">
                {view.headlines.map((h) => (
                  <li key={h.id} className="nws__item" data-source={h.source} data-testid="news-item">
                    <span className="nws__time mono" title={`Observed ${formatTimestamp(h.observed_at)}`}>
                      {formatUtcTime(h.observed_at)}
                      <span className="muted"> {formatAge(Math.max(0, (now - parseUtc(h.observed_at).getTime()) / 1000))}</span>
                    </span>
                    <span className="nws__src mono">{h.source}</span>
                    {h.currency ? <span className="nws__ccy mono">{h.currency}</span> : <span className="nws__ccy muted">—</span>}
                    <a className="nws__title" href={h.url} target="_blank" rel="noopener noreferrer" data-testid="news-title">
                      {h.title}
                    </a>
                    <span className="nws__links">
                      {h.chart_links.map((l) =>
                        onSymbol ? (
                          <button key={l.symbol} type="button" className="ws-sym" onClick={() => onSymbol(l.symbol)} data-testid="news-chart">
                            {l.symbol}
                          </button>
                        ) : (
                          <Link key={l.symbol} href={l.href} className="mono" prefetch={false} data-testid="news-chart">
                            {l.symbol}
                          </Link>
                        ),
                      )}
                    </span>
                    {!compact && h.vendor_published_at ? (
                      <span className="nws__vendor muted" title="The vendor's own timestamp, not used for ordering">
                        vendor {formatUtcTime(h.vendor_published_at)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ol>
            </>
          );
        }}
      </AsyncBoundary>
    </div>
  );
}
