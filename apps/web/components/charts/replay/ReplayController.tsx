"use client";

import { ChevronLeft, ChevronRight, Pause, Play, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { ReplayView } from "@/lib/chart-contract";
import { useApi } from "@/lib/query";
import type { Envelope } from "@/lib/types";
import { formatBarTime } from "../time";

/**
 * Bar-by-bar replay, driven by GET /api/markets/replay/{symbol}. Loaded only
 * when the chart is in replay mode (`?mode=replay`), so none of it is in the
 * chart route's first-load JavaScript.
 *
 * The browser holds a CURSOR (a UTC instant) and nothing else. Every bar,
 * indicator value, regime, marker and trade shown is the API's answer for that
 * cursor, and the API serves nothing timed after it (tests/api/
 * test_chart_replay.py perturbs the future to prove it). Stepping asks for
 * the API's own `next_cursor` / `prev_cursor`; play steps at the chosen speed
 * and never queues a step while one is in flight. The last answer stays on
 * screen while the next is read, marked as reading.
 */

export const REPLAY_SPEEDS = [1, 2, 4, 8] as const;
export type ReplaySpeed = (typeof REPLAY_SPEEDS)[number];
/** Bars replayed per step window: the API's own default. */
const REPLAY_BARS = 300;
/** With no cursor in the URL, start this many closed bars before the latest. */
const START_BACK = 100;

export interface ReplayControllerProps {
  symbol: string;
  tf: string;
  run: string | null;
  /** The cursor in the URL (`?cursor=`), or null to start near the latest close. */
  cursor: string | null;
  onCursor: (cursor: string) => void;
  onExit: () => void;
  /** Formats an instant in the operator's display time zone. */
  formatTime?: (seconds: number) => string;
  children: (view: Envelope<ReplayView>, state: { reading: boolean }) => ReactNode;
}

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return target.isContentEditable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** "2026-09-21T14:00" (the datetime-local value, in UTC) from an ISO instant. */
function toLocalInput(iso: string): string {
  return iso.slice(0, 16);
}

export default function ReplayController({
  symbol,
  tf,
  run,
  cursor,
  onCursor,
  onExit,
  formatTime = formatBarTime,
  children,
}: ReplayControllerProps) {
  // The first read without a cursor asks for "now"; the controller then
  // rewinds START_BACK bars so play has something to play.
  const [boot] = useState(() => new Date().toISOString().replace(/\.\d{3}Z$/, "Z"));
  const at = cursor ?? boot;
  const enc = encodeURIComponent(symbol);
  const path = `/api/markets/replay/${enc}?timeframe=${tf}&cursor=${encodeURIComponent(at)}&limit=${REPLAY_BARS}${
    run ? `&run=${encodeURIComponent(run)}` : ""
  }`;
  const read = useApi<Envelope<ReplayView>>(path);
  const [shown, setShown] = useState<Envelope<ReplayView> | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<ReplaySpeed>(1);
  const [jump, setJump] = useState("");
  const rewound = useRef(cursor !== null);

  const data = read.status === "success" ? read.data : null;
  // Keep the last answer on screen while the next step is read (adjusting
  // state during render, React's pattern for state derived from a prop).
  if (data !== null && data !== shown) setShown(data);
  useEffect(() => {
    if (!data) return;
    if (!rewound.current) {
      rewound.current = true;
      const bars = data.data.bars;
      const start = bars[Math.max(0, bars.length - START_BACK)];
      if (start) onCursor(start.t);
    }
  }, [data, onCursor]);

  const view = shown?.data ?? null;
  const reading = read.status === "loading";
  const next = view?.next_cursor ?? null;
  const prev = view?.prev_cursor ?? null;
  const atEnd = view !== null && next === null;
  const step = useCallback(
    (dir: 1 | -1) => {
      const target = dir === 1 ? next : prev;
      if (target) onCursor(target);
    },
    [next, prev, onCursor],
  );

  // Play: one step per tick, only once the previous step has answered.
  const playingNow = playing && !atEnd;
  useEffect(() => {
    if (!playingNow || reading || !next || view?.cursor !== at) return;
    const timer = window.setTimeout(() => onCursor(next), 1_000 / speed);
    return () => window.clearTimeout(timer);
  }, [playingNow, reading, next, speed, onCursor, view?.cursor, at]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (typing(event.target)) return;
      if (event.key === "." || event.key === ">") {
        event.preventDefault();
        setPlaying(false);
        step(1);
      } else if (event.key === "," || event.key === "<") {
        event.preventDefault();
        setPlaying(false);
        step(-1);
      } else if (event.key === "p" || event.key === "P") {
        event.preventDefault();
        setPlaying((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  const cursorSeconds = view ? Math.floor(Date.parse(view.cursor) / 1_000) : null;
  const error = read.status === "error" ? read.error : null;

  return (
    <div className="cw-replay" data-testid="replay" data-cursor={view?.cursor ?? ""} data-playing={playingNow}>
      <div className="cw-replay__bar" role="toolbar" aria-label="Replay controls">
        <span className="cw-replay__tag" data-testid="replay-tag">
          REPLAY
        </span>
        <button
          type="button"
          className="cw-replay__btn"
          data-testid="replay-back"
          aria-label="Step back one bar (,)"
          title="Step back one bar · ,"
          aria-disabled={!prev}
          onClick={() => {
            setPlaying(false);
            step(-1);
          }}
        >
          <ChevronLeft size={14} aria-hidden="true" />
        </button>
        <button
          type="button"
          className="cw-replay__btn"
          data-testid="replay-play"
          aria-pressed={playingNow}
          aria-label={playingNow ? "Pause (P)" : "Play (P)"}
          title={playingNow ? "Pause · P" : "Play · P"}
          aria-disabled={atEnd}
          onClick={() => (atEnd ? undefined : setPlaying((v) => !v))}
        >
          {playingNow ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
        </button>
        <button
          type="button"
          className="cw-replay__btn"
          data-testid="replay-step"
          aria-label="Step forward one bar (.)"
          title="Step forward one bar · ."
          aria-disabled={!next}
          onClick={() => {
            setPlaying(false);
            step(1);
          }}
        >
          <ChevronRight size={14} aria-hidden="true" />
        </button>
        <label className="cw-replay__field">
          <span className="sr-only">Replay speed</span>
          <select
            data-testid="replay-speed"
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value) as ReplaySpeed)}
          >
            {REPLAY_SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s} bar{s === 1 ? "" : "s"}/s
              </option>
            ))}
          </select>
        </label>
        <form
          className="cw-replay__field"
          onSubmit={(e) => {
            e.preventDefault();
            if (!jump) return;
            setPlaying(false);
            onCursor(`${jump}:00Z`);
            setJump("");
          }}
        >
          <label htmlFor="replay-jump" className="sr-only">
            Jump to a UTC date and time
          </label>
          <input
            id="replay-jump"
            type="datetime-local"
            data-testid="replay-jump"
            value={jump || (view ? toLocalInput(view.cursor) : "")}
            onChange={(e) => setJump(e.target.value)}
          />
          <button type="submit" className="cw-replay__btn cw-replay__btn--text" data-testid="replay-go">
            Go (UTC)
          </button>
        </form>
        <span className="cw-replay__status num" role="status" data-testid="replay-status">
          {view && cursorSeconds !== null
            ? `as of ${formatTime(cursorSeconds)} · ${view.bars_closed.toLocaleString("en-GB")} of ${view.bars_total.toLocaleString(
                "en-GB",
              )} bars closed${atEnd ? " · end of data" : ""}${reading ? " · reading…" : ""}`
            : reading
              ? "reading the replay…"
              : ""}
        </span>
        <button
          type="button"
          className="cw-replay__btn cw-replay__btn--text"
          data-testid="replay-exit"
          onClick={onExit}
        >
          <X size={13} aria-hidden="true" /> Exit replay
        </button>
      </div>
      {error ? (
        <div className="cw__note cw__note--error" role="alert" data-testid="replay-error">
          <strong>Replay step not loaded.</strong> {error.message}{" "}
          <span className="state__code">code: {error.code}</span>{" "}
          <button type="button" className="cw-replay__btn cw-replay__btn--text" onClick={read.reload}>
            Retry
          </button>
        </div>
      ) : null}
      {view && view.trades_detail && run ? (
        <p className="cw__note muted" data-testid="replay-trades-detail">
          {view.trades_detail}
        </p>
      ) : null}
      {shown ? (
        view && view.bars.length === 0 ? (
          <div className="state state--empty cw__state" role="status" data-testid="replay-empty">
            <div className="state__title">No bar had closed by this cursor</div>
            <div className="state__body">
              The dataset starts at {view.first_bar ?? "an unreported time"}. Step forward or jump to a later
              date.
            </div>
          </div>
        ) : (
          children(shown, { reading })
        )
      ) : read.status === "loading" ? (
        <div className="price-chart price-chart--pending" role="status" data-testid="replay-loading">
          Reading the replay for {symbol} {tf}…
        </div>
      ) : null}
    </div>
  );
}
