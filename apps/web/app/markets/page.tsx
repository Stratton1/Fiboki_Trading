"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { GridPage } from "@/components/GridPage";
import type { GridColumn } from "@/components/grid";
import { FigureValue } from "@/components/FigureValue";
import type { InstrumentRow } from "@/lib/types";

/**
 * MARKETS · Explorer: the instrument universe and its declared frictions.
 * Wave 3: a DataGrid (sort, filter, columns, keyboard, CSV with provenance);
 * `?row=EURUSD` selects an instrument (the palette's "open by id").
 * The symbol links to its chart workstation (/markets/EURUSD); Enter on a
 * row opens it too.
 *
 * Spot crypto (venue "coinbase", asset class "crypto") is listed beside the
 * OANDA CFDs: 24/7 hours, 1x, no financing, LONG ONLY (the risk gateway
 * refuses a short entry; this page only displays the server's flag), and a
 * spread in bps of mid. Every value here is the server's; nothing is derived.
 */
function chartHref(symbol: string) {
  return `/markets/${encodeURIComponent(symbol)}`;
}

export default function MarketsExplorerPage() {
  const router = useRouter();
  const columns: GridColumn<InstrumentRow>[] = [
    {
      id: "symbol",
      header: "Symbol",
      value: (row) => row.symbol,
      cell: (row) => (
        <Link
          href={chartHref(row.symbol)}
          data-testid="instrument-chart-link"
          aria-label={`Open the ${row.symbol} chart`}
        >
          <strong>{row.symbol}</strong>
        </Link>
      ),
      width: 110,
      pin: true,
    },
    { id: "class", header: "Asset class", value: (row) => row.asset_class, width: 120 },
    { id: "pair", header: "Base / Quote", value: (row) => `${row.base} / ${row.quote}`, width: 120 },
    { id: "venue", header: "Venue", value: (row) => row.venue, width: 104 },
    {
      id: "direction",
      header: "Direction",
      value: (row) => (row.shortable ? "long / short" : "long only"),
      width: 120,
    },
    { id: "hours", header: "Hours", value: (row) => row.trading_hours, width: 160 },
    { id: "spread", header: "Typical spread", figure: (row) => row.typical_spread_pips, width: 140 },
    {
      id: "spread_bps",
      header: "Spread (bps of mid)",
      kind: "number",
      value: (row) => row.typical_spread_bps?.value ?? null,
      cell: (row) =>
        row.typical_spread_bps ? <FigureValue figure={row.typical_spread_bps} /> : "n/a",
      width: 160,
    },
    { id: "leverage", header: "Retail leverage", figure: (row) => row.retail_leverage, width: 140 },
    { id: "financing", header: "Financing", figure: (row) => row.annual_financing_bps, width: 128 },
    { id: "min", header: "Min size", figure: (row) => row.min_size, width: 112 },
    { id: "pip", header: "Pip size", figure: (row) => row.pip_size, hidden: true },
    { id: "contract", header: "Contract size", figure: (row) => row.contract_size, hidden: true },
    { id: "step", header: "Size step", figure: (row) => row.size_step, hidden: true },
  ];
  return (
    <GridPage<InstrumentRow>
      title="Explorer"
      intro="The registered instrument universe, with the spread, leverage and financing assumptions every simulation uses."
      path="/api/markets/instruments"
      label="instruments"
      gridId="instruments"
      columns={columns}
      rowKey={(row) => row.symbol}
      onRowActivate={(row) => router.push(chartHref(row.symbol))}
    />
  );
}
