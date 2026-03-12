"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { StatusBadge } from "@/components/StatusBadge";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Gauge,
  Layers,
  PieChart,
  Shield,
} from "lucide-react";

function RiskGauge({
  label,
  value,
  softLimit,
  hardLimit,
  unit = "%",
}: {
  label: string;
  value: number;
  softLimit: number;
  hardLimit: number;
  unit?: string;
}) {
  const pct = hardLimit > 0 ? Math.min((value / hardLimit) * 100, 100) : 0;
  const variant =
    value >= hardLimit ? "error" : value >= softLimit ? "warn" : "ok";
  const barColor =
    variant === "error"
      ? "bg-red-500"
      : variant === "warn"
      ? "bg-amber-500"
      : "bg-emerald-500";

  return (
    <div className="bg-background-muted rounded-lg p-3">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-foreground-muted">{label}</span>
        <StatusBadge variant={variant}>
          {value.toFixed(1)}
          {unit}
        </StatusBadge>
      </div>
      <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex justify-between mt-1">
        <span className="text-[10px] text-foreground-muted">
          Soft: {softLimit}
          {unit}
        </span>
        <span className="text-[10px] text-foreground-muted">
          Hard: {hardLimit}
          {unit}
        </span>
      </div>
    </div>
  );
}

function TradeCapacityGauge({
  current,
  max,
}: {
  current: number;
  max: number;
}) {
  const pct = max > 0 ? Math.min((current / max) * 100, 100) : 0;
  const variant = pct >= 100 ? "error" : pct >= 75 ? "warn" : "ok";
  const barColor =
    variant === "error"
      ? "bg-red-500"
      : variant === "warn"
      ? "bg-amber-500"
      : "bg-emerald-500";

  return (
    <div className="bg-background-muted rounded-lg p-3">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-foreground-muted">Open Trades</span>
        <span className="text-sm font-bold">
          {current}
          <span className="text-foreground-muted font-normal"> / {max}</span>
        </span>
      </div>
      <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function ExposurePage() {
  const { data } = useSWR("/paper/exposure", () => api.exposure(), {
    refreshInterval: 10000,
  });

  if (!data) {
    return (
      <div className="max-w-5xl">
        <PageHeader
          title="Exposure Dashboard"
          subtitle="Loading portfolio exposure..."
        />
      </div>
    );
  }

  const {
    instrument_exposure,
    asset_class_exposure,
    direction_balance,
    active_positions,
    concentration_warnings,
    risk_utilization,
  } = data;

  const instruments = Object.entries(instrument_exposure);
  const assetClasses = Object.entries(asset_class_exposure);
  const hasExposure = instruments.length > 0;

  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Exposure Dashboard"
        subtitle={`${active_positions} open position${active_positions !== 1 ? "s" : ""} across ${instruments.length} instrument${instruments.length !== 1 ? "s" : ""}`}
      />

      {/* Risk Utilization Gauges */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-4">
          <Shield size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">Risk Limits</p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <TradeCapacityGauge
            current={risk_utilization.open_trades}
            max={risk_utilization.max_open_trades}
          />
          <RiskGauge
            label="Daily Drawdown"
            value={risk_utilization.daily_dd_pct}
            softLimit={risk_utilization.daily_soft_stop_pct}
            hardLimit={risk_utilization.daily_hard_stop_pct}
          />
          <RiskGauge
            label="Weekly Drawdown"
            value={risk_utilization.weekly_dd_pct}
            softLimit={risk_utilization.weekly_soft_stop_pct}
            hardLimit={risk_utilization.weekly_hard_stop_pct}
          />
        </div>
      </div>

      {/* Direction Balance */}
      <div className="grid grid-cols-2 gap-3 mb-6">
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
              Long
            </span>
            <ArrowUp size={14} className="text-emerald-500" />
          </div>
          <p className="text-2xl font-bold tracking-tight text-emerald-600">
            {direction_balance.long}
          </p>
        </div>
        <div className="stat-card">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium uppercase tracking-wide text-foreground-muted">
              Short
            </span>
            <ArrowDown size={14} className="text-red-500" />
          </div>
          <p className="text-2xl font-bold tracking-tight text-red-600">
            {direction_balance.short}
          </p>
        </div>
      </div>

      {/* Concentration Warnings */}
      {concentration_warnings.length > 0 && (
        <div className="card mb-6 border-l-4 border-l-amber-500">
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle size={14} className="text-amber-500" />
            <p className="text-sm font-semibold text-amber-700">
              Concentration Warning
            </p>
          </div>
          <div className="space-y-1">
            {concentration_warnings.map((w) => (
              <p key={w.instrument} className="text-xs text-foreground-muted">
                <span className="font-medium text-foreground">
                  {w.instrument}
                </span>{" "}
                has {w.bot_count} bots trading it simultaneously
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Instrument Exposure Table */}
      <div className="card mb-6">
        <div className="flex items-center gap-2 mb-4">
          <Layers size={14} className="text-foreground-muted" />
          <p className="section-label !mb-0">By Instrument</p>
        </div>
        {!hasExposure ? (
          <EmptyState
            icon={<Gauge size={36} strokeWidth={1.5} />}
            title="No open positions"
            description="Exposure data appears when paper bots have open positions."
          />
        ) : (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th className="text-left">Instrument</th>
                  <th className="text-right">Long</th>
                  <th className="text-right">Short</th>
                  <th className="text-right">Net</th>
                  <th className="text-right">Bots</th>
                </tr>
              </thead>
              <tbody>
                {instruments.map(([inst, exp]) => (
                  <tr key={inst}>
                    <td className="font-medium">{inst}</td>
                    <td className="text-right tabular-nums text-emerald-600">
                      {exp.long > 0 ? exp.long : "—"}
                    </td>
                    <td className="text-right tabular-nums text-red-600">
                      {exp.short > 0 ? exp.short : "—"}
                    </td>
                    <td
                      className={`text-right tabular-nums font-medium ${
                        exp.net > 0
                          ? "text-emerald-600"
                          : exp.net < 0
                          ? "text-red-600"
                          : "text-foreground-muted"
                      }`}
                    >
                      {exp.net > 0 ? `+${exp.net}` : exp.net}
                    </td>
                    <td className="text-right tabular-nums">
                      {exp.bot_count}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Asset Class Breakdown */}
      {assetClasses.length > 0 && (
        <div className="card">
          <div className="flex items-center gap-2 mb-4">
            <PieChart size={14} className="text-foreground-muted" />
            <p className="section-label !mb-0">By Asset Class</p>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {assetClasses.map(([ac, exp]) => (
              <div
                key={ac}
                className="border border-gray-200 rounded-lg p-3"
              >
                <p className="text-sm font-medium truncate">{ac}</p>
                <div className="flex items-baseline gap-3 mt-1">
                  <span className="text-xs text-emerald-600">
                    {exp.long}L
                  </span>
                  <span className="text-xs text-red-600">{exp.short}S</span>
                  <span className="text-xs text-foreground-muted">
                    {exp.instruments} instr
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
