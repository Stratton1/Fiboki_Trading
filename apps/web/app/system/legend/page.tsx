"use client";

import { parseAsStringLiteral, useQueryState } from "nuqs";
import { lazy, Suspense } from "react";
import { PageHead } from "@/components/primitives";
import { UrlState } from "@/components/UrlState";
import { ProvenanceChip, ProvenanceLabelChip } from "@/components/ProvenanceChip";
import { TITLE_PREFIX } from "@/components/shell/Mode";
import { StatusPill } from "@/components/ui/StatusPill";
import { Tab, TabsList, TabsPanel, TabsRoot } from "@/components/ui/Tabs";
import { PROVENANCE_EXECUTED, PROVENANCE_HELP, PROVENANCE_LABEL } from "@/lib/format";
import { PROVENANCES } from "@/lib/types";

const LegendControls = lazy(() => import("./LegendControls"));

/**
 * SYSTEM · Legend.
 *
 * What every colour, shape and frame on this workstation means, drawn by the
 * same components the screens use. It carries no figures: every number in the
 * product comes from the platform, and a legend has none to show.
 */

const MODES = ["backtest", "paper", "shadow", "demo", "live", "unknown"] as const;

const MODE_MEANING: Record<(typeof MODES)[number], { frame: string; banner: string }> = {
  backtest: { frame: "No frame", banner: "Slate. Nothing can place an order." },
  paper: { frame: "1 px cyan", banner: "Cyan. Simulated fills." },
  shadow: { frame: "2 px dashed violet", banner: "Violet. Mirrored, not sent." },
  demo: { frame: "3 px amber, hatched banner", banner: "Amber. Real orders to a demo venue." },
  live: {
    frame: "4 px magenta",
    banner: "Inverted magenta, REAL MONEY, operator name, kill switch always visible.",
  },
  unknown: {
    frame: "2 px grey stripes",
    banner: "MODE UNKNOWN. The platform has never answered; every mutating control is disabled.",
  },
};

const COLOUR_TOKENS: { group: string; tokens: { name: string; swatch: string }[] }[] = [
  {
    group: "Surfaces",
    tokens: [
      { name: "--bg-canvas", swatch: "bg-[var(--bg-canvas)]" },
      { name: "--bg-sunken", swatch: "bg-[var(--bg-sunken)]" },
      { name: "--bg-surface", swatch: "bg-[var(--bg-surface)]" },
      { name: "--bg-raised", swatch: "bg-[var(--bg-raised)]" },
      { name: "--bg-overlay", swatch: "bg-[var(--bg-overlay)]" },
    ],
  },
  {
    group: "Text and lines",
    tokens: [
      { name: "--fg", swatch: "bg-[var(--fg)]" },
      { name: "--fg-muted", swatch: "bg-[var(--fg-muted)]" },
      { name: "--fg-subtle", swatch: "bg-[var(--fg-subtle)]" },
      { name: "--border", swatch: "bg-[var(--border)]" },
      { name: "--border-control", swatch: "bg-[var(--border-control)]" },
      { name: "--accent", swatch: "bg-[var(--accent)]" },
    ],
  },
  {
    group: "P&L direction",
    tokens: [
      { name: "--pnl-up", swatch: "bg-[var(--pnl-up)]" },
      { name: "--pnl-down", swatch: "bg-[var(--pnl-down)]" },
    ],
  },
  {
    group: "Health",
    tokens: [
      { name: "--ok", swatch: "bg-[var(--ok)]" },
      { name: "--warn", swatch: "bg-[var(--warn)]" },
      { name: "--critical", swatch: "bg-[var(--critical)]" },
      { name: "--unknown", swatch: "bg-[var(--unknown)]" },
    ],
  },
  {
    group: "Execution mode",
    tokens: [
      { name: "--mode-backtest", swatch: "bg-[var(--mode-backtest)]" },
      { name: "--mode-paper", swatch: "bg-[var(--mode-paper)]" },
      { name: "--mode-shadow", swatch: "bg-[var(--mode-shadow)]" },
      { name: "--mode-demo", swatch: "bg-[var(--mode-demo)]" },
      { name: "--mode-live", swatch: "bg-[var(--mode-live)]" },
    ],
  },
  {
    group: "Chart series",
    tokens: [
      { name: "--series-1", swatch: "bg-[var(--series-1)]" },
      { name: "--series-2", swatch: "bg-[var(--series-2)]" },
      { name: "--series-3", swatch: "bg-[var(--series-3)]" },
      { name: "--series-4", swatch: "bg-[var(--series-4)]" },
      { name: "--series-5", swatch: "bg-[var(--series-5)]" },
      { name: "--series-6", swatch: "bg-[var(--series-6)]" },
    ],
  },
];

