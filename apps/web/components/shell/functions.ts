import { route } from "@/lib/routes";
import { tierOf, type Hit } from "@/lib/search";
import { SECTIONS } from "./sections";

/**
 * The function registry: every command-line code, what it opens and the keys
 * that reach it. The command line, the palette, the shortcut sheet and the
 * function directory (/system/functions) are all generated from it, so they
 * cannot disagree.
 *
 * Two kinds of function:
 *  - SCREEN functions: a code alone (`RSK`, `FLT`), from sections.ts;
 *  - INSTRUMENT functions: `<SYMBOL> <FN>` (`EURUSD H4`, `EURUSD BT`). The
 *    symbol must be one the platform lists; unknown text never navigates.
 */

export interface ScreenFunction {
  code: string;
  label: string;
  section: string;
  href: string;
  description: string;
  keys: string[];
}

export interface InstrumentFunction {
  code: string;
  label: string;
  description: string;
  href: (symbol: string) => string;
}

export const SCREEN_FUNCTIONS: ScreenFunction[] = SECTIONS.flatMap((section, sectionIndex) =>
  section.links.map((link, index) => ({
    code: link.code,
    label: link.label,
    section: section.title,
    href: link.href,
    description: link.description,
    keys:
      index === 0
        ? [...(sectionIndex === 4 ? [] : [`F${sectionIndex + 1}`]), `g ${section.chord}`]
        : [],
  })),
);

/** Codes that are not screens: they open a dialog or the directory. */
export const COMMAND_CODES: { code: string; label: string; description: string; keys: string[] }[] = [
  { code: "HELP", label: "Function directory", description: "Every function and key (also FUNC).", keys: ["?"] },
  { code: "KILL", label: "Kill switch…", description: "Opens the kill-switch dialog. It never arms by itself.", keys: ["⇧K"] },
  // wave workspace
  { code: "LAYOUT", label: "Open a layout", description: "LAYOUT <name> opens that named workspace layout (a new name starts from the FX monitoring preset).", keys: [] },
];

export const INSTRUMENT_FUNCTIONS: InstrumentFunction[] = [
  { code: "GP", label: "Chart", description: "Chart workstation, H1 (also just the symbol).", href: (s) => route.instrument(s) },
  { code: "H1", label: "Chart H1", description: "Chart at one-hour bars.", href: (s) => route.instrument(s, { tf: "H1" }) },
  { code: "H4", label: "Chart H4", description: "Chart at four-hour bars.", href: (s) => route.instrument(s, { tf: "H4" }) },
  { code: "D1", label: "Chart D1", description: "Chart at daily bars.", href: (s) => route.instrument(s, { tf: "D1" }) },
  {
    code: "BT",
    label: "Backtest trades",
    description: "Backtest trades on this instrument in the journal; runs are on the chart's run picker.",
    href: (s) => `${route.journal({ instrument: s })}&provenance=backtest`,
  },
  { code: "TRD", label: "Trades", description: "Every closed trade on this instrument.", href: (s) => route.journal({ instrument: s }) },
  {
    code: "NEWS",
    label: "News",
    description: "The news monitor filtered to this instrument's currencies (NWS).",
    href: (s) => route.news(s),
  },
  {
    code: "CAL",
    label: "Calendar on chart",
    description: "Economic events are drawn on the chart workstation; a calendar panel is Wave 2A.",
    href: (s) => route.instrument(s),
  },
  { code: "DES", label: "Description", description: "The instrument's row in the explorer.", href: (s) => `/markets?row=${encodeURIComponent(s)}` },
  { code: "DQ", label: "Data quality", description: "The instrument's dataset quality.", href: (s) => `/markets/data-quality?row=${encodeURIComponent(s)}` },
];

const FUNCTION_ALIASES: Record<string, string> = { FUNC: "/system/functions", HELP: "/system/functions", "?": "/system/functions" };

/** A screen code typed exactly (case-insensitive), or null. */
export function screenByCode(code: string): ScreenFunction | null {
  const want = code.trim().toUpperCase();
  return SCREEN_FUNCTIONS.find((f) => f.code === want) ?? null;
}

export function aliasHref(code: string): string | null {
  return FUNCTION_ALIASES[code.trim().toUpperCase()] ?? null;
}

export function instrumentFunction(code: string): InstrumentFunction | null {
  const want = code.trim().toUpperCase();
  return INSTRUMENT_FUNCTIONS.find((f) => f.code === want) ?? null;
}

/** Registry entries matching a query, as search hits. */
export function registryHits(query: string): Hit[] {
  const hits: Hit[] = [];
  for (const f of SCREEN_FUNCTIONS) {
    const byCode = tierOf(query, f.code, f.code);
    const byLabel = tierOf(query, f.label, `${f.section} ${f.label}`);
    const tier = byCode === 0 ? 0 : byLabel === null ? byCode : byCode === null ? byLabel : Math.min(byCode, byLabel);
    if (tier === null) continue;
    hits.push({
      kind: byCode === 0 ? "function" : "screen",
      id: f.code,
      label: f.label,
      detail: `${f.section} · ${f.description}`,
      href: f.href,
      tier,
    });
  }
  return hits;
}

export type Parsed =
  | { kind: "empty" }
  | { kind: "screen"; fn: ScreenFunction }
  | { kind: "alias"; href: string; code: string }
  | { kind: "command"; code: "KILL" }
  | { kind: "instrument"; symbol: string; fn: InstrumentFunction | null; rest: string }
  | { kind: "text"; text: string };

/**
 * The grammar. `symbols` is the platform's instrument list (upper case); a
 * first word that is not in it is never treated as a symbol.
 */
export function parseCommand(text: string, symbols: ReadonlySet<string>): Parsed {
  const raw = text.trim();
  if (raw === "") return { kind: "empty" };
  const words = raw.split(/\s+/);
  const first = (words[0] ?? "").toUpperCase();
  // wave workspace: LAYOUT <name> opens a named workspace layout.
  if (first === "LAYOUT") {
    const name = raw.slice((words[0] ?? "").length).trim();
    return { kind: "alias", href: name === "" ? "/workspace" : `/workspace?layout=${encodeURIComponent(name)}`, code: name === "" ? "LAYOUT" : `LAYOUT ${name}` };
  }
  if (words.length === 1) {
    if (first === "KILL" || first === "HALT") return { kind: "command", code: "KILL" };
    const alias = aliasHref(first);
    if (alias) return { kind: "alias", href: alias, code: first };
    const screen = screenByCode(first);
    if (screen) return { kind: "screen", fn: screen };
  }
  if (symbols.has(first)) {
    const rest = words.slice(1).join(" ");
    const fn = rest === "" ? (INSTRUMENT_FUNCTIONS[0] ?? null) : instrumentFunction(rest);
    return { kind: "instrument", symbol: first, fn, rest };
  }
  return { kind: "text", text: raw };
}
