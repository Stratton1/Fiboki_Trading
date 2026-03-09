"use client";

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
}

export default function GroupedInstrumentSelect({
  instruments,
  value,
  onChange,
  className = "",
}: GroupedInstrumentSelectProps) {
  const grouped = new Map<string, InstrumentOption[]>();
  for (const inst of instruments) {
    const list = grouped.get(inst.asset_class) ?? [];
    list.push(inst);
    grouped.set(inst.asset_class, list);
  }

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={className}
    >
      <option value="">Select instrument</option>
      {ASSET_CLASS_ORDER.filter((ac) => grouped.has(ac)).map((ac) => (
        <optgroup key={ac} label={ASSET_CLASS_LABELS[ac] ?? ac}>
          {grouped.get(ac)!.map((inst) => (
            <option key={inst.symbol} value={inst.symbol}>
              {inst.symbol}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