const TYPE_SCALE = [
  { cls: "text-2xs", name: "2xs 10.5/14", use: "chip labels" },
  { cls: "text-xs", name: "xs 11.5/16", use: "compact grid, captions" },
  { cls: "text-sm", name: "sm 12.5/18", use: "regular grid, labels" },
  { cls: "text-base", name: "base 13.5/20", use: "body" },
  { cls: "text-md", name: "md 15/22", use: "panel titles" },
  { cls: "text-lg", name: "lg 18/26", use: "page titles" },
  { cls: "text-xl", name: "xl 24/30", use: "stat values" },
  { cls: "text-2xl", name: "2xl 32/38", use: "overview hero" },
];

export default function LegendPage() {
  return (
    <>
      <PageHead
        title="Legend"
        intro="What each colour, shape and frame on this workstation means. Colour carries four meanings only (execution mode, provenance, P&L direction, health) and each also has a shape, glyph or word, so nothing depends on colour alone."
      />
      <UrlState fallback={<LegendTabs tab="meanings" onTab={() => undefined} />}>
        <LegendTabsFromUrl />
      </UrlState>
    </>
  );
}

function Meanings() {
  return (
    <div className="stack">
      <section className="card" data-testid="legend-modes">
        <h2 className="card__title">Execution mode: where an order would go</h2>
        <p className="muted mb-3">
          A property of the whole shell: the frame around the window, the banner, the favicon and
          the tab title all change together.
        </p>
        <div className="legend-grid">
          {MODES.map((mode) => (
            <div
              key={mode}
              className="legend-frame"
              data-mode={mode}
              data-testid={`legend-mode-${mode}`}
            >
              <div className="mode-banner static min-h-0 rounded-sm px-2 py-1" data-mode={mode}>
                <span className="mode-banner__mode">
                  {mode === "unknown" ? "MODE UNKNOWN" : mode.toUpperCase()}
                </span>
                {mode === "live" ? <span className="mode-banner__money">REAL MONEY</span> : null}
              </div>
              <span className="text-xs text-fg-muted">
                <strong className="text-fg">Frame:</strong> {MODE_MEANING[mode].frame}
              </span>
              <span className="text-xs text-fg-muted">{MODE_MEANING[mode].banner}</span>
              <span className="text-xs text-fg-subtle">
                Tab title: <span className="mono">{TITLE_PREFIX[mode]} Fiboki</span>
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="card" data-testid="legend-provenance">
        <h2 className="card__title">Provenance: where a number came from</h2>
        <p className="muted mb-3">
          A property of each figure. Hollow chips are simulated evidence, told apart by outline
          (dashed, solid, double); filled chips are executions, in their mode&apos;s colour.
          Readable in greyscale.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Chip</th>
                <th>Provenance</th>
                <th>Kind</th>
                <th>Meaning</th>
              </tr>
            </thead>
            <tbody>
              {PROVENANCES.map((p) => (
                <tr key={p}>
                  <td>
                    <ProvenanceChip provenance={p} />
                  </td>
                  <td>{PROVENANCE_LABEL[p]}</td>
                  <td>{PROVENANCE_EXECUTED[p] ? "executed (filled)" : "simulated (hollow)"}</td>
                  <td className="wrap">{PROVENANCE_HELP[p]}</td>
                </tr>
              ))}
              <tr>
                <td>
                  <ProvenanceLabelChip
                    label={{
                      kind: "mixed",
                      counts: PROVENANCES.filter((p) => p === "backtest" || p === "paper").map(
                        (p) => ({
                          provenance: p,
                          count: 1,
                        }),
                      ),
                    }}
                  />
                </td>
                <td>MIXED</td>
                <td>aggregate</td>
                <td className="wrap">
                  An aggregate over more than one provenance. Its popover lists the count of each;
                  it is never labelled from its first row.
                </td>
              </tr>
              <tr>
                <td>
                  <ProvenanceLabelChip
                    label={{
                      kind: "unlabelled",
                      reason: "Legend example: no value carried a provenance.",
                    }}
                  />
                </td>
                <td>unlabelled source</td>
                <td>absent</td>
                <td className="wrap">
                  The platform supplied nothing to label it with, and the screen says so rather than
                  guess.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section className="card" data-testid="legend-pnl">
        <h2 className="card__title">P&amp;L direction and health</h2>
        <div className="legend-grid">
          <div className="stack">
            <span className="figure__value pos">
              <span className="figure__glyph" aria-hidden="true">
                ▲
              </span>
              + gain
            </span>
            <span className="figure__value neg">
              <span className="figure__glyph" aria-hidden="true">
                ▼
              </span>
              − loss
            </span>
            <span className="figure__missing">no data</span>
            <span className="text-xs text-fg-muted">
              Green and red mean profit and loss, and nothing else. A value the platform could not
              supply reads &ldquo;no data&rdquo;, never zero. Display settings offer a blue and
              orange pair.
            </span>
          </div>
          <div className="stack">
            <StatusPill tone="ok">ok</StatusPill>
            <StatusPill tone="warn">degraded</StatusPill>
            <StatusPill tone="critical">down</StatusPill>
            <StatusPill tone="unknown">unknown</StatusPill>
            <span className="text-xs text-fg-muted">
              Health always carries a glyph; the worst check wins.
            </span>
          </div>
        </div>
      </section>
    </div>
  );
}

function Tokens() {
  return (
    <div className="stack" data-testid="legend-tokens">
      <section className="card">
        <h2 className="card__title">Colour tokens (OKLCH)</h2>
        <p className="muted mb-3">
          Every foreground and background pair is checked by{" "}
          <span className="mono">scripts/contrast.mjs</span>: 4.5:1 for text, 3:1 for graphics, in
          both themes and the colour-blind preset.
        </p>
        <div className="legend-grid">
          {COLOUR_TOKENS.map((group) => (
            <div key={group.group} className="stack">
              <h3 className="text-sm font-semibold">{group.group}</h3>
              {group.tokens.map((token) => (
                <span key={token.name} className="swatch">
                  <span className={`swatch__chip ${token.swatch}`} aria-hidden="true" />
                  <span className="mono text-xs">{token.name}</span>
                </span>
              ))}
            </div>
          ))}
        </div>
      </section>
      <section className="card">
        <h2 className="card__title">Type</h2>
        <p className="muted mb-3">
          Inter Variable with tabular figures and a slashed zero; JetBrains Mono for identifiers and
          hashes.
        </p>
        <div className="stack">
          {TYPE_SCALE.map((t) => (
            <div key={t.cls} className="flex items-baseline gap-3">
              <span className="w-28 flex-none text-xs text-fg-subtle">{t.name}</span>
              <span className={t.cls}>Operator workstation</span>
              <span className="text-xs text-fg-muted">{t.use}</span>
            </div>
          ))}
          <span className="mono text-sm">sha 3faa854 · run_0001 · O0 l1 I</span>
        </div>
      </section>
      <section className="card">
        <h2 className="card__title">Space, radii and motion</h2>
        <ul className="muted">
          <li>Spacing on a 4 px base: 2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64.</li>
          <li>Radii: 2 px chips, 4 px inputs and buttons, 6 px panels, 10 px dialogs.</li>
          <li>
            Motion: 80 to 240 ms, exits about a quarter shorter than entries; all of it off under
            reduced motion. Nothing animates a number.
          </li>
          <li>Density: 24, 32 or 40 px rows. Comfortable is automatic below 1024 px.</li>
          <li>Focus: a 2 px ring with a 2 px offset on every interactive element.</li>
        </ul>
      </section>
    </div>
  );
}

/** The open tab is URL state (`?tab=tokens`), so a legend section can be linked. */
const TABS = ["meanings", "tokens", "controls"] as const;
type LegendTab = (typeof TABS)[number];
const tabParser = parseAsStringLiteral(TABS).withDefault("meanings").withOptions({
  history: "replace",
});

function LegendTabsFromUrl() {
  const [tab, setTab] = useQueryState("tab", tabParser);
  return <LegendTabs tab={tab} onTab={(next) => void setTab(next)} />;
}

function LegendTabs({ tab, onTab }: { tab: LegendTab; onTab: (next: LegendTab) => void }) {
  return (
      <TabsRoot value={tab} onValueChange={(value) => onTab(value as LegendTab)}>
        <TabsList aria-label="Legend sections">
          <Tab value="meanings" data-testid="legend-tab-meanings">
            Meanings
          </Tab>
          <Tab value="tokens" data-testid="legend-tab-tokens">
            Tokens
          </Tab>
          <Tab value="controls" data-testid="legend-tab-controls">
            Controls
          </Tab>
        </TabsList>
        <TabsPanel value="meanings">
          <Meanings />
        </TabsPanel>
        <TabsPanel value="tokens">
          <Tokens />
        </TabsPanel>
        <TabsPanel value="controls">
          <Suspense fallback={<p className="muted">Loading controls.</p>}>
            <LegendControls />
          </Suspense>
        </TabsPanel>
      </TabsRoot>
  );
}
