"use client";

import Link from "next/link";
import { useApi } from "@/lib/api";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Card, PageHead, SourceBadge, Tile } from "@/components/primitives";
import type { Page, StrategyRow, ValidationRow } from "@/lib/types";

/** RESEARCH · Lab: the entry point to the research record. */
export default function ResearchLabPage() {
  const strategies = useApi<Page<StrategyRow>>("/api/research/strategies");
  const validation = useApi<Page<ValidationRow>>("/api/research/validation");

  return (
    <>
      <PageHead
        title="Research Lab"
        intro="One place to start: what is registered, what has been validated, and what the record can and cannot tell you."
      />
      <Card title="Registered strategies">
        <AsyncBoundary state={strategies} label="strategies" onRetry={strategies.reload}>
          {(page) => (
            <>
              <SourceBadge source={page.source} />
              <div className="tiles">
                {page.items.map((row) => (
                  <div className="tile" key={row.strategy_id}>
                    <div className="tile__label">{row.family}</div>
                    <div className="tile__value text-md">
                      <Link href="/research/strategies">{row.name}</Link>
                    </div>
                    <div className="tile__help">{row.hypothesis || "No hypothesis recorded."}</div>
                  </div>
                ))}
              </div>
            </>
          )}
        </AsyncBoundary>
      </Card>
      <Card title="Validation standing">
        <AsyncBoundary state={validation} label="validation reports" onRetry={validation.reload}>
          {(page) => (
            <>
              <SourceBadge source={page.source} />
              <ul>
                {page.items.map((row) => (
                  <li key={row.strategy_id}>
                    <span className="mono">{row.strategy_id}</span>{" "}
                    <span
                      className={`badge badge--${row.available ? "ok" : "degraded"}`}
                    >
                      {row.verdict}
                    </span>{" "}
                    <span className="muted">{row.detail}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </AsyncBoundary>
      </Card>
    </>
  );
}
