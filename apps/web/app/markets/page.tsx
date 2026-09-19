"use client";

import { ListPage, type Column } from "@/components/ListPage";
import { FigureValue } from "@/components/FigureValue";
import type { InstrumentRow } from "@/lib/types";

/** MARKETS · Explorer: the instrument universe and its declared frictions. */
export default function MarketsExplorerPage() {
  const columns: Column<InstrumentRow>[] = [
    { key: "symbol", header: "Symbol", cell: (row) => <strong>{row.symbol}</strong> },
    { key: "class", header: "Asset class", cell: (row) => row.asset_class },
    { key: "pair", header: "Base / Quote", cell: (row) => `${row.base} / ${row.quote}` },
    { key: "hours", header: "Hours", cell: (row) => row.trading_hours },
    {
      key: "spread",
      header: "Typical spread",
      cell: (row) => <FigureValue figure={row.typical_spread_pips} showChip={false} />,
    },
    {
      key: "leverage",
      header: "Retail leverage",
      cell: (row) => <FigureValue figure={row.retail_leverage} showChip={false} />,
    },
    {
      key: "financing",
      header: "Financing",
      cell: (row) => <FigureValue figure={row.annual_financing_bps} showChip={false} />,
    },
    { key: "min", header: "Min size", cell: (row) => <FigureValue figure={row.min_size} showChip={false} /> },
  ];
  return (
    <ListPage<InstrumentRow>
      title="Explorer"
      intro="The registered instrument universe, with the spread, leverage and financing assumptions every simulation uses."
      path="/api/markets/instruments"
      label="instruments"
      columns={columns}
      rowKey={(row) => row.symbol}
    />
  );
}
