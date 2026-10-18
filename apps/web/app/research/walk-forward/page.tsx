"use client";

import Link from "next/link";
import { parseAsString, useQueryState } from "nuqs";
import { UrlState } from "@/components/UrlState";
import { PageHead } from "@/components/primitives";
import { ResearchNav } from "@/components/research/ResearchNav";
import { WalkForwardPanel } from "@/components/research/WalkForwardView";
import { useApi } from "@/lib/query";
import { researchHref } from "@/lib/research";
import type { Page, StrategyRow } from "@/lib/types";

const parser = parseAsString.withOptions({ history: "replace" });

/**
 * RESEARCH · Walk-forward (feature 25): `/research/walk-forward?strategy=<id>`.
 * The ladder's rung-2 folds for one strategy, from its stored ladder report.
 */
export default function WalkForwardPage() {
  return (
    <UrlState fallback={<WalkForward strategy={null} source={null} onStrategy={() => undefined} />}>
      <FromUrl />
    </UrlState>
  );
}

function FromUrl() {
  const [strategy, setStrategy] = useQueryState("strategy", parser);
  const [source] = useQueryState("source", parser);
  return <WalkForward strategy={strategy} source={source} onStrategy={(s) => void setStrategy(s)} />;
}

function WalkForward({
  strategy,
  source,
  onStrategy,
}: {
  strategy: string | null;
  source: string | null;
  onStrategy: (s: string) => void;
}) {
  const strategies = useApi<Page<StrategyRow>>("/api/research/strategies");
  const chosen =
    strategy ?? (strategies.status === "success" ? (strategies.data.items[0]?.strategy_id ?? null) : null);
  return (
    <div className="rs-page">
      <ResearchNav />
      <PageHead
        title="Walk-forward"
        intro="The ladder's rung 2 as filed: each fold's in-sample selection window and the out-of-sample window it was judged on, the two profit rates side by side, and how stable they are."
      />
      <div className="rs-toolbar">
        <label htmlFor="wf-strategy">Strategy</label>
        <select id="wf-strategy" data-testid="wf-strategy" value={chosen ?? ""} onChange={(e) => onStrategy(e.target.value)}>
          {strategies.status === "success"
            ? strategies.data.items.map((s) => (
                <option key={s.strategy_id} value={s.strategy_id}>
                  {s.name}
                </option>
              ))
            : null}
        </select>
        {chosen ? <Link href={researchHref.strategy(chosen)}>Strategy page</Link> : null}
      </div>
      {chosen ? (
        <WalkForwardPanel key={`${chosen}:${source ?? ""}`} strategyId={chosen} source={source} />
      ) : strategies.status === "error" ? (
        <div className="state state--error" role="alert">
          <div className="state__title">No strategy to read</div>
          <div className="state__body">The strategy list could not be loaded ({strategies.error.code}).</div>
        </div>
      ) : null}
    </div>
  );
}
