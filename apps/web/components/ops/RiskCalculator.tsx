"use client";

import { useId, useState, type FormEvent } from "react";
import { riskCalculatorPath, type RiskCalculatorView } from "@/lib/ops";
import { useApi } from "@/lib/query";
import type { CandidateRow, Envelope, Page } from "@/lib/types";
import { AsyncBoundary } from "../AsyncBoundary";
import { FigureValue } from "../FigureValue";
import { Card, CaveatList, SourceBadge } from "../primitives";
import { Stat } from "../Stat";
import { Button } from "../ui/Button";

/**
 * Risk of ruin and position size (Wave 3O), under /trading/risk.
 *
 * The form collects INPUTS only. GET /api/trading/risk/calculator sizes each
 * instrument with the platform's own size_trade (fixed_fractional_v2, the
 * leverage cap) and runs the seeded Monte Carlo risk of ruin over a strategy's
 * stored backtest R multiples or the operator's own list. Every number shown
 * comes back from the API; nothing is computed here.
 */
const DEFAULTS = {
  accountSize: "100000",
  accountCcy: "USD",
  riskPct: "0.5",
  ruinPct: "30",
  strategyId: "donchian_breakout_atr",
  rMultiples: "",
  entries: "EURUSD:1.10:20, GBPUSD:1.27:25",
};

export function RiskCalculator() {
  const [form, setForm] = useState(DEFAULTS);
  const [path, setPath] = useState<string | null>(null);
  const strategies = useApi<Page<CandidateRow>>("/api/trading/candidates");
  const listId = useId();
  const result = useApi<Envelope<RiskCalculatorView>>(path);
  const set = (key: keyof typeof DEFAULTS) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setPath(riskCalculatorPath(form));
  };
  return (
    <Card title="Risk of ruin and position size">
      <form className="ops-form" onSubmit={submit} data-testid="risk-calc-form" aria-label="Risk calculator inputs">
        <label>
          Account size
          <input inputMode="decimal" value={form.accountSize} onChange={set("accountSize")} data-testid="risk-calc-account" />
        </label>
        <label>
          Currency
          <input value={form.accountCcy} onChange={set("accountCcy")} maxLength={3} />
        </label>
        <label>
          Risk per trade (%)
          <input inputMode="decimal" value={form.riskPct} onChange={set("riskPct")} data-testid="risk-calc-risk" />
        </label>
        <label>
          Ruin = drawdown from peak (%)
          <input inputMode="decimal" value={form.ruinPct} onChange={set("ruinPct")} />
        </label>
        <label>
          Strategy (its stored backtest)
          <input list={listId} value={form.strategyId} onChange={set("strategyId")} data-testid="risk-calc-strategy" />
          <datalist id={listId}>
            {strategies.status === "success" ? strategies.data.items.map((s) => <option key={s.strategy_id} value={s.strategy_id} />) : null}
          </datalist>
        </label>
        <label>
          …or R multiples (comma-separated)
          <input value={form.rMultiples} onChange={set("rMultiples")} placeholder="1.5,-1,-1,2.2" />
        </label>
        <label className="ops-form__wide">
          Instruments SYMBOL:ENTRY:STOP_PIPS[:FX]
          <input value={form.entries} onChange={set("entries")} data-testid="risk-calc-entries" />
        </label>
        <Button type="submit" size="sm" variant="primary" data-testid="risk-calc-run">
          Compute
        </Button>
      </form>
      {path === null ? (
        <p className="muted ops-hint">Enter inputs and press Compute (Enter in any field). The platform computes; nothing is stored.</p>
      ) : (
        <AsyncBoundary state={result} label="the risk calculation" onRetry={result.reload}>
          {(env) => <CalcResult env={env} />}
        </AsyncBoundary>
      )}
    </Card>
  );
}

function CalcResult({ env }: { env: Envelope<RiskCalculatorView> }) {
  const v = env.data;
  return (
    <div data-testid="risk-calc-result">
      <SourceBadge source={env.source} />
      <CaveatList caveats={env.caveats} />
      {v.ruin ? (
        <>
          <div className="tiles" data-testid="risk-calc-ruin">
            <Stat label={`P(ruin: ${v.ruin.ruin_drawdown_pct}% DD)`} figure={v.ruin.probability_pct} />
            <Stat label="Max DD p50" figure={v.ruin.max_drawdown_p50} />
            <Stat label="Max DD p95" figure={v.ruin.max_drawdown_p95} />
            <Stat label="Max DD p99" figure={v.ruin.max_drawdown_p99} />
            <Stat label="Final ×equity p50" figure={v.ruin.final_multiple_p50} />
            <Stat label="Mean R" figure={v.ruin.mean_r} signed />
          </div>
          <p className="ops-hint">
            {v.ruin.n_paths} seeded paths (seed {v.ruin.seed}), {v.ruin.horizon_trades} trades each, block {v.ruin.block_length}; from{" "}
            {v.ruin.source}.
          </p>
        </>
      ) : (
        <p className="muted" data-testid="risk-calc-ruin-absent">
          No risk of ruin: {v.ruin_absent}
        </p>
      )}
      {v.sizes.length > 0 ? (
        <div className="table-wrap">
          <table className="dense" data-testid="risk-calc-sizes">
            <thead>
              <tr>
                <th scope="col">Instrument</th>
                <th scope="col" className="num">
                  Entry
                </th>
                <th scope="col" className="num">
                  Stop
                </th>
                <th scope="col" className="num">
                  Size
                </th>
                <th scope="col" className="num">
                  Risk
                </th>
                <th scope="col" className="num">
                  Notional
                </th>
                <th scope="col" className="num">
                  Lev. cap
                </th>
                <th scope="col">Note</th>
              </tr>
            </thead>
            <tbody>
              {v.sizes.map((s) => (
                <tr key={`${s.instrument}-${s.stop_pips}`} data-testid="risk-calc-size-row" data-instrument={s.instrument}>
                  <th scope="row" className="mono">
                    {s.instrument}
                  </th>
                  <td className="num">
                    <FigureValue figure={s.entry} showChip={false} />
                  </td>
                  <td className="num">
                    <FigureValue figure={s.stop} showChip={false} missingLabel="—" /> <span className="muted">({s.stop_pips} pips)</span>
                  </td>
                  <td className="num">
                    <FigureValue figure={s.size} showChip={false} missingLabel="—" />
                  </td>
                  <td className="num">
                    <FigureValue figure={s.risk_amount} showChip={false} missingLabel="—" />
                  </td>
                  <td className="num">
                    <FigureValue figure={s.notional} showChip={false} missingLabel="—" />
                  </td>
                  <td className="num">
                    <FigureValue figure={s.leverage_cap} showChip={false} missingLabel="—" />
                  </td>
                  <td className="wrap">
                    {s.rejection ? <span className="badge badge--degraded">{s.rejection}</span> : null}
                    {s.leverage_binding ? <span className="badge badge--degraded">LEVERAGE CAP BINDS</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="ops-hint">{v.method}</p>
    </div>
  );
}

export default RiskCalculator;
