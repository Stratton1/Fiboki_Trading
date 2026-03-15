"use client";

import { use, useState } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useTrade } from "@/lib/hooks/use-trades";
import { useJournal } from "@/lib/hooks/use-journal";
import { formatPnl } from "@/lib/format-currency";
import { useBacktest } from "@/lib/hooks/use-backtests";
import { useMarketData } from "@/lib/hooks/use-market-data";
import { Play, X, Save, Trash2, Tag } from "lucide-react";
import { isTpHitNegativePnl } from "@/lib/trade-explain";

const TradeMarkerChart = dynamic(
  () => import("@/components/charts/core/TradeMarkerChart"),
  { ssr: false }
);

const TradeReplay = dynamic(
  () => import("@/components/charts/core/TradeReplay"),
  { ssr: false }
);

const COMMON_TAGS = [
  "good entry",
  "bad entry",
  "held too long",
  "exited too early",
  "trend reversal",
  "news event",
  "false signal",
  "textbook setup",
  "review later",
];

export default function TradeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const numId = parseInt(id, 10);
  const { data: trade, isLoading } = useTrade(isNaN(numId) ? null : numId);
  const [replayMode, setReplayMode] = useState(false);
  const { entry: journal, save: saveJournal, remove: removeJournal } = useJournal(
    isNaN(numId) ? null : numId
  );

  const [journalNote, setJournalNote] = useState<string | null>(null);
  const [journalTags, setJournalTags] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);

  // Sync local state from server on first load
  const note = journalNote ?? journal?.note ?? "";
  const tags = journalTags ?? journal?.tags ?? [];

  // Look up the backtest to get timeframe
  const { data: bt } = useBacktest(trade?.backtest_run_id ?? null);

  // Load market data for this trade's instrument/timeframe
  const { data: marketData } = useMarketData(
    trade?.instrument ?? null,
    bt?.timeframe ?? null
  );

  if (isLoading) {
    return <p className="text-foreground-muted">Loading trade...</p>;
  }

  if (!trade) {
    return <p className="text-foreground-muted">Trade not found.</p>;
  }

  function toggleTag(tag: string) {
    const current = journalTags ?? journal?.tags ?? [];
    if (current.includes(tag)) {
      setJournalTags(current.filter((t) => t !== tag));
    } else {
      setJournalTags([...current, tag]);
    }
  }

  async function handleSave() {
    setSaving(true);
    try {
      await saveJournal({ note, tags });
      setJournalNote(null);
      setJournalTags(null);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    await removeJournal();
    setJournalNote(null);
    setJournalTags(null);
  }

  const isDirty =
    (journalNote !== null && journalNote !== (journal?.note ?? "")) ||
    (journalTags !== null && JSON.stringify(journalTags) !== JSON.stringify(journal?.tags ?? []));

  const fields: { label: string; value: string; color?: string }[] = [
    { label: "Strategy", value: trade.strategy_id },
    { label: "Instrument", value: trade.instrument },
    {
      label: "Direction",
      value: trade.direction,
      color: trade.direction === "LONG" ? "text-primary" : "text-danger",
    },
    { label: "Entry Time", value: trade.entry_time ?? "-" },
    { label: "Entry Price", value: trade.entry_price.toFixed(5) },
    { label: "Exit Time", value: trade.exit_time ?? "-" },
    { label: "Exit Price", value: trade.exit_price.toFixed(5) },
    {
      label: "PnL",
      value: formatPnl(trade.pnl),
      color: trade.pnl >= 0 ? "text-primary" : "text-danger",
    },
    { label: "Bars in Trade", value: String(trade.bars_in_trade) },
    {
      label: "Exit Reason",
      value: isTpHitNegativePnl(trade)
        ? `${trade.exit_reason} (negative PnL due to spread/slippage)`
        : trade.exit_reason,
    },
    { label: "Backtest Run", value: String(trade.backtest_run_id) },
  ];

  return (
    <div>
      <div className="flex items-center gap-2 mb-6">
        <Link href="/trades" className="text-foreground-muted hover:text-foreground text-sm">
          Trades
        </Link>
        <span className="text-foreground-muted text-sm">/</span>
        <h2 className="text-xl font-semibold">Trade #{trade.id}</h2>
        {marketData && (
          <button
            onClick={() => setReplayMode(!replayMode)}
            className={`ml-auto flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md transition ${
              replayMode
                ? "bg-primary text-white"
                : "bg-background-card text-foreground-muted hover:text-foreground border border-gray-200"
            }`}
          >
            {replayMode ? <X size={14} /> : <Play size={14} />}
            {replayMode ? "Exit Replay" : "Replay Trade"}
          </button>
        )}
      </div>

      <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          {fields.map(({ label, value, color }) => (
            <div key={label}>
              <p className="text-xs text-foreground-muted mb-1">{label}</p>
              <p className={`text-sm font-medium ${color ?? ""}`}>{value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Trade Journal */}
      <div className="bg-background-card rounded-lg border border-gray-200 p-5 mb-6">
        <div className="flex items-center justify-between mb-3">
          <p className="section-label flex items-center gap-1.5">
            <Tag size={14} />
            Trade Journal
          </p>
          <div className="flex items-center gap-2">
            {journal && (
              <button
                onClick={handleDelete}
                className="text-xs text-foreground-muted hover:text-danger transition flex items-center gap-1"
              >
                <Trash2 size={12} />
                Delete
              </button>
            )}
            <button
              onClick={handleSave}
              disabled={saving || (!isDirty && !journal && !note && tags.length === 0)}
              className="btn btn-primary text-sm disabled:opacity-40 flex items-center gap-1"
            >
              <Save size={12} />
              {saving ? "Saving..." : journal ? "Update" : "Save"}
            </button>
          </div>
        </div>

        {/* Notes */}
        <textarea
          value={note}
          onChange={(e) => setJournalNote(e.target.value)}
          placeholder="Add notes about this trade... What went well? What would you do differently?"
          className="w-full bg-background border border-border rounded-md p-3 text-sm outline-none focus:border-primary resize-y min-h-[80px] mb-3"
          rows={3}
        />

        {/* Tags */}
        <div>
          <p className="text-xs text-foreground-muted mb-2">Tags</p>
          <div className="flex flex-wrap gap-1.5">
            {COMMON_TAGS.map((tag) => (
              <button
                key={tag}
                type="button"
                onClick={() => toggleTag(tag)}
                className={`px-2.5 py-1 text-xs rounded-full border transition ${
                  tags.includes(tag)
                    ? "bg-primary/10 border-primary/30 text-primary font-medium"
                    : "bg-background border-border text-foreground-muted hover:text-foreground hover:border-gray-400"
                }`}
              >
                {tag}
              </button>
            ))}
          </div>
        </div>

        {/* Saved indicator */}
        {journal && !isDirty && (
          <p className="text-[10px] text-foreground-muted mt-3">
            Last saved {journal.updated_at ? new Date(journal.updated_at).toLocaleString() : ""}
          </p>
        )}
      </div>

      {/* Chart area */}
      {marketData && (
        <div>
          <h3 className="text-sm font-medium text-foreground-muted mb-2">
            {replayMode ? "Trade Replay" : "Trade Context"} — {trade.instrument}
          </h3>
          <div className="h-[450px]">
            {replayMode ? (
              <TradeReplay data={marketData} trade={trade} />
            ) : (
              <TradeMarkerChart
                data={marketData}
                trades={[trade]}
                focusTradeId={trade.id}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
