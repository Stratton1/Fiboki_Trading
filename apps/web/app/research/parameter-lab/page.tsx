"use client";

import { parseAsString, useQueryState } from "nuqs";
import { UrlState } from "@/components/UrlState";
import { useApi } from "@/lib/query";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { FigureValue } from "@/components/FigureValue";
import {
  Card,
  PageHead,
  SourceBadge,
  TableWrap,
  Tile,
} from "@/components/primitives";
import type { Envelope, Page, ParameterLabView, StrategyRow } from "@/lib/types";

/**
 * RESEARCH · Parameter Lab.
 *
 * The search-space size is shown as a first-class figure, with the deflation
 * warning the API computes from it. Sweeping a grid is a multiple-testing
 * burden, and an operator turning five knobs should be told what that does to
 * every later Sharpe, not discover it in a validation report.
 *
 * The selected strategy is URL state (`?strategy=<id>`).
 */
const strategyParser = parseAsString.withOptions({ history: "replace" });

export default function ParameterLabPage() {
  return (
    <UrlState fallback={<ParameterLab selected={null} onSelect={() => undefined} />}>
      <ParameterLabFromUrl />
    </UrlState>
  );
}

function ParameterLabFromUrl() {
  const [selected, setSelected] = useQueryState("strategy", strategyParser);
  return <ParameterLab selected={selected} onSelect={(id) => void setSelected(id)} />;
}

function ParameterLab({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const strategies = useApi<Page<StrategyRow>>("/api/research/strategies");
  const chosen =
    selected ??
    (strategies.status === "success" ? (strategies.data.items[0]?.strategy_id ?? null) : null);
  const lab = useApi<Envelope<ParameterLabView>>(
    chosen ? `/api/research/parameter-lab/${encodeURIComponent(chosen)}` : null,
  );

  return (
    <>
      <PageHead
        title="Parameter Lab"
        intro="Declared parameter domains, and the size of the search they imply. Searching more is not finding more."
      />

      <AsyncBoundary state={strategies} label="strategies" onRetry={strategies.reload}>
        {(page) => (
          <div className="row mb-3.5">
            <label className="m-0" htmlFor="strategy-select">
              Strategy
            </label>
            <select
              id="strategy-select"
              data-testid="strategy-select"
              value={chosen ?? ""}
              onChange={(e) => onSelect(e.target.value)}
            >
              {page.items.map((row) => (
                <option key={row.strategy_id} value={row.strategy_id}>
                  {row.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </AsyncBoundary>

      <AsyncBoundary state={lab} label="the parameter space" onRetry={lab.reload}>
        {(envelope) => (
          <>
            <SourceBadge source={envelope.source} />
            <div className="tiles">
              <Tile
                label="Search space"
                figure={envelope.data.search_space_size}
                help="Every combination is a trial."
              />
            </div>
            <div className="caveat caveat--warning" data-testid="deflation-warning">
              {envelope.data.deflation_warning}
            </div>
            <Card title="Declared domains">
              {envelope.data.parameters.length === 0 ? (
                <div className="state state--empty">
                  <div className="state__body">
                    This strategy declares no numeric parameter ranges.
                  </div>
                </div>
              ) : (
                <TableWrap>
                  <table>
                    <thead>
                      <tr>
                        <th>Parameter</th>
                        <th>Default</th>
                        <th>Min</th>
                        <th>Max</th>
                        <th>Step</th>
                        <th>Description</th>
                      </tr>
                    </thead>
                    <tbody>
                      {envelope.data.parameters.map((row) => (
                        <tr key={row.name}>
                          <td className="mono">{row.name}</td>
                          <td>
                            <FigureValue figure={row.current} showChip={false} />
                          </td>
                          <td>
                            <FigureValue figure={row.minimum} showChip={false} />
                          </td>
                          <td>
                            <FigureValue figure={row.maximum} showChip={false} />
                          </td>
                          <td>
                            <FigureValue figure={row.step} showChip={false} />
                          </td>
                          <td className="wrap">{row.description}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              )}
            </Card>
          </>
        )}
      </AsyncBoundary>
    </>
  );
}
