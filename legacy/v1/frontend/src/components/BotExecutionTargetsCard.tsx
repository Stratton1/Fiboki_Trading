"use client";

import { useState } from "react";
import useSWR, { mutate as swrMutate } from "swr";
import { Crosshair, Plus, X } from "lucide-react";

import { api, ApiError } from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";
import { InfoTip } from "@/components/InfoTip";

const BROKER_LABELS: Record<string, string> = {
  paper: "Paper",
  ig: "IG",
  tradovate: "Tradovate",
};

function brokerVariant(broker: string): "ok" | "warn" | "error" | "neutral" | "info" {
  if (broker === "paper") return "neutral";
  if (broker === "ig") return "info";
  if (broker === "tradovate") return "warn";
  return "neutral";
}

interface Props {
  botId: string;
}

/**
 * Phase-2 per-bot execution targets panel for the bot detail page.
 *
 * Shows the execution accounts attached to a bot, lets the operator add
 * a new target from the list of available accounts, and toggle/remove
 * existing targets. Backwards-compatible: a bot with no targets simply
 * shows an empty state explaining the default-Paper fallback.
 */
export function BotExecutionTargetsCard({ botId }: Props) {
  const { data: targets, mutate } = useSWR(
    `/paper/bots/${botId}/targets`,
    () => api.listBotTargets(botId),
    { refreshInterval: 30000 },
  );
  const { data: accounts } = useSWR(
    "/execution/accounts",
    () => api.listExecutionAccounts(),
    { refreshInterval: 60000 },
  );

  const [adding, setAdding] = useState(false);
  const [pickAccountId, setPickAccountId] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);

  const attachedIds = new Set((targets ?? []).map((t) => t.execution_account_id));
  const availableAccounts = (accounts ?? []).filter((a) => !attachedIds.has(a.id));

  async function handleAttach() {
    if (!pickAccountId || typeof pickAccountId !== "number") return;
    setError(null);
    try {
      await api.attachBotTarget(botId, { execution_account_id: pickAccountId });
      setAdding(false);
      setPickAccountId("");
      await mutate();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function handleToggle(targetId: number, isEnabled: boolean) {
    setError(null);
    try {
      await api.patchBotTarget(botId, targetId, { is_enabled: !isEnabled });
      await mutate();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  async function handleDelete(targetId: number) {
    setError(null);
    try {
      await api.deleteBotTarget(botId, targetId);
      await mutate();
      // Bust the parent's bot detail cache too — not strictly required but
      // keeps the picker fresh.
      void swrMutate(`/paper/bots/${botId}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }

  return (
    <div className="card mb-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Crosshair size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">
            Execution Targets
            <InfoTip text="Phase 2 multi-broker fan-out. This bot's signals are sent to every enabled target. With no targets, the bot defaults to the seeded Paper account in db_targets mode." />
          </p>
        </div>
        {!adding && availableAccounts.length > 0 ? (
          <button
            type="button"
            className="btn btn-ghost text-xs flex items-center gap-1"
            onClick={() => setAdding(true)}
          >
            <Plus size={12} />
            Attach account
          </button>
        ) : null}
      </div>

      {adding ? (
        <div className="mb-4 px-3 py-2 rounded-md border border-border-muted bg-background-muted">
          <div className="flex items-center gap-2">
            <select
              value={pickAccountId === "" ? "" : String(pickAccountId)}
              onChange={(e) =>
                setPickAccountId(e.target.value === "" ? "" : Number(e.target.value))
              }
              className="input text-xs"
            >
              <option value="">Pick an account…</option>
              {availableAccounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} — {BROKER_LABELS[a.broker] ?? a.broker} · {a.environment}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleAttach}
              disabled={!pickAccountId}
              className="btn btn-primary text-xs px-3 py-1"
            >
              Attach
            </button>
            <button
              type="button"
              onClick={() => {
                setAdding(false);
                setPickAccountId("");
                setError(null);
              }}
              className="btn btn-ghost text-xs"
            >
              Cancel
            </button>
          </div>
          {availableAccounts.length === 0 ? (
            <p className="text-xs text-foreground-muted mt-2">
              All execution accounts are already attached to this bot.
            </p>
          ) : null}
        </div>
      ) : null}

      {error ? (
        <p className="text-danger text-xs mb-3">{error}</p>
      ) : null}

      {!targets || targets.length === 0 ? (
        <p className="text-foreground-muted text-sm">
          No execution targets attached. In <code>db_targets</code> router mode this bot
          will fall back to the default Paper account.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Account</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Broker</th>
                <th className="text-left px-3 py-2 text-xs text-foreground-muted">Env</th>
                <th className="text-right px-3 py-2 text-xs text-foreground-muted">Allocation</th>
                <th className="text-center px-3 py-2 text-xs text-foreground-muted">Enabled</th>
                <th className="text-right px-3 py-2 text-xs text-foreground-muted">Actions</th>
              </tr>
            </thead>
            <tbody>
              {targets.map((t) => {
                const acct = t.account;
                const allocation = t.allocation_override ?? acct?.allocated_capital ?? null;
                return (
                  <tr key={t.id} className="border-b border-gray-100">
                    <td className="px-3 py-1.5 font-medium">
                      {acct?.name ?? `Account ${t.execution_account_id}`}
                    </td>
                    <td className="px-3 py-1.5">
                      {acct ? (
                        <StatusBadge variant={brokerVariant(acct.broker)}>
                          {BROKER_LABELS[acct.broker] ?? acct.broker}
                        </StatusBadge>
                      ) : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-xs text-foreground-muted">
                      {acct?.environment ?? "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular-nums">
                      {allocation !== null
                        ? allocation.toLocaleString(undefined, {
                            style: "currency",
                            currency: "GBP",
                            maximumFractionDigits: 0,
                          })
                        : "—"}
                      {t.allocation_override !== null ? (
                        <span className="ml-1 text-[10px] text-foreground-muted">override</span>
                      ) : null}
                    </td>
                    <td className="px-3 py-1.5 text-center">
                      <button
                        type="button"
                        onClick={() => handleToggle(t.id, t.is_enabled)}
                        className={`text-xs px-2 py-0.5 rounded-full border ${
                          t.is_enabled
                            ? "bg-green-50 text-green-800 border-green-200"
                            : "bg-gray-50 text-foreground-muted border-gray-200"
                        }`}
                      >
                        {t.is_enabled ? "On" : "Off"}
                      </button>
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <button
                        type="button"
                        onClick={() => handleDelete(t.id)}
                        className="text-foreground-muted hover:text-danger transition"
                        title="Detach this target"
                      >
                        <X size={14} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
