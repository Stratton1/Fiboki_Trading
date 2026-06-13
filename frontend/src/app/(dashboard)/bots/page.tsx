"use client";

import { Fragment, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useBots, useAccount } from "@/lib/hooks/use-bots";
import { currencySymbol as getCurrencySymbol, formatCurrency, formatPnl } from "@/lib/format-currency";
import GroupedInstrumentSelect from "@/components/GroupedInstrumentSelect";
import WatchlistPicker from "@/components/WatchlistPicker";
import { useWatchlists } from "@/lib/hooks/use-watchlists";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import { Bot, Loader2, Wallet, TrendingUp, CalendarDays, Activity, AlertTriangle, BarChart3, Search, Star, ChevronDown, ExternalLink, Flag, Download, Archive, ChevronRight, ShieldAlert, X } from "lucide-react";
import { InfoTip } from "@/components/InfoTip";
import { strategyShortName } from "@/lib/strategy-names";
import { useShortlist } from "@/lib/hooks/use-shortlist";
import Link from "next/link";

interface BotItem {
  bot_id: string;
  strategy_id: string;
  instrument: string;
  timeframe: string;
  state: string;
}

// P0-7: include position_open so a bot in a trade renders distinctly from
// a stopped bot on the list view. The detail page already does this.
const STATE_VARIANT: Record<string, "ok" | "warn" | "neutral"> = {
  monitoring: "ok",
  position_open: "ok",
  paused: "warn",
  stopped: "neutral",
};

// P0-6: actions whose semantics depend on execution being permitted.
// When the kill switch is active the worker silently refuses to route
// orders, so even though these transitions succeed in the DB the bot
// will not actually trade. We block them at the UI layer to stop the
// operator believing they have restored execution.
type PendingAction =
  | { kind: "delete-bot"; botId: string; label: string }
  | { kind: "stop-bot"; botId: string; label: string }
  | { kind: "delete-all"; count: number; trades: number }
  | { kind: "restart-all"; count: number }
  | { kind: "smart-deploy" };

