"use client";

import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import { PanelStamp } from "@/components/workspace/PanelStamp";
import { useToast } from "@/components/ui/Toast";
import { ApiError } from "@/lib/api";
import { formatAge, formatTimestamp, formatUtcTime, parseUtc } from "@/lib/format";
import { useClock } from "@/lib/clock";
import {
  ALERT_PATHS,
  createRule,
  deleteRule,
  markRead,
  updateRule,
  type AlertRulesView,
  type NotificationsView,
  type PriceRule,
  type RuleWrite,
} from "@/lib/monitors";
import { invalidatePath, useApi } from "@/lib/query";
import type { Envelope, InstrumentRow, Page } from "@/lib/types";
import { useBrowserNotify } from "./BrowserNotify";

const EVAL_WORD = { live: "EVALUATING", stale: "EVALUATOR STALE", never: "NOT EVALUATED" } as const;

/**
 * NTF: price and level alert notifications, newest first, with the API's
 * unread count and server-side read marker, and the rules that raise them.
 * Rules are evaluated by a separate process over recorded quotes (the API's
 * evaluator state says whether anything is evaluating); this screen edits
 * rules and reads notifications. A fired alert is a message, never an order.
 */
export function AlertsMonitor({
  onSymbol,
  compact = false,
  readOnly = false,
}: {
  onSymbol?: (symbol: string) => void;
  compact?: boolean;
  readOnly?: boolean;
}) {
  const notes = useApi<Envelope<NotificationsView>>(`${ALERT_PATHS.notifications}?limit=${compact ? 30 : 200}`, {
    refreshMs: 10_000,
  });
  const rules = useApi<Envelope<AlertRulesView>>(ALERT_PATHS.rules, { refreshMs: 30_000 });
  const client = useQueryClient();
  const notify = useToast();
  const now = useClock();
  const browser = useBrowserNotify(notes.status === "success" ? notes.data.data.items : null);
  const refresh = () => {
    void invalidatePath(client, ALERT_PATHS.notifications);
    void invalidatePath(client, ALERT_PATHS.rules);
  };
  const sym = (s: string) =>
    onSymbol ? (
      <button type="button" className="ws-sym" onClick={() => onSymbol(s)}>
        {s}
      </button>
    ) : (
      <Link href={`/markets/${encodeURIComponent(s)}`} className="mono" prefetch={false}>
        {s}
      </Link>
    );
  return (
    <div className="ntf" data-testid="alerts-monitor">
      <AsyncBoundary state={notes} label="alert notifications" onRetry={notes.reload}>
        {(envelope) => {
          const v = envelope.data;
          const newest = v.items[0]?.seq ?? null;
          return (
            <section aria-label="Notifications" className="ntf__section">
              <div className="ws-panel__bar">
                <span className="ntf__unread" data-testid="ntf-unread" data-unread={v.unread}>
                  {v.unread} UNREAD
                </span>
                <span className="muted">of {v.total}</span>
                <span className="ntf__eval" data-state={v.evaluator.state} title={v.evaluator.detail} data-testid="ntf-evaluator">
                  {EVAL_WORD[v.evaluator.state]}
                  {v.evaluator.last_run_at ? ` · ${formatAge(Math.max(0, (now - parseUtc(v.evaluator.last_run_at).getTime()) / 1000))}` : ""}
                </span>
                {!readOnly ? (
                  <button
                    type="button"
                    className="ws-btn"
                    disabled={v.unread === 0 || newest === null}
                    data-testid="ntf-mark-read"
                    onClick={async () => {
                      if (newest === null) return;
                      try {
                        await markRead(newest);
                      } catch (error) {
                        notify(error instanceof Error ? error.message : String(error));
                      }
                      refresh();
                    }}
                  >
                    Mark all read
                  </button>
                ) : null}
                {browser.supported ? (
                  <button
                    type="button"
                    className="ws-btn ws-btn--ghost"
                    aria-pressed={browser.enabled}
                    data-testid="ntf-browser"
                    title={browser.permission === "denied" ? "This browser has blocked notifications for this site." : "Show new alerts as browser notifications while this screen is open."}
                    disabled={browser.permission === "denied"}
                    onClick={() => void browser.toggle()}
                  >
                    Browser notifications: {browser.enabled ? "on" : browser.permission === "denied" ? "blocked" : "off"}
                  </button>
                ) : null}
              </div>
              <PanelStamp source={envelope.source} />
              {v.evaluator.state !== "live" ? (
                <p className="ntf__warn" role="status" data-testid="ntf-evaluator-detail">
                  {v.evaluator.detail}
                </p>
              ) : null}
              {v.items.length === 0 ? (
                <p className="muted" data-testid="ntf-empty">
                  No alert has fired. That is an answer only while the evaluator above is evaluating.
                </p>
              ) : (
                <ol className="ntf__list" data-testid="ntf-list">
                  {v.items.map((n) => (
                    <li key={n.seq} className="ntf__item" data-testid="ntf-item" data-read={n.read} data-tone={n.tones.state}>
                      <span className="mono" title={`Fired ${formatTimestamp(n.fired_at)}; quote ${formatTimestamp(n.quote_time)}`}>
                        {formatUtcTime(n.fired_at)}
                      </span>
                      <span>{sym(n.instrument)}</span>
                      <span className={n.tones.state ? `tone-${n.tones.state}` : undefined}>{n.message}</span>
                      <span className="muted mono">#{n.seq}</span>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          );
        }}
      </AsyncBoundary>
      {!compact ? (
        <AsyncBoundary state={rules} label="alert rules" onRetry={rules.reload}>
          {(envelope) => (
            <section aria-label="Rules" className="ntf__section">
              <h2 className="pulse__h">Rules ({envelope.data.rules.length})</h2>
              {envelope.data.can_edit && !readOnly ? <NewRule onDone={refresh} /> : null}
              <RulesTable rules={envelope.data.rules} canEdit={envelope.data.can_edit && !readOnly} onDone={refresh} sym={sym} />
            </section>
          )}
        </AsyncBoundary>
      ) : null}
    </div>
  );
}

function RulesTable({
  rules,
  canEdit,
  onDone,
  sym,
}: {
  rules: PriceRule[];
  canEdit: boolean;
  onDone: () => void;
  sym: (s: string) => React.ReactNode;
}) {
  const notify = useToast();
  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
    } catch (error) {
      notify(
        error instanceof ApiError && error.code === "alert_rule_conflict"
          ? "This rule changed elsewhere; reloaded."
          : error instanceof Error
            ? error.message
            : String(error),
      );
    }
    onDone();
  };
  const spec = (r: PriceRule, patch: Partial<RuleWrite> = {}): RuleWrite => ({
    instrument: r.instrument,
    kind: r.kind,
    level: r.level.value as number,
    side: r.side,
    mode: r.mode,
    note: r.note,
    enabled: r.enabled,
    ...patch,
  });
  if (rules.length === 0) return <p className="muted">No rules. Add one above.</p>;
  return (
    <div className="table-wrap">
      <table className="dense ws-table" data-testid="ntf-rules">
        <thead>
          <tr>
            <th scope="col">Instr</th>
            <th scope="col">When</th>
            <th scope="col" className="num">Level</th>
            <th scope="col">Side</th>
            <th scope="col">Mode</th>
            <th scope="col">State</th>
            <th scope="col" className="num">Fired</th>
            <th scope="col" className="num">Last seen</th>
            <th scope="col">Note</th>
            {canEdit ? <th scope="col">Actions</th> : null}
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id} data-testid="ntf-rule" data-state={r.state}>
              <td>{sym(r.instrument)}</td>
              <td className="mono">{r.kind}</td>
              <td className="num">
                <FigureValue figure={r.level} showChip={false} />
              </td>
              <td className="mono">{r.side}</td>
              <td className="mono">{r.mode}</td>
              <td className="mono">{r.state.toUpperCase()}</td>
              <td className="num">
                <FigureValue figure={r.fired_count} showChip={false} />
              </td>
              <td className="num">
                <FigureValue figure={r.last_price} showChip={false} />
              </td>
              <td>{r.note}</td>
              {canEdit ? (
                <td className="row">
                  <button
                    type="button"
                    className="ws-btn ws-btn--ghost"
                    data-testid="ntf-rule-toggle"
                    onClick={() => void act(() => updateRule(r.id, r.version, spec(r, { enabled: !r.enabled })))}
                  >
                    {r.enabled ? "Disable" : "Enable"}
                  </button>
                  {r.state === "fired" ? (
                    <button type="button" className="ws-btn ws-btn--ghost" onClick={() => void act(() => updateRule(r.id, r.version, spec(r)))}>
                      Re-arm
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="ws-btn ws-btn--ghost"
                    data-testid="ntf-rule-delete"
                    onClick={() => void act(() => deleteRule(r.id, r.version))}
                  >
                    Delete
                  </button>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NewRule({ onDone }: { onDone: () => void }) {
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments");
  const notify = useToast();
  const [instrument, setInstrument] = useState("EURUSD");
  const [kind, setKind] = useState<RuleWrite["kind"]>("above");
  const [level, setLevel] = useState("");
  const [side, setSide] = useState<NonNullable<RuleWrite["side"]>>("mid");
  const [mode, setMode] = useState<NonNullable<RuleWrite["mode"]>>("once");
  const [note, setNote] = useState("");
  const symbols = instruments.status === "success" ? instruments.data.items.map((i) => i.symbol) : [];
  const parsed = Number(level);
  const valid = symbols.includes(instrument) && level.trim() !== "" && Number.isFinite(parsed) && parsed > 0;
  return (
    <form
      className="ws-panel__bar ntf__form"
      aria-label="New alert rule"
      data-testid="ntf-new"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!valid) return;
        try {
          await createRule({ instrument, kind, level: parsed, side, mode, note, enabled: true });
          setLevel("");
          setNote("");
        } catch (error) {
          notify(error instanceof Error ? error.message : String(error));
        }
        onDone();
      }}
    >
      <label className="ws-inline">
        <span className="sr-only">Instrument</span>
        <input className="ws-input mono" list="ntf-symbols" value={instrument} onChange={(e) => setInstrument(e.target.value.toUpperCase())} data-testid="ntf-new-instrument" />
      </label>
      <datalist id="ntf-symbols">
        {symbols.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>
      <label className="ws-inline">
        <span className="sr-only">When</span>
        <select value={kind} onChange={(e) => setKind(e.target.value as RuleWrite["kind"])} data-testid="ntf-new-kind">
          <option value="above">at or above</option>
          <option value="below">below</option>
          <option value="cross">crosses</option>
        </select>
      </label>
      <label className="ws-inline">
        <span className="sr-only">Level</span>
        <input className="ws-input mono" inputMode="decimal" placeholder="level" value={level} onChange={(e) => setLevel(e.target.value)} data-testid="ntf-new-level" />
      </label>
      <label className="ws-inline">
        <span className="sr-only">Side</span>
        <select value={side} onChange={(e) => setSide(e.target.value as typeof side)}>
          <option value="mid">mid</option>
          <option value="bid">bid</option>
          <option value="ask">ask</option>
        </select>
      </label>
      <label className="ws-inline">
        <span className="sr-only">Mode</span>
        <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
          <option value="once">once</option>
          <option value="rearm">every time</option>
        </select>
      </label>
      <label className="ws-inline">
        <span className="sr-only">Note</span>
        <input className="ws-input" placeholder="note" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
      <button type="submit" className="ws-btn" disabled={!valid} data-testid="ntf-new-add">
        Add alert
      </button>
    </form>
  );
}
