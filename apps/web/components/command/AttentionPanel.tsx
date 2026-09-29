"use client";

import Link from "next/link";
import type { KeyboardEvent } from "react";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { AttentionItem, Page } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { CaveatList, SourceBadge } from "../primitives";
import { SeverityBadge } from "./SeverityBadge";

/**
 * COMMAND · the attention queue (plan §4, report E §6.6 item 2).
 *
 * "Is anything wrong, and what needs me now?" The ranking is the server's:
 * ranking is logic, and logic belongs in the backend. This panel renders the
 * items in the order received; it never sorts, filters or re-weights them.
 * The position shown is the item's place in that order, and the score is the
 * server's, with its provenance.
 *
 * Keyboard: each item is a link, so Tab reaches it and Enter opens its deep
 * link; ArrowDown/ArrowUp (or j/k) move between items. A deep link that is not
 * an in-app path is shown but not followed.
 */

export const ATTENTION_PATH = "/api/command/attention";
const ATTENTION_REFRESH_MS = 15_000;

/** Only an in-app path is followed; anything else could leave the workstation. */
function inAppLink(link: string | null): string | null {
  if (!link || !link.startsWith("/") || link.startsWith("//") || link.startsWith("/\\")) return null;
  return link;
}

function moveFocus(event: KeyboardEvent<HTMLOListElement>) {
  const key = event.key;
  const down = key === "ArrowDown" || key === "j";
  const up = key === "ArrowUp" || key === "k";
  if (!down && !up) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const links = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>("[data-attention-focus]"),
  );
  const index = links.indexOf(document.activeElement as HTMLElement);
  if (index === -1) return;
  const next = links[down ? Math.min(index + 1, links.length - 1) : Math.max(index - 1, 0)];
  event.preventDefault();
  next?.focus();
}

function Body({ item, position }: { item: AttentionItem; position: number }) {
  return (
    <>
      <span className="mono muted" aria-label={`position ${position}`}>
        #{position}
      </span>
      <SeverityBadge severity={item.severity} />
      <strong>{item.title}</strong>
      <span className="attention-item__detail">
        {item.reason}
        {item.as_of ? ` · as of ${formatTimestamp(item.as_of)}` : ""} · score{" "}
        <FigureValue figure={item.score} />
      </span>
    </>
  );
}

export function AttentionPanel() {
  const state = useApi<Page<AttentionItem>>(ATTENTION_PATH, { refreshMs: ATTENTION_REFRESH_MS });
  return (
    <AsyncBoundary
      state={state}
      label="the attention queue"
      onRetry={state.reload}
      isEmpty={(page) => page.items.length === 0}
      emptyTitle="Nothing needs you"
      emptyBody="The platform's attention queue is empty. That is the server's answer, not an absence of one."
    >
      {(page) => (
        <>
          <SourceBadge source={page.source} />
          <CaveatList caveats={page.caveats} />
          <ol className="attention-list" data-testid="attention-list" onKeyDown={moveFocus}>
            {page.items.map((item, index) => {
              const href = inAppLink(item.deep_link);
              return (
                <li
                  key={item.id}
                  data-testid="attention-item"
                  data-item-id={item.id}
                  data-position={index + 1}
                  data-severity={item.severity}
                >
                  {href ? (
                    <Link
                      href={href}
                      className="attention-item"
                      data-attention-focus=""
                      data-testid={`attention-link-${item.id}`}
                    >
                      <Body item={item} position={index + 1} />
                    </Link>
                  ) : (
                    <div
                      className="attention-item"
                      tabIndex={0}
                      data-attention-focus=""
                      data-testid={`attention-nolink-${item.id}`}
                    >
                      <Body item={item} position={index + 1} />
                      <span className="attention-item__detail">
                        No in-app link was supplied for this item.
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
          <p className="muted mt-2">
            {page.items.length} of {page.total}, in the platform&apos;s order.
          </p>
        </>
      )}
    </AsyncBoundary>
  );
}
