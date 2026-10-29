/** Clock skew is a caveat. It never rewrites a quote or a P&L figure. */

export const CLOCK_SKEW_MS = 5_000;
const SKEW_EVENT = "fiboki-clock-skew";

export function noteClockSkew(raw: string): number | null {
  try {
    const body = JSON.parse(raw) as { server_time?: string };
    const server = body.server_time ? Date.parse(body.server_time) : Number.NaN;
    if (!Number.isFinite(server)) return null;
    const skew = Math.abs(Date.now() - server);
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(SKEW_EVENT, { detail: skew }));
    }
    return skew;
  } catch {
    return null;
  }
}

export function onClockSkew(listener: (skewMs: number) => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const handler = (event: Event) => {
    const skew = (event as CustomEvent<number>).detail;
    if (typeof skew === "number") listener(skew);
  };
  window.addEventListener(SKEW_EVENT, handler);
  return () => window.removeEventListener(SKEW_EVENT, handler);
}
