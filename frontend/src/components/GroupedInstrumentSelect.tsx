"use client";

import { useRef, useState, useEffect, useMemo } from "react";
import { ChevronDown, Search, X } from "lucide-react";

export interface InstrumentOption {
  symbol: string;
  name: string;
  asset_class: string;
  has_canonical_data: boolean;
}

const ASSET_CLASS_LABELS: Record<string, string> = {
  forex_major: "Forex Major",
  forex_cross: "Forex Cross",
  forex_g10_cross: "Forex G10 Cross",
  forex_scandinavian: "Forex Scandinavian",
  forex_em: "Forex EM",
  commodity_metal: "Metals",
  commodity_energy: "Energy",
  index: "Indices",
  crypto: "Crypto",
};

const ASSET_CLASS_ORDER = [
  "forex_major",
  "forex_cross",
  "forex_g10_cross",
  "forex_scandinavian",
  "forex_em",
  "commodity_metal",
  "commodity_energy",
  "index",
  "crypto",
];

interface GroupedInstrumentSelectProps {
  instruments: InstrumentOption[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
  showDataIndicator?: boolean;
}

export default function GroupedInstrumentSelect({
  instruments,
  value,
  onChange,
  className = "",
  showDataIndicator = false,
}: GroupedInstrumentSelectProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  // Close on outside click
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  // Focus search on open
  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    if (!q) return instruments;
    return instruments.filter(
      (inst) =>
        inst.symbol.toLowerCase().includes(q) ||
        inst.name.toLowerCase().includes(q) ||
        (ASSET_CLASS_LABELS[inst.asset_class] ?? inst.asset_class)
          .toLowerCase()
          .includes(q)
    );
  }, [instruments, search]);

  const grouped = useMemo(() => {
    const map = new Map<string, InstrumentOption[]>();
    for (const inst of filtered) {
      const list = map.get(inst.asset_class) ?? [];
      list.push(inst);
      map.set(inst.asset_class, list);
    }
    return map;
  }, [filtered]);

  const selectedInst = instruments.find((i) => i.symbol === value);

  function selectInstrument(symbol: string) {
    onChange(symbol);
    setOpen(false);
    setSearch("");
  }

  return (
    <div ref={containerRef} className="relative">
      {/* Trigger button */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={`flex items-center justify-between gap-2 min-w-[180px] ${className}`}
      >
        <span className={value ? "text-foreground" : "text-foreground-muted"}>
          {selectedInst ? selectedInst.symbol : "Select instrument"}
        </span>
        <ChevronDown
          size={14}
          className={`text-foreground-muted transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {/* Dropdown */}
      {open && (
        <div className="absolute z-50 top-full mt-1 left-0 w-72 bg-background-card border border-border rounded-lg shadow-lg overflow-hidden">
          {/* Search input */}
          <div className="p-2 border-b border-border">
            <div className="flex items-center gap-2 bg-background-muted rounded-md px-2.5 py-1.5">
              <Search size={14} className="text-foreground-muted shrink-0" />
              <input
                ref={searchRef}
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search instruments..."
                className="bg-transparent text-sm flex-1 outline-none placeholder:text-foreground-muted"
              />
              {search && (
                <button
                  onClick={() => setSearch("")}
                  className="text-foreground-muted hover:text-foreground"
                >
                  <X size={12} />
                </button>
              )}
            </div>
          </div>

          {/* Results */}
          <div className="max-h-64 overflow-y-auto py-1">
            {filtered.length === 0 ? (
              <p className="text-sm text-foreground-muted text-center py-4">
                No instruments match &ldquo;{search}&rdquo;
              </p>
            ) : (
              ASSET_CLASS_ORDER.filter((ac) => grouped.has(ac)).map((ac) => (
                <div key={ac}>
                  <p className="text-[10px] uppercase font-semibold text-foreground-muted px-3 py-1.5 bg-background-muted">
                    {ASSET_CLASS_LABELS[ac] ?? ac}
                    <span className="ml-1 text-foreground-muted/60">({grouped.get(ac)!.length})</span>
                  </p>
                  {grouped.get(ac)!.map((inst) => (
                    <button
                      key={inst.symbol}
                      type="button"
                      onClick={() => selectInstrument(inst.symbol)}
                      className={`w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-background-muted transition ${
                        inst.symbol === value
                          ? "bg-primary/5 text-primary font-medium"
                          : ""
                      }`}
                    >
                      <span>{inst.symbol}</span>
                      <span className="flex items-center gap-1.5">
                        {showDataIndicator && (
                          <span
                            className={`w-1.5 h-1.5 rounded-full ${
                              inst.has_canonical_data
                                ? "bg-green-500"
                                : "bg-gray-300"
                            }`}
                            title={
                              inst.has_canonical_data
                                ? "Data available"
                                : "No data"
                            }
                          />
                        )}
                        <span className="text-xs text-foreground-muted">
                          {inst.name}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>

          {/* Footer */}
          <div className="border-t border-border px-3 py-1.5">
            <p className="text-[10px] text-foreground-muted">
              {filtered.length} of {instruments.length} instruments
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
