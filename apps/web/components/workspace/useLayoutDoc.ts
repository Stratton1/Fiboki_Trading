"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiFetch } from "@/lib/api";
import { apiKey, invalidatePath, useApi } from "@/lib/query";
import type { Envelope, Page } from "@/lib/types";
import {
  PATHS,
  saveLayout,
  type LayoutDoc,
  type LayoutPreset,
  type LayoutView,
} from "@/lib/workspace";

/** How long after the last edit the workstation autosaves. */
export const AUTOSAVE_MS = 1_500;

export type SaveStatus =
  | { kind: "loading" }
  | { kind: "clean"; savedAt: string | null }
  | { kind: "dirty" }
  | { kind: "saving"; action: "save" | "autosave" }
  | { kind: "conflict"; current: number; message: string }
  | { kind: "error"; message: string }
  | { kind: "new" };

export interface LayoutDocState {
  name: string;
  doc: LayoutDoc | null;
  /** The server version this doc was edited from (0: not saved under this name yet). */
  version: number;
  canEdit: boolean;
  status: SaveStatus;
  /** True when the doc differs from what the server holds. */
  dirty: boolean;
  edit: (fn: (doc: LayoutDoc) => LayoutDoc) => void;
  /** Replace the doc wholesale (a preset loaded into this name). */
  replace: (doc: LayoutDoc) => void;
  save: (action?: "save" | "autosave") => Promise<boolean>;
  /** Resolve a 409: take the server's version, or write mine over it. */
  resolve: (choice: "theirs" | "mine") => Promise<void>;
  loadError: ApiError | null;
}

/**
 * One named layout, edited locally and kept on the server.
 *
 *  - Loads GET /api/workspace/layouts/<name>; a 404 starts from the
 *    "FX monitoring" preset as an unsaved, version-0 layout under that name.
 *  - Every edit marks the layout UNSAVED and (for an operator) schedules a
 *    debounced autosave; ⌘/Ctrl+S saves at once. The server keeps every
 *    version and who wrote it (save or autosave).
 *  - A write is based on the version it was edited from; a 409 means another
 *    tab or browser saved first. Nothing is overwritten silently: the operator
 *    chooses theirs (reload) or mine (write over, as a new version).
 */
export function useLayoutDoc(name: string, presetKey = "fx-monitoring"): LayoutDocState {
  const client = useQueryClient();
  const remote = useApi<Envelope<LayoutView>>(PATHS.layout(name));
  const presets = useApi<Page<LayoutPreset>>(PATHS.presets);
  const [doc, setDoc] = useState<LayoutDoc | null>(null);
  const [version, setVersion] = useState(0);
  const [canEdit, setCanEdit] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState<SaveStatus>({ kind: "loading" });
  const [adopted, setAdopted] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const docRef = useRef<LayoutDoc | null>(null);
  const versionRef = useRef(0);
  const editsRef = useRef(0);
  useEffect(() => {
    docRef.current = doc;
    versionRef.current = version;
  });

  const notFound = remote.status === "error" && remote.error.status === 404;
  const loadError = remote.status === "error" && !notFound ? remote.error : null;

  // Adopt the server's layout (or the preset for a name never saved) once per
  // name, and again whenever the server holds a newer version and nothing is
  // being edited here.
  const remoteVersion = remote.status === "success" ? remote.data.data.version : null;
  const key = `${name}|${remoteVersion ?? (notFound ? "404" : "?")}`;
  if (adopted !== key && !dirty) {
    if (remote.status === "success") {
      setAdopted(key);
      setDoc(remote.data.data.layout);
      setVersion(remote.data.data.version);
      setCanEdit(remote.data.data.can_edit);
      setStatus({ kind: "clean", savedAt: remote.data.data.updated_at });
    } else if (notFound && presets.status === "success") {
      const preset = presets.data.items.find((p) => p.key === presetKey) ?? presets.data.items[0];
      if (preset) {
        setAdopted(key);
        setDoc(preset.layout);
        setVersion(0);
        setStatus({ kind: "new" });
      }
    }
  }

  const save = useCallback(
    async (action: "save" | "autosave" = "save"): Promise<boolean> => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        timer.current = null;
      }
      const current = docRef.current;
      if (!current) return false;
      const editsAtSend = editsRef.current;
      setStatus({ kind: "saving", action });
      try {
        const saved = await saveLayout(name, versionRef.current, current, action);
        setVersion(saved.data.version);
        versionRef.current = saved.data.version;
        client.setQueryData(apiKey(PATHS.layout(name)), saved);
        setAdopted(`${name}|${saved.data.version}`);
        if (editsRef.current === editsAtSend) {
          setDirty(false);
          setStatus({ kind: "clean", savedAt: saved.data.updated_at });
        } else {
          setStatus({ kind: "dirty" });
        }
        void invalidatePath(client, PATHS.layouts);
        return true;
      } catch (error) {
        if (error instanceof ApiError && error.code === "workspace_conflict") {
          const currentVersion = Number(error.context.current_version);
          setStatus({ kind: "conflict", current: currentVersion, message: error.message });
        } else {
          setStatus({ kind: "error", message: error instanceof Error ? error.message : String(error) });
        }
        return false;
      }
    },
    [client, name],
  );

  const schedule = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      void save("autosave");
    }, AUTOSAVE_MS);
  }, [save]);

  const edit = useCallback(
    (fn: (doc: LayoutDoc) => LayoutDoc) => {
      const current = docRef.current;
      if (!current) return;
      const next = fn(current);
      if (next === current) return;
      editsRef.current += 1;
      docRef.current = next;
      setDoc(next);
      setDirty(true);
      setStatus((s) => (s.kind === "conflict" ? s : { kind: "dirty" }));
      if (canEdit) schedule();
    },
    [canEdit, schedule],
  );

  const replace = useCallback((next: LayoutDoc) => edit(() => next), [edit]);

  const resolve = useCallback(
    async (choice: "theirs" | "mine") => {
      if (choice === "theirs") {
        setDirty(false);
        setAdopted(null);
        editsRef.current += 1;
        await invalidatePath(client, PATHS.layout(name));
        return;
      }
      try {
        const current = await apiFetch<Envelope<LayoutView>>(PATHS.layout(name));
        setVersion(current.data.version);
        versionRef.current = current.data.version;
      } catch (error) {
        // Deleted elsewhere: write mine as a re-creation on top of the tombstone.
        if (error instanceof ApiError && error.status === 404) {
          const v = typeof error.context.version === "number" ? error.context.version : Number.NaN;
          if (!Number.isInteger(v)) {
            setStatus({ kind: "error", message: error.message });
            return;
          }
          setVersion(v);
          versionRef.current = v;
        } else {
          setStatus({ kind: "error", message: error instanceof Error ? error.message : String(error) });
          return;
        }
      }
      await save("save");
    },
    [client, name, save],
  );

  // A different name starts over.
  const [forName, setForName] = useState(name);
  if (forName !== name) {
    setForName(name);
    setDoc(null);
    setDirty(false);
    setVersion(0);
    setAdopted(null);
    setStatus({ kind: "loading" });
  }

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
  }, []);

  // Leaving with unsaved edits asks first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return { name, doc, version, canEdit, status, dirty, edit, replace, save, resolve, loadError };
}
