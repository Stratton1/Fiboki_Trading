import { LifecycleEntity } from "./LifecycleEntity";

/**
 * STRATEGY LIFECYCLE · one strategy, by content hash: `/lifecycle/<hash>`,
 * the deep link the backend's attention queue emits for strategy-review
 * items (routers/command.py). Before this page existed every such link was a
 * 404 (inventory F-2).
 *
 * A thin server entry around a client component: any hash renders on request
 * (`dynamicParams`); one placeholder is prerendered so the route has a static
 * HTML file for the first-load budget. The page holds no data until the
 * browser reads it.
 */
export const dynamicParams = true;

/** A placeholder hash prerendered for the first-load measurement only. */
export function generateStaticParams() {
  return [{ hash: "abc123def456" }];
}

export default async function LifecyclePage({ params }: { params: Promise<{ hash: string }> }) {
  const { hash } = await params;
  return <LifecycleEntity hash={decodeURIComponent(hash)} />;
}
