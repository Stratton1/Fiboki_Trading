/**
 * The running stream's controls, for the DISCONNECTED banner's Reconnect
 * button. Kept apart from the (lazy-loaded) stream client so the shell can
 * offer the button without bundling the client into the first load.
 */

export interface StreamControl {
  reconnect(): void;
  stop(): void;
}

let control: StreamControl | null = null;

export function setStreamControl(next: StreamControl | null) {
  control = next;
}

export function reconnectStream() {
  control?.reconnect();
}

/**
 * Counters for the Playwright suite. The suite runs against the production
 * build that the byte budgets measure, so these are switched on at run time,
 * not build time: nothing is recorded unless the test installed
 * `window.__fibokiTestHooks` before the page loaded.
 */
export function testHooks(): Record<string, unknown> | null {
  if (typeof window === "undefined") return null;
  const hooks = (window as unknown as { __fibokiTestHooks?: unknown }).__fibokiTestHooks;
  return hooks !== null && typeof hooks === "object" ? (hooks as Record<string, unknown>) : null;
}

export function countTestHook(name: string) {
  const hooks = testHooks();
  if (!hooks) return;
  const current = hooks[name];
  hooks[name] = (typeof current === "number" ? current : 0) + 1;
}
