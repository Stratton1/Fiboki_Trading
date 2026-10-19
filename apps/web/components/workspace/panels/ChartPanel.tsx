"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { LazyPriceChart, preloadPriceChart } from "@/components/LazyChart";
import { buildChartModel } from "@/lib/chart-model";
import { priceDecimals } from "@/lib/format";
import { useApi } from "@/lib/query";
import { route } from "@/lib/routes";
import type { BarsView, Envelope, InstrumentRow, Page } from "@/lib/types";
import { PanelStamp } from "../PanelStamp";
import { settingString, type PanelProps } from "../types";

const TIMEFRAMES = ["M15", "H1", "H4", "D1"] as const;
const BAR_LIMIT = 300;
const NO_HIDDEN: ReadonlySet<string> = new Set();
const NO_DECIMALS: ReadonlyMap<string, number> = new Map();
const LAYERS = { regimes: false, signals: false, fills: false, levels: false };

/**
 * GP in a tile: closed-candle bars for the panel's symbol (the link group's
 * when retargeted), drawn by the same lazily loaded engine as the chart
 * workstation. Overlays, trades and drawings stay on the full workstation,
 * one click away; this panel draws the bars the API returned and nothing it
 * computed itself.
 */
export default function ChartPanel({ panel, symbol, onSettings, readOnly }: PanelProps) {
  const tf = settingString(panel, "timeframe", "H1");
  const sym = symbol ?? "EURUSD";
  const path = `/api/markets/bars/${encodeURIComponent(sym)}?timeframe=${tf}&limit=${BAR_LIMIT}`;
  const bars = useApi<Envelope<BarsView>>(path, { refreshMs: 60_000 });
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments");
  const [cursor, setCursor] = useState<number | null>(null);
  useEffect(() => {
    void preloadPriceChart();
  }, []);
  const pip =
    instruments.status === "success"
      ? (instruments.data.items.find((i) => i.symbol === sym)?.pip_size.value ?? null)
      : null;
  return (
    <div className="ws-chart" data-testid="ws-chart" data-symbol={sym}>
      <div className="ws-panel__bar">
        <span className="mono ws-chart__sym">{sym}</span>
        <label className="ws-inline">
          <span className="sr-only">Timeframe</span>
          <select
            value={tf}
            disabled={readOnly}
            onChange={(e) => onSettings({ timeframe: e.target.value })}
            data-testid="ws-chart-tf"
          >
            {TIMEFRAMES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <Link href={route.instrument(sym, { tf })} className="ws-link" prefetch={false}>
          Open in chart workstation
        </Link>
      </div>
      <AsyncBoundary
        state={bars}
        label={`${sym} ${tf} bars`}
        onRetry={bars.reload}
        isEmpty={(e) => e.data.bars.length === 0}
        emptyTitle="No bars"
        emptyBody="The bar store has no closed bars for this instrument and timeframe."
      >
        {(envelope) => (
          <ChartBody envelope={envelope} pip={pip} cursor={cursor} onCursor={setCursor} label={`${sym} ${tf}`} />
        )}
      </AsyncBoundary>
    </div>
  );
}

function ChartBody({
  envelope,
  pip,
  cursor,
  onCursor,
  label,
}: {
  envelope: Envelope<BarsView>;
  pip: number | null;
  cursor: number | null;
  onCursor: (i: number | null) => void;
  label: string;
}) {
  const model = useMemo(
    () => buildChartModel(envelope.data.bars, null, envelope.data.volume_kind),
    [envelope.data.bars, envelope.data.volume_kind],
  );
  return (
    <>
      <PanelStamp source={envelope.source} extra={`dataset ${envelope.data.dataset_version_id.slice(0, 18)}`} />
      <div className="ws-chart__canvas">
        <LazyPriceChart
          model={model}
          hidden={NO_HIDDEN}
          layers={LAYERS}
          priceDecimals={priceDecimals(pip)}
          paneDecimals={NO_DECIMALS}
          label={`${label} closed-candle chart`}
          cursor={cursor}
          onCursor={(index, source) => onCursor(source === "keyboard" ? index : null)}
        />
      </div>
    </>
  );
}
