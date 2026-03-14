"use client";

import { useRef, useState, useEffect } from "react";
import { useWatchlists } from "@/lib/hooks/use-watchlists";
import {
  Eye,
  ChevronDown,
  Plus,
  X,
  Pencil,
  Trash2,
  Check,
  List,
} from "lucide-react";

interface WatchlistPickerProps {
  /** Optional: called whenever active watchlist changes with the instrument set (or null) */
  onFilterChange?: (filterSet: Set<string> | null) => void;
  className?: string;
}

export default function WatchlistPicker({
  onFilterChange,
  className = "",
}: WatchlistPickerProps) {
  const {
    watchlists,
    active,
    setActive,
    create,
    update,
    remove,
  } = useWatchlists();

  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"select" | "create" | "rename">("select");
  const [inputValue, setInputValue] = useState("");
  const [renameId, setRenameId] = useState<number | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setMode("select");
        setConfirmDeleteId(null);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  useEffect(() => {
    if ((mode === "create" || mode === "rename") && inputRef.current) {
      inputRef.current.focus();
    }
  }, [mode]);

  // Notify parent when active changes
  useEffect(() => {
    if (onFilterChange) {
      onFilterChange(active ? new Set(active.instrument_ids) : null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.id, active?.instrument_ids?.length]);

  async function handleCreate() {
    const name = inputValue.trim();
    if (!name) return;
    const wl = await create(name, []);
    setActive(wl.id);
    setInputValue("");
    setMode("select");
  }

  async function handleRename() {
    const name = inputValue.trim();
    if (!name || !renameId) return;
    await update(renameId, { name });
    setInputValue("");
    setRenameId(null);
    setMode("select");
  }

  async function handleDelete(id: number) {
    await remove(id);
    setConfirmDeleteId(null);
  }

  function startRename(id: number, currentName: string) {
    setRenameId(id);
    setInputValue(currentName);
    setMode("rename");
  }

  function handleSelect(id: number | null) {
    setActive(id);
    setOpen(false);
  }

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-1.5 text-sm px-2.5 py-1.5 rounded-md border transition ${
          active
            ? "border-primary/30 bg-primary/5 text-primary"
            : "border-border bg-background-card text-foreground-muted hover:text-foreground"
        }`}
      >
        <Eye size={14} />
        <span className="max-w-[120px] truncate">
          {active ? active.name : "Watchlist"}
        </span>
        {active && (
          <span className="text-[10px] tabular-nums opacity-70">
            ({active.instrument_ids.length})
          </span>
        )}
        <ChevronDown
          size={12}
          className={`transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute z-50 top-full mt-1 left-0 w-64 bg-background-card border border-border rounded-lg shadow-lg overflow-hidden">
          {mode === "select" && (
            <>
              {/* "All instruments" option */}
              <button
                type="button"
                onClick={() => handleSelect(null)}
                className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-background-muted transition ${
                  !active ? "bg-primary/5 text-primary font-medium" : ""
                }`}
              >
                <List size={14} className="text-foreground-muted" />
                All instruments
              </button>

              {/* Divider */}
              {watchlists.length > 0 && <div className="border-t border-border" />}

              {/* Watchlist items */}
              <div className="max-h-48 overflow-y-auto">
                {watchlists.map((wl) => (
                  <div
                    key={wl.id}
                    className={`group flex items-center justify-between px-3 py-2 text-sm hover:bg-background-muted transition ${
                      active?.id === wl.id ? "bg-primary/5 text-primary font-medium" : ""
                    }`}
                  >
                    {confirmDeleteId === wl.id ? (
                      <div className="flex items-center gap-2 w-full">
                        <span className="text-xs text-danger">Delete?</span>
                        <div className="flex-1" />
                        <button
                          onClick={() => handleDelete(wl.id)}
                          className="text-xs text-danger hover:underline"
                        >
                          Yes
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(null)}
                          className="text-xs text-foreground-muted hover:underline"
                        >
                          No
                        </button>
                      </div>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => handleSelect(wl.id)}
                          className="flex items-center gap-2 flex-1 text-left truncate"
                        >
                          <Eye size={14} className="text-foreground-muted shrink-0" />
                          <span className="truncate">{wl.name}</span>
                          <span className="text-[10px] text-foreground-muted tabular-nums shrink-0">
                            ({wl.instrument_ids.length})
                          </span>
                        </button>
                        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              startRename(wl.id, wl.name);
                            }}
                            className="p-0.5 text-foreground-muted hover:text-foreground"
                            title="Rename"
                          >
                            <Pencil size={12} />
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmDeleteId(wl.id);
                            }}
                            className="p-0.5 text-foreground-muted hover:text-danger"
                            title="Delete"
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>

              {/* Create button */}
              <div className="border-t border-border">
                <button
                  type="button"
                  onClick={() => {
                    setInputValue("");
                    setMode("create");
                  }}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-foreground-muted hover:text-foreground hover:bg-background-muted transition"
                >
                  <Plus size={14} />
                  New watchlist
                </button>
              </div>
            </>
          )}

          {/* Create / Rename input */}
          {(mode === "create" || mode === "rename") && (
            <div className="p-3">
              <p className="text-xs font-medium text-foreground-muted mb-2">
                {mode === "create" ? "New watchlist" : "Rename watchlist"}
              </p>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  mode === "create" ? handleCreate() : handleRename();
                }}
                className="flex items-center gap-2"
              >
                <input
                  ref={inputRef}
                  type="text"
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  placeholder="Watchlist name..."
                  className="flex-1 text-sm px-2.5 py-1.5 border border-border rounded-md bg-background outline-none focus:border-primary"
                  maxLength={100}
                />
                <button
                  type="submit"
                  disabled={!inputValue.trim()}
                  className="p-1.5 text-primary hover:bg-primary/10 rounded disabled:opacity-40"
                >
                  <Check size={14} />
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setMode("select");
                    setRenameId(null);
                  }}
                  className="p-1.5 text-foreground-muted hover:text-foreground rounded"
                >
                  <X size={14} />
                </button>
              </form>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
