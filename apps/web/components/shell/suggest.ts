"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api";
import { apiKey } from "@/lib/query";
import { rankHits, tierOf, type Hit } from "@/lib/search";
import type { InstrumentRow, Page, SearchHit } from "@/lib/types";
import { INSTRUMENT_FUNCTIONS, parseCommand, registryHits } from "./functions";

/**
 * Suggestions for the command line and the palette, from one ranking:
 * the function registry, the platform's instrument list (matched here, so an
 * instrument appears without a round trip) and GET /api/search for every
 * other entity, merged exact-first by lib/search.ts.
 */

const INSTRUMENTS_PATH = "/api/markets/instruments";
const SEARCH_DEBOUNCE_MS = 90;
const MAX_SUGGESTIONS = 8;

export const KIND_LABEL: Record<Hit["kind"], string> = {
  function: "FN",
  screen: "SCREEN",
  instrument: "INSTR",
  strategy: "STRAT",
  session: "BOT",
  candidate: "LIFE",
  run: "RUN",
  trade: "TRADE",
  incident: "INC",
};


/**
 * The instrument list the platform publishes, fetched once the line is first
 * used. Read through the query cache directly (shared with every page that
 * lists instruments) rather than `useApi`, so a lookup list never counts as
 * data on screen in the status bar's "data as of".
 */
function useSymbols(active: boolean): { symbols: ReadonlySet<string>; rows: InstrumentRow[]; settled: boolean } {
  const instruments = useQuery<Page<InstrumentRow>>({
    queryKey: apiKey(INSTRUMENTS_PATH),
    enabled: active,
    staleTime: 5 * 60_000,
  });
  const settled = instruments.isSuccess || instruments.isError;
  return useMemo(() => {
    const rows = instruments.data?.items ?? [];
    return { symbols: new Set(rows.map((r) => r.symbol.toUpperCase())), rows, settled };
  }, [instruments.data, settled]);
}

/** GET /api/search, debounced; stale answers for an older query are dropped. */
function useEntitySearch(query: string): { hits: Hit[]; pending: boolean } {
  const [state, setState] = useState<{ q: string; hits: Hit[] }>({ q: "", hits: [] });
  const q = query.trim();
  useEffect(() => {
    if (q.length < 2) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      apiFetch<Page<SearchHit>>(`/api/search?q=${encodeURIComponent(q)}&limit=12`, { signal: controller.signal })
        .then((page) =>
          setState({
            q,
            hits: page.items.map((h) => ({ kind: h.kind, id: h.id, label: h.label, detail: h.detail, href: h.href, tier: h.tier })),
          }),
        )
        .catch(() => undefined);
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [q]);
  return { hits: state.q === q ? state.hits : [], pending: q.length >= 2 && state.q !== q };
}

/** Suggestions for the current text: registry, instruments and API entities, ranked once. */
export function useSuggestions(text: string, active: boolean, limit = MAX_SUGGESTIONS) {
  const { symbols, rows, settled } = useSymbols(active);
  const parsed = parseCommand(text, symbols);
  const words = text.trim().split(/\s+/);
  const entity = useEntitySearch(parsed.kind === "text" ? text : "");
  const hits = useMemo(() => {
    const q = text.trim();
    if (q === "") return [];
    // "<SYMBOL> <partial function>": the instrument's functions.
    if (parsed.kind === "instrument") {
      const partial = parsed.rest.toUpperCase();
      // The bare symbol is an exact match for the instrument itself; its
      // functions follow.
      const self: Hit[] =
        partial === ""
          ? [
              {
                kind: "instrument",
                id: parsed.symbol,
                label: parsed.symbol,
                detail: "chart workstation",
                href: `/markets/${encodeURIComponent(parsed.symbol)}`,
                tier: 0,
              },
            ]
          : [];
      return [...self, ...INSTRUMENT_FUNCTIONS.filter((fn) => partial !== "" || fn.code !== "GP").flatMap((fn): Hit[] => {
        const tier = partial === "" ? 1 : tierOf(partial, fn.code, fn.label);
        return tier === null
          ? []
          : [
              {
                kind: "function",
                id: `${parsed.symbol} ${fn.code}`,
                label: `${parsed.symbol} ${fn.label}`,
                detail: fn.description,
                href: fn.href(parsed.symbol),
                tier,
              },
            ];
      })].slice(0, limit);
    }
    const local: Hit[] = [...registryHits(q)];
    if (words.length === 1) {
      for (const row of rows) {
        const tier = tierOf(q, row.symbol, row.symbol);
        if (tier !== null) {
          local.push({
            kind: "instrument",
            id: row.symbol,
            label: row.symbol,
            detail: `${row.asset_class} · chart`,
            href: `/markets/${encodeURIComponent(row.symbol)}`,
            tier,
          });
        }
      }
    }
    return rankHits([...local, ...entity.hits]).slice(0, limit);
  }, [text, parsed, rows, entity.hits, words.length, limit]);
  // Not ready: the instrument list or the entity search for this text is
  // still in flight, so Enter must wait rather than decide on half the facts.
  return { parsed, hits, symbols, pending: entity.pending || (active && !settled) };
}