export default function BotsPage() {
  const { data: bots, mutate: mutateBots } = useBots();
  const { data: account } = useAccount();
  const { data: fleet } = useSWR("/paper/fleet", () => api.fleet(), { refreshInterval: 5000 });
  const { data: strategies } = useSWR("strategies", () => api.strategies());
  const { data: instruments } = useSWR("instruments", () => api.instruments());
  const { data: execMode } = useSWR("/execution/mode", () => api.executionMode(), { refreshInterval: 30000 });
  const isIgDemo = execMode?.mode === "ig_demo";
  // P0-6: kill-switch state drives action gating on this page.
  const killSwitchActive = execMode?.kill_switch_active ?? false;
  // P1-9: surface the registered-strategy count alongside the
  // operator-visible filter. systemStatus reports both numbers.
  const { data: systemStatus } = useSWR("/system/status", () => api.systemStatus(), { refreshInterval: 60000 });
  const { data: igHealth } = useSWR(
    isIgDemo ? "/execution/ig-health" : null,
    () => api.igHealth(),
    { refreshInterval: 30000 }
  );
  const { filterSet } = useWatchlists();
  const [strategy, setStrategy] = useState("");
  const [instrument, setInstrument] = useState("");
  const [timeframe, setTimeframe] = useState("H1");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actingBotId, setActingBotId] = useState<string | null>(null);
  const [groupBy, setGroupBy] = useState<"none" | "strategy">("none");
  const { shortlist } = useShortlist();
  const [showShortlistPicker, setShowShortlistPicker] = useState(false);
  const [smartDeployLoading, setSmartDeployLoading] = useState(false);
  const [smartDeployResult, setSmartDeployResult] = useState<string | null>(null);
  const { data: phases, mutate: mutatePhases } = useSWR("/paper/phases", () => api.listPhases(), { refreshInterval: 60000 });
  const { data: activePhase, mutate: mutateActivePhase } = useSWR("/paper/phases/active", () => api.getActivePhase(), { refreshInterval: 60000 });
  const [showTransitionDialog, setShowTransitionDialog] = useState(false);
  const [transitionLoading, setTransitionLoading] = useState(false);
  const [transitionError, setTransitionError] = useState<string | null>(null);
  const [transitionResult, setTransitionResult] = useState<string | null>(null);
  const [archiveName, setArchiveName] = useState("Phase A — Initial Testing");
  const [newPhaseName, setNewPhaseName] = useState("Phase B — Live Forward Tracking");
  const [sortField, setSortField] = useState<"strategy" | "instrument" | "tf" | "state" | "bars" | "trades" | "pnl">("instrument");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // P0-1..5: pending destructive / fleet-wide action awaiting confirmation.
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [confirmBusy, setConfirmBusy] = useState(false);

  function handleSort(field: typeof sortField) {
    if (sortField === field) setSortDir(d => d === "asc" ? "desc" : "asc");
    else { setSortField(field); setSortDir("asc"); }
  }
  const sortArrow = (f: typeof sortField) => sortField === f ? (sortDir === "asc" ? " ▲" : " ▼") : "";

  function loadFromShortlist(entry: { strategy_id: string; instrument: string; timeframe: string }) {
    setStrategy(entry.strategy_id);
    setInstrument(entry.instrument);
    setTimeframe(entry.timeframe);
    setShowShortlistPicker(false);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!strategy || !instrument) return;
    setCreating(true);
    setError(null);
    try {
      await api.createBot({ strategy_id: strategy, instrument, timeframe });
      setStrategy("");
      setInstrument("");
      await mutateBots();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create bot");
    } finally {
      setCreating(false);
    }
  }

  async function handlePause(id: string) {
    if (actingBotId) return;
    setActingBotId(id);
    setActionError(null);
    try {
      await api.pauseBot(id);
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to pause bot");
    } finally {
      setActingBotId(null);
    }
  }

  async function handleStop(id: string) {
    if (actingBotId) return;
    setActingBotId(id);
    setActionError(null);
    try {
      await api.stopBot(id);
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to stop bot");
    } finally {
      setActingBotId(null);
    }
  }

  async function handleResume(id: string) {
    if (actingBotId) return;
    setActingBotId(id);
    setActionError(null);
    try {
      await api.resumeBot(id);
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to resume bot");
    } finally {
      setActingBotId(null);
    }
  }

  async function handleRestart(id: string) {
    if (actingBotId) return;
    setActingBotId(id);
    setActionError(null);
    try {
      await api.restartBot(id);
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to restart bot");
    } finally {
      setActingBotId(null);
    }
  }

  async function handleDelete(id: string) {
    if (actingBotId) return;
    setActingBotId(id);
    setActionError(null);
    try {
      await api.deleteBot(id);
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to delete bot");
    } finally {
      setActingBotId(null);
    }
  }

  async function doSmartDeploy() {
    setSmartDeployLoading(true);
    setSmartDeployResult(null);
    setActionError(null);
    try {
      const res = await api.smartDeploy({ top_n: 5, min_score: 0.3 });
      if (res.deployed === 0) {
        setSmartDeployResult(
          res.skipped > 0
            ? `All top combos already have bots (${res.skipped} skipped)`
            : "No research results found. Run Auto Scout on the Research page first."
        );
      } else {
        setSmartDeployResult(
          `Deployed ${res.deployed} bot${res.deployed !== 1 ? "s" : ""} from top research results${res.skipped > 0 ? ` (${res.skipped} already existed)` : ""}`
        );
        await mutateBots();
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Smart Deploy failed");
    } finally {
      setSmartDeployLoading(false);
    }
  }

  async function doDeleteAll() {
    setActionError(null);
    try {
      await api.deleteAllBots();
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to delete bots");
    }
  }

  async function doRestartAll() {
    setActionError(null);
    try {
      await api.restartAllBots();
      await mutateBots();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to restart bots");
    }
  }

  // P0-1..5: every destructive or fleet-wide action goes through
  // pendingAction so the operator sees an in-page confirm with the exact
  // damage / scope. Routine reversible actions (Pause / Resume / Restart
  // for a single bot) still execute on first click.
  async function confirmPendingAction() {
    if (!pendingAction) return;
    setConfirmBusy(true);
    try {
      switch (pendingAction.kind) {
        case "delete-bot":
          await handleDelete(pendingAction.botId);
          break;
        case "stop-bot":
          await handleStop(pendingAction.botId);
          break;
        case "delete-all":
          await doDeleteAll();
          break;
        case "restart-all":
          await doRestartAll();
          break;
        case "smart-deploy":
          await doSmartDeploy();
          break;
      }
      setPendingAction(null);
    } finally {
      setConfirmBusy(false);
    }
  }

  const igBal = isIgDemo && igHealth?.balance != null ? igHealth.balance : null;
  const balance = igBal ?? (account?.balance ?? 0);
  const equity = igBal ?? (account?.equity ?? 0);
  const dailyPnl = account?.daily_pnl ?? 0;
  const currency = account?.currency ?? "GBP";
  const sym = getCurrencySymbol(currency);
  const botList = (bots ?? []) as BotItem[];
  const stoppedBots = botList.filter(b => b.state === "stopped");
  const pausedBots = botList.filter(b => b.state === "paused");
  const fleetBots = fleet?.bots ?? [];

  // P1-10: human-readable execution-mode label for the subtitle. The
  // global ExecutionModeBanner says it once at the top of the layout;
  // operators on /bots benefit from seeing it next to the page title too.
  const execLabel = (() => {
    if (!execMode) return null;
    if (execMode.mode === "paper") return "Paper mode";
    if (execMode.mode === "ig_demo") {
      return execMode.live_execution_enabled
        ? "IG Demo · execution enabled (no real money)"
        : "IG Demo · execution disabled (paper-only)";
    }
    return execMode.mode;
  })();
  const subtitle = execLabel
    ? `Manage trading bots and monitor fleet performance · ${execLabel}`
    : "Manage trading bots and monitor fleet performance";

  // P1-9: how many of the registered strategies are exposed to the
  // operator. Helps the "why are there only 2 strategies in the dropdown"
  // confusion when FIBOKEI_VISIBLE_STRATEGIES is set.
  const visibleStrategyCount = strategies?.length ?? 0;
  const registeredStrategyCount = systemStatus?.strategies_loaded ?? null;
  const showStrategyHint =
    registeredStrategyCount !== null &&
    registeredStrategyCount > visibleStrategyCount &&
    visibleStrategyCount > 0;

  // Aggregate counts for confirm-banner copy.
  const totalTradeCount = fleet?.aggregate_trades ?? 0;

  // Sort bots for flat view
  const sortedBotList = [...botList].sort((a, b) => {
    const fa = fleetBots.find(fb => fb.bot_id === a.bot_id);
    const fb_ = fleetBots.find(fb => fb.bot_id === b.bot_id);
    let cmp = 0;
    switch (sortField) {
      case "strategy": cmp = a.strategy_id.localeCompare(b.strategy_id); break;
      case "instrument": cmp = a.instrument.localeCompare(b.instrument); break;
      case "tf": {
        const order = ["M1","M5","M15","M30","H1","H4","D"];
        cmp = order.indexOf(a.timeframe) - order.indexOf(b.timeframe);
        break;
      }
      case "state": cmp = a.state.localeCompare(b.state); break;
      case "bars": cmp = (fa?.bars_seen ?? 0) - (fb_?.bars_seen ?? 0); break;
      case "trades": cmp = (fa?.total_trades ?? 0) - (fb_?.total_trades ?? 0); break;
      case "pnl": cmp = (fa?.total_pnl ?? 0) - (fb_?.total_pnl ?? 0); break;
    }
    return sortDir === "asc" ? cmp : -cmp;
  });

  // Strategy groups for grouped view
  const strategyGroups = fleet?.strategy_groups ?? {};
  const groupedBots: Record<string, typeof fleetBots> = {};
  if (groupBy === "strategy") {
    for (const b of fleetBots) {
      if (!groupedBots[b.strategy_id]) groupedBots[b.strategy_id] = [];
      groupedBots[b.strategy_id].push(b);
    }
  }

  return (
    <div className="max-w-6xl">
      <PageHeader
        title="Bots"
        subtitle={subtitle}
        actions={
          stoppedBots.length > 0 ? (
            <button
              onClick={() =>
                setPendingAction({ kind: "restart-all", count: stoppedBots.length })
              }
              disabled={killSwitchActive}
              className="btn btn-primary flex items-center gap-2 disabled:opacity-50"
              title={
                killSwitchActive
                  ? "Kill switch is active — execution is halted"
                  : "Restart all stopped bots and put them back into monitoring mode"
              }
              aria-label={`Start all ${stoppedBots.length} stopped bots`}
            >
              <Activity size={14} /> Start All Bots ({stoppedBots.length})
            </button>
          ) : undefined
        }
      />

      {/* P0-6: when the kill switch is active, every "is this bot
          trading?" answer from this page becomes "no, regardless of state
          badge". Surface this clearly so the operator does not click
          Resume / Restart and believe execution has resumed. */}
      {killSwitchActive && (
        <div
          role="alert"
          className="mb-4 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
        >
          <ShieldAlert size={16} className="mt-0.5 shrink-0 text-red-700" />
          <div>
            <p className="font-medium">Kill switch is active — execution is halted</p>
            <p className="mt-0.5 text-xs text-red-800">
              Bots can still appear in <strong>monitoring</strong> state but the
              worker will not route any orders. Resume / Restart / Start-All
              / Smart Deploy are disabled until the kill switch is deactivated
              from <Link href="/system" className="underline hover:no-underline">System</Link>.
            </p>
          </div>
        </div>
      )}

      {/* Smart Deploy */}
      <div className="card-elevated flex flex-wrap items-center justify-between gap-4 mb-6">
        <div>
          <p className="text-sm font-medium">Smart Deploy</p>
          <p className="text-xs text-foreground-muted">
            Auto-create bots from your top 5 research results. Skips combos that already have bots.
          </p>
        </div>
        <button
          onClick={() => setPendingAction({ kind: "smart-deploy" })}
          disabled={smartDeployLoading || killSwitchActive}
          className="btn btn-primary disabled:opacity-50"
          title={killSwitchActive ? "Kill switch is active — execution is halted" : undefined}
          aria-label="Smart Deploy top research combos"
        >
          {smartDeployLoading ? <><Loader2 size={14} className="animate-spin" /> Deploying...</> : "Deploy Top Combos"}
        </button>
      </div>
      {smartDeployResult && (
        <div className="rounded-lg border border-green-200 bg-green-50 px-4 py-2 text-xs text-green-800 mb-4 flex items-center justify-between">
          <span>{smartDeployResult}</span>
          <button onClick={() => setSmartDeployResult(null)} className="text-green-500 hover:text-green-700 text-xs ml-4">Dismiss</button>
        </div>
      )}

      {/* Fleet Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3 mb-6">
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Balance</span>
            <Wallet size={14} className="text-foreground-muted" />
          </div>
          <p className="text-xl font-bold tracking-tight">{sym}{balance.toFixed(2)}</p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Equity</span>
            <TrendingUp size={14} className="text-foreground-muted" />
          </div>
          <p className="text-xl font-bold tracking-tight">{sym}{equity.toFixed(2)}</p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Daily PnL</span>
            <CalendarDays size={14} className="text-foreground-muted" />
          </div>
          <p className={`text-xl font-bold tracking-tight ${dailyPnl >= 0 ? "text-primary" : "text-danger"}`}>
            {dailyPnl >= 0 ? "+" : ""}{sym}{dailyPnl.toFixed(2)}
          </p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Running</span>
            <Activity size={14} className="text-foreground-muted" />
          </div>
          <p className="text-xl font-bold tracking-tight">
            {fleet?.running ?? 0}
            <span className="text-sm font-normal text-foreground-muted"> / {fleet?.total_bots ?? 0}</span>
          </p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Fleet PnL<InfoTip text="Realised PnL from closed trades across all bots in the current evaluation phase. Open positions are not marked-to-market in this number." /></span>
            <BarChart3 size={14} className="text-foreground-muted" />
          </div>
          <p className={`text-xl font-bold tracking-tight ${(fleet?.aggregate_pnl ?? 0) >= 0 ? "text-primary" : "text-danger"}`}>
            {(fleet?.aggregate_pnl ?? 0) >= 0 ? "+" : ""}{sym}{(fleet?.aggregate_pnl ?? 0).toFixed(2)}
          </p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">Fleet Trades</span>
            {(fleet?.stale ?? 0) > 0 && <AlertTriangle size={14} className="text-amber-500" />}
          </div>
          <p className="text-xl font-bold tracking-tight">
            {fleet?.aggregate_trades ?? 0}
            {(fleet?.stale ?? 0) > 0 && (
              <span className="text-xs font-normal text-amber-600 ml-1">({fleet?.stale} stale)</span>
            )}
          </p>
        </div>
      </div>

      {/* Strategy Family Summary */}
      {Object.keys(strategyGroups).length > 1 && (
        <div className="card mb-6">
          <p className="section-label">By Strategy</p>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {Object.entries(strategyGroups).map(([sid, g]) => (
              <div key={sid} className="border border-gray-200 rounded-lg p-3">
                <p className="text-sm font-medium truncate">{sid}</p>
                <div className="flex items-baseline gap-3 mt-1">
                  <span className="text-xs text-foreground-muted">{g.count} bots</span>
                  <span className="text-xs text-foreground-muted">{g.running} active</span>
                  <span className={`text-xs font-medium ${g.pnl >= 0 ? "text-primary" : "text-danger"}`}>
                    {g.pnl >= 0 ? "+" : ""}{sym}{g.pnl.toFixed(2)}
                  </span>
                </div>
                <p className="text-xs text-foreground-muted mt-0.5">{g.trades} trades</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Workflow explainer (show when no bots exist) */}
      {botList.length === 0 && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-xs text-blue-800 mb-6">
          <strong>How Bots work:</strong> A bot monitors a strategy/instrument/timeframe combo in real time.
          {isIgDemo ? " Orders are routed to your IG demo account." : " No real money is at risk."}
          {" "}The recommended path: <strong>Research</strong> (find promising combos) → <strong>Save to Shortlist</strong> → <strong>Create Bot</strong> → observe performance.
          You can also add bots manually below.
        </div>
      )}

      {/* Add Bot Form */}
      <form onSubmit={handleCreate} className="card-elevated mb-6">
        <p className="section-label">Add Bot<InfoTip text="Creates a bot that monitors this strategy/instrument/timeframe combo. The bot starts in 'monitoring' state and evaluates signals on each new candle close." /></p>
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Strategy</label>
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)} className="input">
              <option value="">Select strategy</option>
              {strategies?.map((s: any) => (
                <option key={s.id} value={s.id}>{s.name || s.id}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="flex items-center gap-2 text-xs text-foreground-muted mb-1.5">
              Instrument
              <WatchlistPicker />
            </label>
            <GroupedInstrumentSelect instruments={instruments ?? []} value={instrument} onChange={setInstrument} className="input" watchlistFilter={filterSet} />
          </div>
          <div>
            <label className="block text-xs text-foreground-muted mb-1.5">Timeframe</label>
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} className="input">
              <option value="M1">M1</option>
              <option value="M5">M5</option>
              <option value="M15">M15</option>
              <option value="M30">M30</option>
              <option value="H1">H1</option>
              <option value="H4">H4</option>
            </select>
          </div>
          <button
            type="submit"
            disabled={creating || !strategy || !instrument || killSwitchActive}
            className="btn btn-primary disabled:opacity-50"
            title={killSwitchActive ? "Kill switch is active — execution is halted" : undefined}
          >
            {creating && <Loader2 size={14} className="animate-spin" />}
            {creating ? "Creating..." : "Add Bot"}
          </button>
          <div className="relative">
            <button
              type="button"
              onClick={() => setShowShortlistPicker(!showShortlistPicker)}
              className="btn btn-secondary text-xs"
              disabled={shortlist.length === 0}
              title={shortlist.length === 0 ? "No shortlisted combos yet" : "Load combo from Shortlist"}
            >
              <Star size={12} />
              From Shortlist
              <ChevronDown size={12} />
            </button>
            {showShortlistPicker && shortlist.length > 0 && (
              <div className="absolute top-full left-0 mt-1 bg-white border border-border rounded-lg shadow-lg z-20 w-72 max-h-60 overflow-y-auto">
                {shortlist.filter((e) => e.status === "active").map((e) => (
                  <button
                    key={e.id}
                    type="button"
                    onClick={() => loadFromShortlist(e)}
                    className="w-full text-left px-3 py-2 text-sm hover:bg-background-muted transition-colors flex items-center gap-2"
                  >
                    <span className="font-medium">{e.strategy_id}</span>
                    <span className="text-foreground-muted">{e.instrument}</span>
                    <span className="text-foreground-muted">{e.timeframe}</span>
                    <span className="ml-auto text-xs text-foreground-muted">{e.score.toFixed(2)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        {error && <p className="text-danger text-sm mt-3">{error}</p>}
        {showStrategyHint && (
          <p className="text-xs text-foreground-muted mt-3">
            Showing {visibleStrategyCount} of {registeredStrategyCount} registered strategies.
            The remaining {registeredStrategyCount - visibleStrategyCount} are hidden by the
            <code className="mx-1 px-1 bg-background-muted rounded text-[10px]">FIBOKEI_VISIBLE_STRATEGIES</code>
            environment variable. Edit it on Railway to expose more.
          </p>
        )}
      </form>

      {actionError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 mb-4 flex items-center justify-between">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="text-red-500 hover:text-red-700 transition text-xs ml-4">Dismiss</button>
        </div>
      )}

      {stoppedBots.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 mb-4 flex items-center justify-between">
          <span>
            <strong>{stoppedBots.length} bot{stoppedBots.length !== 1 ? "s" : ""} stopped</strong>
            {" "}— click Restart All to put them back into monitoring mode.
          </span>
          <button
            onClick={() => setPendingAction({ kind: "restart-all", count: stoppedBots.length })}
            disabled={killSwitchActive}
            className="btn btn-secondary text-xs ml-4 flex items-center gap-1.5 shrink-0 disabled:opacity-50"
            title={killSwitchActive ? "Kill switch is active — execution is halted" : undefined}
          >
            <Activity size={11} /> Restart All
          </button>
        </div>
      )}

      {/* P1-11: paused bots get the same banner shape as stopped bots
          since the operational impact (no new signal evaluation) is
          identical. */}
      {pausedBots.length > 0 && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 mb-4 flex items-center justify-between">
          <span>
            <strong>{pausedBots.length} bot{pausedBots.length !== 1 ? "s" : ""} paused</strong>
            {" "}— signal evaluation is halted. Resume each from the row controls or detail page.
          </span>
        </div>
      )}

      {/* ── Phase Management ─────────────────────────────────── */}
      {(() => {
        const isFirstUse = !phases?.length && !activePhase;
        const slugify = (s: string) =>
          s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 20);

        return (
      <div className="card mb-6" data-testid="phase-management">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold flex items-center gap-2">
            <Flag size={14} className="text-primary" />
            Evaluation Phases
            <InfoTip text="Each evaluation phase tracks paper + IG demo performance from a clean £1,000 baseline. Archiving a phase preserves the full trade history so you can compare periods side-by-side." />
          </h2>
          <p className="text-xs text-foreground-muted mt-0.5">
            {isFirstUse
              ? "Capture your existing bots & trades as Phase A, then start a fresh £1,000 evaluation."
              : "Archive the current phase and start a clean £1,000 forward-tracking evaluation."}
          </p>
        </div>
        {isFirstUse && (
          <span className="text-xs text-blue-700 bg-blue-50 px-2 py-1 rounded-lg flex items-center gap-1.5">
            <Flag size={11} /> Not yet set up
          </span>
        )}
        {!isFirstUse && !activePhase && (
          <span className="text-xs text-amber-700 bg-amber-50 px-2 py-1 rounded-lg flex items-center gap-1.5">
            <AlertTriangle size={11} /> No active phase
          </span>
        )}
      </div>

      {/* First-use explanation */}
      {isFirstUse && (
        <div className="mb-4 p-3 rounded-lg border border-blue-200 bg-blue-50 text-xs text-blue-800">
          <p className="font-medium mb-1">How to get started</p>
          <p>Click <strong>Initialize Phase Tracking</strong> below. It will:</p>
          <ol className="mt-1 ml-3 space-y-0.5 list-decimal">
            <li>Save all your existing bots &amp; trades as <strong>Phase A</strong> (nothing is deleted)</li>
            <li>Reset the paper account to a clean <strong>£1,000</strong> baseline</li>
            <li>Start a new forward-tracking evaluation phase</li>
          </ol>
        </div>
      )}

      {/* Active Phase */}
      {activePhase && (
        <div className="mb-4 p-3 rounded-lg border border-primary/20 bg-primary/4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs font-semibold text-primary flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-primary inline-block" />
                Active — {activePhase.name}
              </p>
              <p className="text-xs text-foreground-muted mt-0.5">
                Started {new Date(activePhase.started_at).toLocaleDateString()} &middot;{" "}
                {activePhase.total_trades} trade{activePhase.total_trades !== 1 ? "s" : ""} &middot;{" "}
                Baseline: £{activePhase.normalized_baseline.toFixed(0)}
                {activePhase.net_pnl !== 0 && (
                  <span className={`ml-1 font-medium ${activePhase.net_pnl >= 0 ? "text-primary" : "text-danger"}`}>
                    ({activePhase.net_pnl >= 0 ? "+" : ""}£{activePhase.net_pnl.toFixed(2)})
                  </span>
                )}
              </p>
            </div>
            <a
              href={`${api.exportAllTrades()}${typeof window !== "undefined" && localStorage.getItem("fibokei_token") ? `?token=${localStorage.getItem("fibokei_token")}` : ""}`}
              className="btn btn-secondary text-xs flex items-center gap-1.5 shrink-0"
              title="Export all trades to Excel"
            >
              <Download size={11} /> Export Trades
            </a>
          </div>
        </div>
      )}

      {/* Archived Phases */}
      {(phases ?? []).filter((p) => !p.is_active).length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-foreground-muted mb-2 flex items-center gap-1.5">
            <Archive size={11} /> Archived Phases
          </p>
          <div className="space-y-2">
            {(phases ?? []).filter((p) => !p.is_active).map((p) => (
              <div key={p.id} className="flex items-center justify-between gap-3 p-2.5 rounded-lg bg-background-muted text-sm">
                <div>
                  <p className="font-medium text-xs">{p.name}</p>
                  <p className="text-[11px] text-foreground-muted">
                    {new Date(p.started_at).toLocaleDateString()} → {p.archived_at ? new Date(p.archived_at).toLocaleDateString() : "—"} &middot;{" "}
                    {p.total_trades} trades &middot;{" "}
                    Net PnL:{" "}
                    <span className={p.net_pnl >= 0 ? "text-primary font-medium" : "text-danger font-medium"}>
                      {p.net_pnl >= 0 ? "+" : ""}£{p.net_pnl.toFixed(2)}
                    </span>
                    {p.final_balance != null && (
                      <> &middot; Final: £{p.final_balance.toFixed(2)}</>
                    )}
                  </p>
                </div>
                <a
                  href={api.exportPhase(p.id)}
                  className="btn btn-secondary text-xs flex items-center gap-1 shrink-0"
                  title={`Export ${p.name} trades`}
                >
                  <Download size={10} /> .xlsx
                </a>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Transition Button */}
      {transitionResult ? (
        <div className="p-3 rounded-lg bg-green-50 border border-green-200 text-sm text-green-800 flex items-center gap-2">
          <span>✓</span> {transitionResult}
        </div>
      ) : (
        <button
          onClick={() => { setShowTransitionDialog(true); setTransitionError(null); }}
          className="btn btn-secondary text-xs flex items-center gap-2"
          disabled={transitionLoading}
        >
          <Flag size={12} />
          {isFirstUse ? "Initialize Phase Tracking" : "Begin Phase Transition"}
          <ChevronRight size={10} />
        </button>
      )}

      {/* Transition Dialog */}
      {showTransitionDialog && (
        <div className="mt-4 p-4 rounded-xl border border-amber-200 bg-amber-50 space-y-3">
          <p className="text-sm font-semibold text-amber-900">
            {isFirstUse ? "Initialize Phase Tracking" : "Phase Transition"}
          </p>
          <p className="text-xs text-amber-800">
            {isFirstUse
              ? "Name the archive for your existing data (Phase A) and the new evaluation phase. No data will be deleted."
              : "This will archive the current active phase, reset the paper account to £1,000, and start a new evaluation. No data will be deleted."}
          </p>
          <div>
            <label className="text-xs font-medium text-foreground-muted block mb-1">
              Archive Name <span className="text-foreground-muted font-normal">(current bots &amp; trades)</span>
            </label>
            <input
              type="text"
              value={archiveName}
              onChange={(e) => setArchiveName(e.target.value)}
              className="input text-sm w-full"
              placeholder="Phase A — Initial Testing"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-foreground-muted block mb-1">New Phase Name</label>
            <input
              type="text"
              value={newPhaseName}
              onChange={(e) => setNewPhaseName(e.target.value)}
              className="input text-sm w-full"
              placeholder="Phase B — Live Forward Tracking"
            />
          </div>
          {transitionError && (
            <p className="text-xs text-red-700 bg-red-50 rounded px-2 py-1">{transitionError}</p>
          )}
          <div className="flex gap-2">
            <button
              onClick={async () => {
                if (!newPhaseName.trim() || !archiveName.trim()) return;
                setTransitionLoading(true);
                setTransitionError(null);
                try {
                  await api.performPhaseTransition({
                    archive_name: archiveName.trim(),
                    archive_label: slugify(archiveName.trim()),
                    archive_initial_balance: 1000,
                    new_phase_name: newPhaseName.trim(),
                    new_phase_label: slugify(newPhaseName.trim()),
                    new_initial_balance: 1000,
                    new_normalized_baseline: 1000,
                    stop_active_bots: false,
                    reset_account: true,
                  });
                  setTransitionResult(`Done. "${archiveName.trim()}" archived. "${newPhaseName.trim()}" is now active with a clean £1,000 baseline.`);
                  setShowTransitionDialog(false);
                  await Promise.all([mutateBots(), mutatePhases(), mutateActivePhase()]);
                } catch (err) {
                  setTransitionError(err instanceof Error ? err.message : "Transition failed");
                } finally {
                  setTransitionLoading(false);
                }
              }}
              disabled={transitionLoading || !newPhaseName.trim() || !archiveName.trim()}
              className="btn btn-primary text-xs disabled:opacity-50 flex items-center gap-2"
            >
              {transitionLoading ? <Loader2 size={12} className="animate-spin" /> : <Flag size={12} />}
              {isFirstUse ? "Initialize" : "Confirm Transition"}
            </button>
            <button
              onClick={() => setShowTransitionDialog(false)}
              className="btn btn-secondary text-xs"
              disabled={transitionLoading}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      </div>
        );
      })()}

      {/* View controls */}
      <div className="flex items-center justify-between gap-3 mb-3">
        <button
          onClick={() => setGroupBy(groupBy === "none" ? "strategy" : "none")}
          className={`text-xs px-3 py-1 rounded border ${groupBy === "strategy" ? "bg-primary/10 border-primary text-primary" : "border-gray-200"}`}
        >
          {groupBy === "strategy" ? "Grouped by Strategy" : "Group by Strategy"}
        </button>
        {botList.length > 0 && (
          <button
            onClick={() =>
              setPendingAction({
                kind: "delete-all",
                count: botList.length,
                trades: totalTradeCount,
              })
            }
            className="text-xs px-3 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50 transition-colors"
            aria-label={`Delete all ${botList.length} bots`}
          >
            Delete All Bots ({botList.length})
          </button>
        )}
      </div>

      {/* Bot List */}
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th className="text-left cursor-pointer select-none" onClick={() => handleSort("strategy")}>Strategy{sortArrow("strategy")}</th>
              <th className="text-left cursor-pointer select-none" onClick={() => handleSort("instrument")}>Instrument{sortArrow("instrument")}</th>
              <th className="text-left cursor-pointer select-none" onClick={() => handleSort("tf")}>TF{sortArrow("tf")}</th>
              <th className="text-left cursor-pointer select-none" onClick={() => handleSort("state")}>State{sortArrow("state")}</th>
              <th className="text-right cursor-pointer select-none" onClick={() => handleSort("bars")}>Bars{sortArrow("bars")}</th>
              <th className="text-right cursor-pointer select-none" onClick={() => handleSort("trades")}>Trades{sortArrow("trades")}</th>
              <th className="text-right cursor-pointer select-none" onClick={() => handleSort("pnl")}>PnL{sortArrow("pnl")}</th>
              <th className="text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {botList.length === 0 && (
              <tr>
                <td colSpan={8}>
                  <EmptyState
                    icon={<Bot size={36} strokeWidth={1.5} />}
                    title="No bots yet"
                    description={isIgDemo ? "Create a bot to start trading on your IG demo account. Add one manually above, or promote a top-scoring combo from Research." : "Bots monitor strategies in real time. Add one manually above, or promote a top-scoring combo from your Research shortlist."}
                  />
                  <div className="flex justify-center gap-3 pb-4">
                    <Link href="/research" className="btn btn-secondary text-sm">
                      <Search size={14} />
                      Go to Research
                    </Link>
                  </div>
                </td>
              </tr>
            )}
            {groupBy === "none" ? (
              sortedBotList.map((bot) => {
                const fleetBot = fleetBots.find((fb) => fb.bot_id === bot.bot_id);
                return (
                  <tr key={bot.bot_id} className={fleetBot?.is_stale ? "bg-amber-50/50" : ""}>
                    <td>
                      <span className="font-medium">{bot.strategy_id}</span>
                      <span className="block text-[10px] text-foreground-muted truncate max-w-[160px]">{strategyShortName(bot.strategy_id)}</span>
                    </td>
                    <td>{bot.instrument}</td>
                    <td className="text-foreground-muted">{bot.timeframe}</td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        <StatusBadge variant={STATE_VARIANT[bot.state] ?? "neutral"}>
                          {bot.state}
                        </StatusBadge>
                        {fleetBot?.is_stale && (
                          // P1-12: surface last-evaluated timestamp on hover
                          // so operators can tell 30s-stale from 3d-stale.
                          // Lucide icons don't accept `title`, so wrap in span.
                          <span
                            title={
                              fleetBot?.last_evaluated_bar
                                ? `Last evaluated ${new Date(fleetBot.last_evaluated_bar).toLocaleString()}`
                                : "Stale — last_evaluated_bar unknown"
                            }
                          >
                            <AlertTriangle
                              size={12}
                              className="text-amber-500"
                              aria-label="Stale — bot has not evaluated recently"
                            />
                          </span>
                        )}
                        {killSwitchActive &&
                          (bot.state === "monitoring" || bot.state === "position_open") && (
                            <span
                              className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-700 border border-red-200"
                              title="Kill switch is active — worker will not route orders"
                            >
                              <ShieldAlert size={10} /> halted
                            </span>
                          )}
                      </div>
                    </td>
                    <td className="text-right tabular-nums text-foreground-muted">{fleetBot?.bars_seen ?? 0}</td>
                    <td className="text-right tabular-nums">{fleetBot?.total_trades ?? 0}</td>
                    <td className={`text-right tabular-nums font-medium ${(fleetBot?.total_pnl ?? 0) >= 0 ? "text-primary" : "text-danger"}`}>
                      {(fleetBot?.total_pnl ?? 0) >= 0 ? "+" : ""}{sym}{(fleetBot?.total_pnl ?? 0).toFixed(2)}
                    </td>
                    <td className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Link
                          href={`/bots/${bot.bot_id}`}
                          className="btn-ghost text-xs px-2 py-1 rounded"
                          title="View bot detail"
                          aria-label={`View detail for bot ${bot.strategy_id} ${bot.instrument} ${bot.timeframe}`}
                        >
                          <ExternalLink size={11} />
                        </Link>
                        {bot.state === "stopped" && (
                          <button
                            onClick={() => handleRestart(bot.bot_id)}
                            disabled={!!actingBotId || killSwitchActive}
                            className="btn-ghost text-xs px-2 py-1 rounded text-primary disabled:opacity-40"
                            title={killSwitchActive ? "Kill switch is active — execution is halted" : "Continue from where this bot left off"}
                          >
                            {actingBotId === bot.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Restart"}
                          </button>
                        )}
                        {bot.state === "paused" && (
                          <button
                            onClick={() => handleResume(bot.bot_id)}
                            disabled={!!actingBotId || killSwitchActive}
                            className="btn-ghost text-xs px-2 py-1 rounded text-primary disabled:opacity-40"
                            title={killSwitchActive ? "Kill switch is active — execution is halted" : undefined}
                          >
                            {actingBotId === bot.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Resume"}
                          </button>
                        )}
                        {bot.state === "monitoring" && (
                          <button
                            onClick={() => handlePause(bot.bot_id)}
                            disabled={!!actingBotId}
                            className="btn-ghost text-xs px-2 py-1 rounded disabled:opacity-40"
                          >
                            {actingBotId === bot.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Pause"}
                          </button>
                        )}
                        {bot.state !== "stopped" && (
                          <button
                            onClick={() =>
                              setPendingAction({
                                kind: "stop-bot",
                                botId: bot.bot_id,
                                label: `${bot.strategy_id} ${bot.instrument} ${bot.timeframe}`,
                              })
                            }
                            disabled={!!actingBotId}
                            className="text-xs px-2 py-1 rounded text-danger hover:bg-red-50 transition-colors disabled:opacity-40"
                            aria-label={`Stop bot ${bot.strategy_id} ${bot.instrument} ${bot.timeframe}`}
                          >
                            {actingBotId === bot.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Stop"}
                          </button>
                        )}
                        <button
                          onClick={() =>
                            setPendingAction({
                              kind: "delete-bot",
                              botId: bot.bot_id,
                              label: `${bot.strategy_id} ${bot.instrument} ${bot.timeframe}`,
                            })
                          }
                          disabled={!!actingBotId}
                          className="text-xs px-2 py-1 rounded text-foreground-muted hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-40"
                          title="Delete bot and trade history"
                          aria-label={`Delete bot ${bot.strategy_id} ${bot.instrument} ${bot.timeframe}`}
                        >
                          {actingBotId === bot.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Delete"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            ) : (
              Object.entries(groupedBots).map(([sid, bots]) => (
                <Fragment key={sid}>
                  <tr className="bg-background-muted">
                    <td colSpan={8} className="text-sm font-semibold py-2">
                      {sid}
                      <span className="text-foreground-muted font-normal ml-2">
                        ({bots.length} bots &middot; {strategyGroups[sid]?.running ?? 0} active &middot;
                        <span className={`ml-1 ${(strategyGroups[sid]?.pnl ?? 0) >= 0 ? "text-primary" : "text-danger"}`}>
                          {(strategyGroups[sid]?.pnl ?? 0) >= 0 ? "+" : ""}{sym}{(strategyGroups[sid]?.pnl ?? 0).toFixed(2)}
                        </span>
                        )
                      </span>
                    </td>
                  </tr>
                  {bots.map((b) => (
                    <tr key={b.bot_id} className={b.is_stale ? "bg-amber-50/50" : ""}>
                      <td className="pl-6" title={strategyShortName(b.strategy_id)}>
                        <span className="font-medium">{b.strategy_id}</span>
                      </td>
                      <td>{b.instrument}</td>
                      <td className="text-foreground-muted">{b.timeframe}</td>
                      <td>
                        <div className="flex items-center gap-1.5">
                          <StatusBadge variant={STATE_VARIANT[b.state] ?? "neutral"}>
                            {b.state}
                          </StatusBadge>
                          {b.is_stale && (
                            <span
                              title={
                                b.last_evaluated_bar
                                  ? `Last evaluated ${new Date(b.last_evaluated_bar).toLocaleString()}`
                                  : "Stale — last_evaluated_bar unknown"
                              }
                            >
                              <AlertTriangle
                                size={12}
                                className="text-amber-500"
                                aria-label="Stale — bot has not evaluated recently"
                              />
                            </span>
                          )}
                          {killSwitchActive &&
                            (b.state === "monitoring" || b.state === "position_open") && (
                              <span
                                className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-red-50 text-red-700 border border-red-200"
                                title="Kill switch is active — worker will not route orders"
                              >
                                <ShieldAlert size={10} /> halted
                              </span>
                            )}
                        </div>
                      </td>
                      <td className="text-right tabular-nums text-foreground-muted">{b.bars_seen}</td>
                      <td className="text-right tabular-nums">{b.total_trades}</td>
                      <td className={`text-right tabular-nums font-medium ${b.total_pnl >= 0 ? "text-primary" : "text-danger"}`}>
                        {b.total_pnl >= 0 ? "+" : ""}{sym}{b.total_pnl.toFixed(2)}
                      </td>
                      <td className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Link
                            href={`/bots/${b.bot_id}`}
                            className="btn-ghost text-xs px-2 py-1 rounded"
                            title="View bot detail"
                            aria-label={`View detail for bot ${b.strategy_id} ${b.instrument} ${b.timeframe}`}
                          >
                            <ExternalLink size={11} />
                          </Link>
                          {b.state === "stopped" && (
                            <button
                              onClick={() => handleRestart(b.bot_id)}
                              disabled={!!actingBotId || killSwitchActive}
                              className="btn-ghost text-xs px-2 py-1 rounded text-primary disabled:opacity-40"
                              title={killSwitchActive ? "Kill switch is active — execution is halted" : "Continue from where this bot left off"}
                            >
                              {actingBotId === b.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Restart"}
                            </button>
                          )}
                          {b.state === "paused" && (
                            <button
                              onClick={() => handleResume(b.bot_id)}
                              disabled={!!actingBotId || killSwitchActive}
                              className="btn-ghost text-xs px-2 py-1 rounded text-primary disabled:opacity-40"
                              title={killSwitchActive ? "Kill switch is active — execution is halted" : undefined}
                            >
                              {actingBotId === b.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Resume"}
                            </button>
                          )}
                          {b.state === "monitoring" && (
                            <button
                              onClick={() => handlePause(b.bot_id)}
                              disabled={!!actingBotId}
                              className="btn-ghost text-xs px-2 py-1 rounded disabled:opacity-40"
                            >
                              {actingBotId === b.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Pause"}
                            </button>
                          )}
                          {b.state !== "stopped" && (
                            <button
                              onClick={() =>
                                setPendingAction({
                                  kind: "stop-bot",
                                  botId: b.bot_id,
                                  label: `${b.strategy_id} ${b.instrument} ${b.timeframe}`,
                                })
                              }
                              disabled={!!actingBotId}
                              className="text-xs px-2 py-1 rounded text-danger hover:bg-red-50 transition-colors disabled:opacity-40"
                              aria-label={`Stop bot ${b.strategy_id} ${b.instrument} ${b.timeframe}`}
                            >
                              {actingBotId === b.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Stop"}
                            </button>
                          )}
                          <button
                            onClick={() =>
                              setPendingAction({
                                kind: "delete-bot",
                                botId: b.bot_id,
                                label: `${b.strategy_id} ${b.instrument} ${b.timeframe}`,
                              })
                            }
                            disabled={!!actingBotId}
                            className="text-xs px-2 py-1 rounded text-foreground-muted hover:text-red-600 hover:bg-red-50 transition-colors disabled:opacity-40"
                            title="Delete bot and trade history"
                            aria-label={`Delete bot ${b.strategy_id} ${b.instrument} ${b.timeframe}`}
                          >
                            {actingBotId === b.bot_id ? <Loader2 size={12} className="animate-spin inline" /> : "Delete"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* P0-1..5: in-page confirmation modal for destructive / fleet-wide
          actions. Mirrors the KillSwitchModal idiom so the workstation
          feels consistent across high-impact operator controls. */}
      {pendingAction && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="bots-confirm-title"
          className="fixed inset-0 z-[10000] flex items-center justify-center"
        >
          <div
            className="absolute inset-0 bg-black/40"
            onClick={confirmBusy ? undefined : () => setPendingAction(null)}
          />
          <div className="relative bg-background-card rounded-2xl shadow-2xl border border-gray-200 w-full max-w-md mx-4 p-6">
            <button
              type="button"
              onClick={() => setPendingAction(null)}
              disabled={confirmBusy}
              aria-label="Close dialog"
              className="absolute top-3 right-3 text-foreground-muted hover:text-foreground p-1 rounded-md hover:bg-background-muted transition-colors disabled:opacity-50"
            >
              <X size={16} />
            </button>

            <div className="flex items-start gap-3 mb-4">
              <div className="w-10 h-10 rounded-xl bg-red-50 flex items-center justify-center shrink-0">
                <AlertTriangle size={20} className="text-danger" />
              </div>
              <div>
                <h2 id="bots-confirm-title" className="text-lg font-bold tracking-tight">
                  {pendingAction.kind === "delete-bot" && "Delete bot?"}
                  {pendingAction.kind === "stop-bot" && "Stop bot?"}
                  {pendingAction.kind === "delete-all" &&
                    `Delete all ${pendingAction.count} bots?`}
                  {pendingAction.kind === "restart-all" &&
                    `Restart ${pendingAction.count} stopped bots?`}
                  {pendingAction.kind === "smart-deploy" &&
                    "Deploy top research combos?"}
                </h2>
              </div>
            </div>

            <div className="text-sm text-foreground space-y-2 mb-5">
              {pendingAction.kind === "delete-bot" && (
                <>
                  <p>
                    Removes <strong>{pendingAction.label}</strong> and{" "}
                    <strong>all of its trade history</strong>. This cannot be
                    undone.
                  </p>
                  <p className="text-xs text-foreground-muted">
                    If you only want to halt signal evaluation without losing
                    the history, use Stop instead.
                  </p>
                </>
              )}
              {pendingAction.kind === "stop-bot" && (
                <>
                  <p>
                    Stops <strong>{pendingAction.label}</strong>. Signal
                    evaluation halts and any open position is left under its
                    own stop / target rules.
                  </p>
                  <p className="text-xs text-foreground-muted">
                    Reversible — use Restart to put the bot back into
                    monitoring mode.
                  </p>
                </>
              )}
              {pendingAction.kind === "delete-all" && (
                <>
                  <p>
                    Removes every bot in the current evaluation phase along
                    with <strong>{pendingAction.trades.toLocaleString()}</strong>{" "}
                    trade record{pendingAction.trades === 1 ? "" : "s"}. This
                    cannot be undone.
                  </p>
                  <p className="text-xs text-foreground-muted">
                    Archived phase trade history (if any) is unaffected.
                  </p>
                </>
              )}
              {pendingAction.kind === "restart-all" && (
                <p>
                  Puts every stopped bot back into{" "}
                  <strong>monitoring</strong> state. Each bot resumes signal
                  evaluation on the next closed candle.
                </p>
              )}
              {pendingAction.kind === "smart-deploy" && (
                <>
                  <p>
                    Creates up to <strong>5 bots</strong> from your top research
                    results (composite score ≥ 0.30). Combos that already have
                    bots are skipped.
                  </p>
                  <p className="text-xs text-foreground-muted">
                    New bots start in <strong>monitoring</strong> state with
                    default risk settings. You can edit risk per bot from the
                    detail page afterwards.
                  </p>
                </>
              )}
            </div>

            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingAction(null)}
                disabled={confirmBusy}
                className="btn btn-secondary text-sm disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={confirmPendingAction}
                disabled={confirmBusy}
                className={`text-sm px-4 py-2 rounded-lg font-medium transition-colors disabled:opacity-50 ${
                  pendingAction.kind === "restart-all" ||
                  pendingAction.kind === "smart-deploy"
                    ? "bg-primary text-white hover:bg-primary/90"
                    : "bg-red-600 text-white hover:bg-red-700"
                }`}
              >
                {confirmBusy
                  ? "Working..."
                  : pendingAction.kind === "delete-bot"
                    ? "Delete bot"
                    : pendingAction.kind === "stop-bot"
                      ? "Stop bot"
                      : pendingAction.kind === "delete-all"
                        ? `Delete ${pendingAction.count} bots`
                        : pendingAction.kind === "restart-all"
                          ? `Restart ${pendingAction.count}`
                          : "Deploy"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
