"use client";

import { useState, useEffect, useRef } from "react";
import { init, dispose } from "klinecharts";
import type { Chart, KLineData } from "klinecharts";
import { mapCandlesToKLine } from "@/lib/chart-mappers/candle-mapper";
import { formatPnl } from "@/lib/format-currency";
import type { MarketDataResponse } from "@/types/contracts/chart";
import type { Trade } from "@/types/contracts/trades";
import {
  Play,
  Pause,
  SkipForward,
  SkipBack,
  RotateCcw,
  FastForward,
} from "lucide-react";

interface TradeReplayProps {
  data: MarketDataResponse;
  trade: Trade;
}

const SPEEDS = [
  { label: "0.5x", ms: 1000 },
  { label: "1x", ms: 500 },
  { label: "2x", ms: 250 },
  { label: "4x", ms: 125 },
];

export default function TradeReplay({ data, trade }: TradeReplayProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<Chart | null>(null);
  const allBarsRef = useRef<KLineData[]>([]);
  const visibleRef = useRef<KLineData[]>([]);

  const entryTs = trade.entry_time ? new Date(trade.entry_time).getTime() : 0;
  const exitTs = trade.exit_time ? new Date(trade.exit_time).getTime() : 0;

  const [barIndex, setBarIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speedIdx, setSpeedIdx] = useState(1);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [ready, setReady] = useState(false);

  // Map candles once
  useEffect(() => {
    if (data?.candles) {
      allBarsRef.current = mapCandlesToKLine(data.candles);
      setReady(true);
    }
  }, [data]);

  // Compute start/end indices
  const entryBarIdx = allBarsRef.current.findIndex((b) => b.timestamp >= entryTs);
  const exitBarIdx = allBarsRef.current.findIndex((b) => b.timestamp >= exitTs);
  const startIdx = Math.max(0, (entryBarIdx > 0 ? entryBarIdx : 0) - 50);
  const endIdx = Math.min(
    allBarsRef.current.length,
    (exitBarIdx > 0 ? exitBarIdx : allBarsRef.current.length - 1) + 20
  );
  const totalBars = endIdx - startIdx;

  // Initialize chart
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = init(containerRef.current, {
      styles: {
        grid: { show: true },
        candle: { type: "candle_solid" as const },
      },
    });
    chartRef.current = chart ?? null;
    return () => {
      if (containerRef.current) dispose(containerRef.current);
      chartRef.current = null;
    };
  }, []);

  // Load data into chart via setDataLoader when ready
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !ready || allBarsRef.current.length === 0) return;

    // Set initial visible bars
    const initial = allBarsRef.current.slice(startIdx, startIdx + barIndex + 1);
    visibleRef.current = initial;

    chart.setSymbol({ ticker: data.instrument || "REPLAY" });
    chart.setPeriod({ span: 1, type: "day" });
    chart.setDataLoader({
      getBars: ({ type, callback }) => {
        if (type === "init" || type === "backward") {
          callback(visibleRef.current, false);
        } else {
          callback([], false);
        }
      },
    });
  }, [ready]);

  // Update visible data when barIndex changes by reloading
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !ready || allBarsRef.current.length === 0) return;

    const newVisible = allBarsRef.current.slice(startIdx, startIdx + barIndex + 1);
    visibleRef.current = newVisible;

    // Force chart to reload data
    chart.setDataLoader({
      getBars: ({ type, callback }) => {
        if (type === "init" || type === "backward") {
          callback(visibleRef.current, false);
        } else {
          callback([], false);
        }
      },
    });

    // Add entry/exit markers
    const currentTs = newVisible[newVisible.length - 1]?.timestamp ?? 0;

    try {
      chart.removeOverlay({ groupId: "replay_markers" });
    } catch { /* ok */ }

    if (entryTs && currentTs >= entryTs) {
      const entryBar = allBarsRef.current.find((b) => b.timestamp >= entryTs);
      if (entryBar) {
        chart.createOverlay({
          name: "simpleTag",
          groupId: "replay_markers",
          points: [{ timestamp: entryBar.timestamp, value: trade.entry_price }],
          styles: {
            text: { color: "#fff", backgroundColor: "#2196F3", borderRadius: 2 },
          },
        });
      }
    }

    if (exitTs && currentTs >= exitTs) {
      const exitBar = allBarsRef.current.find((b) => b.timestamp >= exitTs);
      if (exitBar) {
        chart.createOverlay({
          name: "simpleTag",
          groupId: "replay_markers",
          points: [{ timestamp: exitBar.timestamp, value: trade.exit_price }],
          styles: {
            text: {
              color: "#fff",
              backgroundColor: trade.pnl >= 0 ? "#4CAF50" : "#F44336",
              borderRadius: 2,
            },
          },
        });
      }
    }
  }, [barIndex, ready, startIdx, entryTs, exitTs, trade]);

  // Playback timer
  useEffect(() => {
    if (playing) {
      timerRef.current = setInterval(() => {
        setBarIndex((prev) => {
          if (prev >= totalBars - 1) {
            setPlaying(false);
            return prev;
          }
          return prev + 1;
        });
      }, SPEEDS[speedIdx].ms);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [playing, speedIdx, totalBars]);

  const handleReset = () => { setPlaying(false); setBarIndex(0); };
  const handleStepBack = () => { setPlaying(false); setBarIndex((p) => Math.max(0, p - 1)); };
  const handleStepForward = () => { setPlaying(false); setBarIndex((p) => Math.min(totalBars - 1, p + 1)); };
  const progress = totalBars > 0 ? ((barIndex / Math.max(1, totalBars - 1)) * 100).toFixed(0) : "0";

  return (
    <div className="flex flex-col h-full">
      <div ref={containerRef} className="flex-1 min-h-0" />

      {/* Playback controls */}
      <div className="flex items-center gap-3 px-3 py-2 border-t border-border bg-background">
        <button onClick={handleReset} title="Reset" className="p-1 hover:bg-background-muted rounded">
          <RotateCcw size={14} />
        </button>
        <button onClick={handleStepBack} title="Step Back" className="p-1 hover:bg-background-muted rounded">
          <SkipBack size={14} />
        </button>
        <button
          onClick={() => setPlaying(!playing)}
          title={playing ? "Pause" : "Play"}
          className={`p-1.5 rounded ${playing ? "bg-primary text-white" : "hover:bg-background-muted"}`}
        >
          {playing ? <Pause size={14} /> : <Play size={14} />}
        </button>
        <button onClick={handleStepForward} title="Step Forward" className="p-1 hover:bg-background-muted rounded">
          <SkipForward size={14} />
        </button>

        <div className="flex items-center gap-1 ml-2">
          <FastForward size={12} className="text-foreground-muted" />
          {SPEEDS.map((s, i) => (
            <button
              key={s.label}
              onClick={() => setSpeedIdx(i)}
              className={`px-1.5 py-0.5 text-[10px] rounded ${
                speedIdx === i ? "bg-primary text-white" : "text-foreground-muted hover:bg-background-muted"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2 ml-auto text-xs text-foreground-muted">
          <input
            type="range"
            min={0}
            max={Math.max(0, totalBars - 1)}
            value={barIndex}
            onChange={(e) => { setPlaying(false); setBarIndex(parseInt(e.target.value, 10)); }}
            className="w-32"
          />
          <span>Bar {barIndex + 1}/{totalBars} ({progress}%)</span>
        </div>

        <div className="text-xs ml-3">
          <span className={trade.direction === "LONG" ? "text-primary" : "text-danger"}>
            {trade.direction}
          </span>{" "}
          <span className={trade.pnl >= 0 ? "text-primary" : "text-danger"}>
            {formatPnl(trade.pnl)}
          </span>
        </div>
      </div>
    </div>
  );
}
