import { IncidentDetail } from "./IncidentDetail";

/**
 * SYSTEM & INCIDENTS · one incident: `/system/incidents/<id>`, the deep link
 * the backend's incident read model and attention queue emit
 * (routers/incidents.py `deep_link`). Before this page existed every such
 * link was a 404 (inventory F-1).
 *
 * A thin server entry around a client component, like the chart workstation:
 * any id renders on request (`dynamicParams`); one placeholder id is
 * prerendered so the route has a static HTML file for the first-load budget.
 * The prerendered page holds no data: the incident is read in the browser.
 */
export const dynamicParams = true;

/** A placeholder id prerendered for the first-load measurement only. */
export function generateStaticParams() {
  return [{ id: "inc-1" }];
}

export default async function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <IncidentDetail id={decodeURIComponent(id)} />;
}
