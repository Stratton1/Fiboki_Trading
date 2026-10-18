"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { apiKey, useApi } from "@/lib/query";
import type { Drawing, DrawingSetView, DrawingSetWrite, Envelope } from "@/lib/types";

/**
 * The signed-in operator's drawings for one symbol: read from
 * GET /api/markets/drawings/{symbol}, edited optimistically, saved with the
 * version they were edited from (PUT, or DELETE to clear).
 *
 *  - Optimistic: an edit shows at once; the save follows. Saves are serial:
 *    while one is in flight the latest set waits and is sent when it lands,
 *    based on the version the server just returned.
 *  - Conflict-safe: a 409 means the set changed elsewhere (another tab).
 *    Nothing on the server was overwritten; the newer set is loaded and the
 *    operator is told, rather than one window silently winning.
 *  - Undo/redo are local snapshots of the whole set; each is saved like any
 *    other edit, so an undo is itself versioned and audited.
 *
 * Drawings are annotations. Nothing here feeds a strategy, a stop or a size.
 */

export type SaveStatus =
  | { kind: "loading" }
  | { kind: "saved"; version: number }
  | { kind: "saving" }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string }
  | { kind: "unavailable"; message: string };

const HISTORY = 100;

interface Local {
  all: Drawing[];
  past: Drawing[][];
  future: Drawing[][];
}

export interface DrawingsHandle {
  /** The whole set, every timeframe. */
  all: Drawing[];
  others: { owner: string; drawing: Drawing }[];
  canEdit: boolean;
  status: SaveStatus;
  commit: (next: Drawing[]) => void;
  clear: () => void;
  undo: () => void;
  redo: () => void;
  retry: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

export function useDrawings(symbol: string, onNotice: (message: string) => void): DrawingsHandle {
  const path = `/api/markets/drawings/${encodeURIComponent(symbol)}`;
  const read = useApi<Envelope<DrawingSetView>>(path);
  const client = useQueryClient();
  const [local, setLocal] = useState<Local>({ all: [], past: [], future: [] });
  const [status, setStatus] = useState<SaveStatus>({ kind: "loading" });
  const localRef = useRef(local);
  const serverVersion = useRef<number | null>(null);
  const inFlight = useRef(false);
  const queued = useRef<{ drawings: Drawing[]; clear: boolean } | null>(null);
  const failed = useRef<{ drawings: Drawing[]; clear: boolean } | null>(null);
  const notice = useRef(onNotice);
  const reload = read.reload;
  useEffect(() => {
    notice.current = onNotice;
  });

  const server = read.status === "success" ? read.data.data : null;
  const readError = read.status === "error" ? read.error : null;

  const set = useCallback((next: Local) => {
    localRef.current = next;
    setLocal(next);
  }, []);

  // Server → local: on first load, and when nothing local is waiting to be saved.
  useEffect(() => {
    if (!server) return;
    if (inFlight.current || queued.current || failed.current) return;
    if (serverVersion.current !== null && server.version <= serverVersion.current) return;
    serverVersion.current = server.version;
    set({ all: server.drawings, past: [], future: [] });
    setStatus({ kind: "saved", version: server.version });
  }, [server, set]);

  const send = useCallback(async (): Promise<void> => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      // Serial: one request at a time, always based on the version the
      // previous one returned; the newest queued set wins.
      while (queued.current !== null && serverVersion.current !== null) {
        const job = queued.current;
        queued.current = null;
        setStatus({ kind: "saving" });
        const base = serverVersion.current;
        try {
          const env = job.clear
            ? await apiFetch<Envelope<DrawingSetView>>(`${path}?base_version=${base}`, {
                method: "DELETE",
              })
            : await apiFetch<Envelope<DrawingSetView>>(path, {
                method: "PUT",
                body: JSON.stringify({
                  base_version: base,
                  drawings: job.drawings,
                } satisfies DrawingSetWrite),
              });
          serverVersion.current = env.data.version;
          failed.current = null;
          client.setQueryData(apiKey(path), env);
          if (queued.current === null) setStatus({ kind: "saved", version: env.data.version });
        } catch (error) {
          queued.current = null;
          if (error instanceof ApiError && error.code === "drawings_conflict") {
            const message =
              "Not saved: your drawings for this symbol changed in another window. The newer set is shown; nothing was overwritten.";
            failed.current = null;
            serverVersion.current = null;
            setStatus({ kind: "conflict", message });
            notice.current(message);
            reload();
          } else {
            // Keep the edit on screen; Retry (or the next edit) sends it again.
            failed.current = job;
            const message =
              error instanceof ApiError
                ? `Not saved (${error.code}): ${error.message}`
                : "Not saved: the platform could not be reached.";
            setStatus({ kind: "error", message });
            notice.current(message);
          }
          break;
        }
      }
    } finally {
      inFlight.current = false;
    }
  }, [client, path, reload]);

  const canEdit = server?.can_edit === true;

  const save = useCallback(
    (drawings: Drawing[], clear: boolean) => {
      failed.current = null;
      queued.current = { drawings, clear };
      void send();
    },
    [send],
  );

  const commit = useCallback(
    (next: Drawing[], clear = false) => {
      if (!canEdit) return;
      const cur = localRef.current;
      set({ all: next, past: [...cur.past, cur.all].slice(-HISTORY), future: [] });
      save(next, clear);
    },
    [canEdit, save, set],
  );

  const undo = useCallback(() => {
    const cur = localRef.current;
    const previous = cur.past[cur.past.length - 1];
    if (!canEdit || previous === undefined) return;
    set({ all: previous, past: cur.past.slice(0, -1), future: [cur.all, ...cur.future].slice(0, HISTORY) });
    save(previous, false);
  }, [canEdit, save, set]);

  const redo = useCallback(() => {
    const cur = localRef.current;
    const next = cur.future[0];
    if (!canEdit || next === undefined) return;
    set({ all: next, past: [...cur.past, cur.all].slice(-HISTORY), future: cur.future.slice(1) });
    save(next, false);
  }, [canEdit, save, set]);

  const retry = useCallback(() => {
    const job = failed.current;
    if (job) save(job.drawings, job.clear);
  }, [save]);

  const others = useMemo(
    () =>
      (server?.others ?? []).flatMap((s) => s.drawings.map((drawing) => ({ owner: s.owner, drawing }))),
    [server],
  );

  // A read that failed outranks any save state: there is nothing to save onto.
  const shown: SaveStatus = readError
    ? {
        kind: "unavailable",
        message:
          readError.status === 401
            ? "Sign in to see and save drawings."
            : `Drawings could not be loaded: ${readError.message}`,
      }
    : status;

  return {
    all: local.all,
    others,
    canEdit,
    status: shown,
    commit: (next) => commit(next),
    clear: () => commit([], true),
    undo,
    redo,
    retry,
    canUndo: canEdit && local.past.length > 0,
    canRedo: canEdit && local.future.length > 0,
  };
}
