import useSWR from "swr";
import { api } from "@/lib/api";

export interface Watchlist {
  id: number;
  name: string;
  instrument_ids: string[];
}

const ACTIVE_KEY = "fiboki_active_watchlist";

function loadActiveId(): number | null {
  if (typeof window === "undefined") return null;
  const v = localStorage.getItem(ACTIVE_KEY);
  return v ? Number(v) : null;
}

function saveActiveId(id: number | null) {
  if (typeof window === "undefined") return;
  if (id === null) localStorage.removeItem(ACTIVE_KEY);
  else localStorage.setItem(ACTIVE_KEY, String(id));
}

export function useWatchlists() {
  const { data, error, isLoading, mutate } = useSWR<Watchlist[]>(
    "/watchlists",
    () => api.listWatchlists(),
  );

  const watchlists = data ?? [];

  // Resolve active watchlist — fall back to first if stored id no longer exists
  const storedId = loadActiveId();
  const active = watchlists.find((w) => w.id === storedId) ?? null;

  const setActive = (id: number | null) => {
    saveActiveId(id);
    mutate(); // trigger re-render
  };

  const create = async (name: string, instrumentIds: string[]) => {
    const wl = await api.createWatchlist({ name, instrument_ids: instrumentIds });
    await mutate();
    return wl;
  };

  const update = async (id: number, updates: { name?: string; instrument_ids?: string[] }) => {
    const wl = await api.updateWatchlist(id, updates);
    await mutate();
    return wl;
  };

  const remove = async (id: number) => {
    await api.deleteWatchlist(id);
    if (storedId === id) saveActiveId(null);
    await mutate();
  };

  const addInstrument = async (watchlistId: number, symbol: string) => {
    const wl = watchlists.find((w) => w.id === watchlistId);
    if (!wl || wl.instrument_ids.includes(symbol)) return;
    return update(watchlistId, { instrument_ids: [...wl.instrument_ids, symbol] });
  };

  const removeInstrument = async (watchlistId: number, symbol: string) => {
    const wl = watchlists.find((w) => w.id === watchlistId);
    if (!wl) return;
    return update(watchlistId, {
      instrument_ids: wl.instrument_ids.filter((s) => s !== symbol),
    });
  };

  /** Instrument symbols in the active watchlist, or null if no watchlist active */
  const filterSet: Set<string> | null = active
    ? new Set(active.instrument_ids)
    : null;

  return {
    watchlists,
    active,
    filterSet,
    error,
    isLoading,
    mutate,
    setActive,
    create,
    update,
    remove,
    addInstrument,
    removeInstrument,
  };
}
