/** Static map of strategy IDs to human-readable names.
 *  Matches backend strategy_name properties exactly.
 *  Used across Research, Backtests, Scenarios, Bots pages.
 */
export const STRATEGY_NAMES: Record<string, string> = {
  bot01: "Pure Sanyaku Confluence",
  bot02: "Kijun-sen Pullback",
  bot03: "Flat Senkou Span B Bounce",
  bot04: "Chikou Open Space Momentum",
  bot05: "MTFA Sanyaku",
  bot06: "N-Wave Structural Targeting",
  bot07: "Kumo Twist Anticipator",
  bot08: "Kihon Suchi Time Cycle Confluence",
  bot09: "Golden Cloud Confluence",
  bot10: "Kijun + 38.2% Shallow Continuation",
  bot11: "Sanyaku + Fib Extension Targets",
  bot12: "Kumo Twist + Fibonacci Time Zone",
};

/** Returns "bot01 — Pure Sanyaku Confluence" or just the id if unknown. */
export function strategyLabel(id: string): string {
  const name = STRATEGY_NAMES[id];
  return name ? `${id} — ${name}` : id;
}

/** Returns just the short human name, or the id if unknown. */
export function strategyShortName(id: string): string {
  return STRATEGY_NAMES[id] ?? id;
}
