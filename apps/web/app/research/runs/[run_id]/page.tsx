import { RunTearsheet } from "@/components/research/tearsheet/RunTearsheet";

/**
 * RESEARCH · one stored run as a tearsheet: `/research/runs/<run_id>`, the
 * link every run, job output and chart backtest picker points at.
 *
 * A thin server entry around a client component (plan D-F1): any run id
 * renders on request (`dynamicParams`); one placeholder is prerendered so the
 * route has a static HTML file for the first-load budget. The page holds no
 * data until the browser reads it.
 */
export const dynamicParams = true;

/** A placeholder run id prerendered for the first-load measurement only. */
export function generateStaticParams() {
  return [{ run_id: "bt_0001" }];
}

export default async function RunPage({ params }: { params: Promise<{ run_id: string }> }) {
  const { run_id } = await params;
  return <RunTearsheet runId={decodeURIComponent(run_id)} />;
}
