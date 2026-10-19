import { SessionView } from "@/components/ops/SessionView";

/**
 * FLEET · one paper bot (session): `/trading/sessions/<session_id>` (Wave 3O).
 *
 * Its control state and history, the backtest-versus-paper reconciliation,
 * the advisory maturity report and its hash-chained audit log. A thin server
 * entry around a client component: any session id renders on request; one
 * placeholder is prerendered so the route has a static HTML file for the
 * first-load budget.
 */
export const dynamicParams = true;

/** A placeholder session id prerendered for the first-load measurement only. */
export function generateStaticParams() {
  return [{ session_id: "forward-paper_forward_v1" }];
}

export default async function SessionPage({ params }: { params: Promise<{ session_id: string }> }) {
  const { session_id } = await params;
  return <SessionView sessionId={decodeURIComponent(session_id)} />;
}
