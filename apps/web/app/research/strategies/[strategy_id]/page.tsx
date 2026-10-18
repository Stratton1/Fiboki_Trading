import { StrategyEntity } from "@/components/research/StrategyEntity";

/**
 * RESEARCH · one strategy: `/research/strategies/<strategy_id>`, with its
 * runs, lifecycle state, ladder history and links. The command line, the
 * palette and the lifecycle pages link here by strategy id.
 *
 * Thin server entry (plan D-F1); one placeholder is prerendered for the
 * first-load measurement, every other id renders on request.
 */
export const dynamicParams = true;

export function generateStaticParams() {
  return [{ strategy_id: "donchian_breakout_atr" }];
}

export default async function StrategyPage({ params }: { params: Promise<{ strategy_id: string }> }) {
  const { strategy_id } = await params;
  return <StrategyEntity strategyId={decodeURIComponent(strategy_id)} />;
}
