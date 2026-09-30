import { ChartWorkstation } from "./ChartWorkstation";

/**
 * MARKETS · chart workstation for one instrument: `/markets/EURUSD?tf=H4`.
 *
 * A thin server entry around a client component (plan D-F1: everything that
 * reads the API is client-side). Any registered symbol renders on request
 * (`dynamicParams`); one is prerendered at build time so the route has a
 * static HTML file that scripts/first-load.mjs can measure against its
 * first-load budget, like every other route. The prerendered page holds no
 * data: bars and overlays are read in the browser, as on every page.
 */
export const dynamicParams = true;

/** The representative instrument prerendered for the first-load measurement. */
export function generateStaticParams() {
  return [{ symbol: "EURUSD" }];
}

export default async function ChartPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol } = await params;
  return <ChartWorkstation symbol={decodeURIComponent(symbol)} />;
}
