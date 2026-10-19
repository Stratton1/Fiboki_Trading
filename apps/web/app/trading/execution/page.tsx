"use client";

import Link from "next/link";
import { parseAsString, parseAsStringLiteral, useQueryState } from "nuqs";
import { useMemo, useState } from "react";
import { GridPage } from "@/components/GridPage";
import { NoteEditor } from "@/components/ops/NoteEditor";
import { UrlState } from "@/components/UrlState";
import { ProvenanceChip } from "@/components/ProvenanceChip";
import { Distribution } from "@/components/charts";
import type { GridColumn } from "@/components/grid";
import { useApi } from "@/lib/query";
import { PROVENANCES, type InstrumentRow, type Page, type Provenance, type TradeRow } from "@/lib/types";
import { formatTimestamp, priceDecimals } from "@/lib/format";
import { deriveProvenance } from "@/lib/provenance";
import { route } from "@/lib/routes";
import { JOURNAL_NOTES_PATH, JOURNAL_VOCAB_PATH, NOTE_GRADES, type JournalVocabularyView, type TradeNoteView } from "@/lib/ops";
import type { Envelope } from "@/lib/types";

/**
 * TRADING · Execution (the Journal's trade list).
 *
 * This replaces V1's `/trades`, which was titled "Paper / Backtest" and
 * contained zero paper trades, and which the dashboard re-rendered under
 * "Recent Execution" below a tile called "Fleet PnL (live)".
 *
 * Provenance is a COLUMN and a FILTER. The page title makes no claim about
 * where these trades came from, because the rows say it themselves. The
 * filter is URL state (`?provenance=paper`), so a filtered view can be linked,
 * and it sits OUTSIDE the async boundary, so changing it never unmounts it or
 * loses focus (report G W-18).
 *
 * Wave 3: the rows are a DataGrid (sort, filter, columns, keyboard, CSV with
 * provenance). Prices use the instrument's pip size from the platform when the
 * instrument list is available; otherwise the unitless default.
 */
const FILTERS = ["all", ...PROVENANCES] as const;
const filterParser = parseAsStringLiteral(FILTERS)
  .withDefault("all")
  .withOptions({ history: "replace" });

/** Closed trades change as the worker closes them: re-read every 30 s. */
const TRADES_REFRESH_MS = 30_000;

export default function ExecutionPage() {
  return (
    <UrlState fallback={<ExecutionView filter="all" onFilter={() => undefined} strategy={null} instrument={null} onClear={() => undefined} journal={NO_JOURNAL_FILTER} onJournal={() => undefined} />}>
      <ExecutionFromUrl />
    </UrlState>
  );
}

const textParser = parseAsString.withOptions({ history: "replace" });

/** wave ops: the structured journal's filters (server-side, /api/trading/journal/trades). */
interface JournalFilter {
  tag: string | null;
  grade: string | null;
  mistake: string | null;
}
const NO_JOURNAL_FILTER: JournalFilter = { tag: null, grade: null, mistake: null };

function ExecutionFromUrl() {
  const [filter, setFilter] = useQueryState("provenance", filterParser);
  // Cross-links land here filtered: a bot's or a strategy's journal
  // (`?strategy=`), an instrument's trades (`?instrument=`, `EURUSD TRD`).
  const [strategy, setStrategy] = useQueryState("strategy", textParser);
  const [instrument, setInstrument] = useQueryState("instrument", textParser);
  const [tag, setTag] = useQueryState("tag", textParser);
  const [grade, setGrade] = useQueryState("grade", textParser);
  const [mistake, setMistake] = useQueryState("mistake", textParser);
  return (
    <ExecutionView
      filter={filter}
      onFilter={(next) => void setFilter(next)}
      strategy={strategy}
      instrument={instrument}
      onClear={() => {
        void setStrategy(null);
        void setInstrument(null);
      }}
      journal={{ tag, grade, mistake }}
      onJournal={(next) => {
        void setTag(next.tag);
        void setGrade(next.grade);
        void setMistake(next.mistake);
      }}
    />
  );
}

function ExecutionView({
  filter,
  onFilter,
  strategy,
  instrument,
  onClear,
  journal,
  onJournal,
}: {
  filter: Provenance | "all";
  onFilter: (next: Provenance | "all") => void;
  strategy: string | null;
  instrument: string | null;
  onClear: () => void;
  journal: JournalFilter;
  onJournal: (next: JournalFilter) => void;
}) {
  const query =
    (filter === "all" ? "" : `&provenance=${filter}`) +
    (strategy ? `&strategy_id=${encodeURIComponent(strategy)}` : "") +
    (instrument ? `&instrument=${encodeURIComponent(instrument)}` : "");
  // wave ops: a journal filter switches the grid to the server-side filtered
  // record (/api/trading/journal/trades); without one the path is unchanged.
  const journalQuery =
    (journal.tag ? `&tag=${encodeURIComponent(journal.tag)}` : "") +
    (journal.grade ? `&grade=${encodeURIComponent(journal.grade)}` : "") +
    (journal.mistake ? `&mistake=${encodeURIComponent(journal.mistake)}` : "");
  const gridPath = journalQuery
    ? `/api/trading/journal/trades?limit=200${query}${journalQuery}`
    : `/api/trading/trades?limit=200${query}`;
  const notes = useApi<Page<TradeNoteView>>(JOURNAL_NOTES_PATH, { refreshMs: TRADES_REFRESH_MS });
  const vocab = useApi<Envelope<JournalVocabularyView>>(JOURNAL_VOCAB_PATH);
  const noteOf = useMemo(() => {
    const map = new Map<string, TradeNoteView>();
    if (notes.status === "success") for (const n of notes.data.items) map.set(n.trade_id, n);
    return map;
  }, [notes]);
  const [editing, setEditing] = useState<string | null>(null);
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments");
  const pips = useMemo(() => {
    const map = new Map<string, number | null>();
    if (instruments.status === "success") {
      for (const row of instruments.data.items) {
        map.set(row.symbol, priceDecimals(row.pip_size.value));
      }
    }
    return map;
  }, [instruments]);

  const columns: GridColumn<TradeRow>[] = [
    {
      id: "provenance",
      header: "Source",
      value: (row) => row.provenance,
      cell: (row) => <ProvenanceChip provenance={row.provenance} />,
      width: 96,
      pin: true,
    },
    { id: "trade_id", header: "Trade", value: (row) => row.trade_id, width: 120 },
    {
      id: "exit_time",
      header: "Closed",
      kind: "time",
      value: (row) => row.exit_time,
      cell: (row) => formatTimestamp(row.exit_time),
    },
    {
      id: "instrument",
      header: "Instrument",
      value: (row) => row.instrument,
      // The chart at this trade: the chart workstation reads `trade` to select it.
      cell: (row) => (
        <Link href={route.instrument(row.instrument, { trade: row.trade_id })} data-testid="journal-chart-link">
          {row.instrument}
        </Link>
      ),
      width: 100,
    },
    {
      id: "strategy",
      header: "Strategy",
      value: (row) => row.strategy_id,
      cell: (row) => (
        <Link prefetch={false} href={route.strategy(row.strategy_id)} data-testid="journal-strategy-link">
          {row.strategy_id}
        </Link>
      ),
      width: 180,
    },
    { id: "direction", header: "Side", value: (row) => row.direction.toUpperCase(), width: 72 },
    { id: "size", header: "Size", figure: (row) => row.size },
    {
      id: "entry_price",
      header: "Entry",
      figure: (row) => row.entry_price,
      decimals: (row) => pips.get(row.instrument) ?? null,
      hidden: true,
    },
    {
      id: "exit_price",
      header: "Exit price",
      figure: (row) => row.exit_price,
      decimals: (row) => pips.get(row.instrument) ?? null,
      hidden: true,
    },
    { id: "gross", header: "Gross P&L", figure: (row) => row.gross_pnl, signed: true, hidden: true },
    { id: "costs", header: "Costs", figure: (row) => row.costs, hidden: true },
    { id: "net", header: "Net P&L", figure: (row) => row.net_pnl, signed: true, width: 124 },
    { id: "r", header: "R", figure: (row) => row.r_multiple, signed: true, width: 88 },
    { id: "reason", header: "Exit", value: (row) => row.exit_reason, width: 120 },
    // wave ops: the structured journal (operator annotations, server-stored)
    {
      id: "grade",
      header: "Grade",
      value: (row) => noteOf.get(row.trade_id)?.grade ?? "",
      cell: (row) => <span className="mono" data-testid="journal-grade">{noteOf.get(row.trade_id)?.grade ?? "—"}</span>,
      width: 64,
    },
    {
      id: "tags",
      header: "Tags",
      value: (row) => (noteOf.get(row.trade_id)?.tags ?? []).join(" "),
      cell: (row) => (
        <span className="ops-tags" data-testid="journal-tags">
          {(noteOf.get(row.trade_id)?.tags ?? []).map((t) => (
            <span key={t} className="ops-tag">
              {t}
            </span>
          ))}
        </span>
      ),
      width: 160,
    },
    {
      id: "note",
      header: "Note",
      value: (row) => noteOf.get(row.trade_id)?.note ?? "",
      cell: (row) => {
        const n = noteOf.get(row.trade_id);
        return n ? (
          <span title={n.note} data-testid="journal-note">
            {n.intervention_reason ? "✋ " : ""}
            {n.note.length > 48 ? `${n.note.slice(0, 48)}…` : n.note || "(no text)"}
          </span>
        ) : (
          <span className="muted">—</span>
        );
      },
      width: 220,
    },
    {
      id: "entry_time",
      header: "Opened",
      kind: "time",
      value: (row) => row.entry_time,
      cell: (row) => formatTimestamp(row.entry_time),
      hidden: true,
    },
  ];

  return (
    <GridPage<TradeRow>
      title="Execution"
      intro="Every closed trade the platform holds, from every source. The Source column is the truth; this heading makes no claim about it."
      path={gridPath}
      label="trades"
      gridId="trades"
      columns={columns}
      rowKey={(row) => row.trade_id}
      refreshMs={TRADES_REFRESH_MS}
      onRowActivate={(row) => setEditing(row.trade_id)}
      before={
        <div className="row mb-3">
          <label className="m-0" htmlFor="prov-filter">
            Filter by provenance
          </label>
          <select
            id="prov-filter"
            data-testid="provenance-filter"
            value={filter}
            onChange={(e) => onFilter(e.target.value as Provenance | "all")}
          >
            <option value="all">All sources (mixed)</option>
            {PROVENANCES.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          {strategy || instrument ? (
            <span className="muted" data-testid="journal-scope">
              {strategy ? (
                <>
                  strategy <Link prefetch={false} href={route.strategy(strategy)}>{strategy}</Link>{" "}
                </>
              ) : null}
              {instrument ? (
                <>
                  instrument <Link href={route.instrument(instrument)}>{instrument}</Link>{" "}
                </>
              ) : null}
              <button type="button" className="linklike" onClick={onClear} data-testid="journal-scope-clear">
                show all
              </button>
            </span>
          ) : null}
          {/* wave ops: journal filters, applied by the API */}
          <JournalFilters journal={journal} onJournal={onJournal} vocab={vocab.status === "success" ? vocab.data.data : null} />
          <span className="ops-hint" data-testid="journal-hint">
            Enter or double-click a row: its journal note.
          </span>
          <NoteEditor tradeId={editing} onClose={() => setEditing(null)} />
        </div>
      }
    >
      {(page) => {
        // Label the distribution from the values that are IN it: rows whose
        // R is null contribute nothing and so claim nothing. It is drawn over
        // the rows the platform returned, and says so when that is a page.
        const plotted = page.items.filter((t) => t.r_multiple.value !== null);
        return (
          <div className="card">
            <Distribution
              title={
                page.total > page.items.length
                  ? `R-multiple distribution (first ${page.items.length} of ${page.total})`
                  : "R-multiple distribution"
              }
              provenance={deriveProvenance(
                plotted.map((t) => t.r_multiple.provenance),
                "No trade in this result has an R-multiple, so nothing is plotted and nothing is labelled.",
              )}
              unit="R"
              values={plotted
                .map((t) => t.r_multiple.value)
                .filter((v): v is number => v !== null)}
            />
          </div>
        );
      }}
    </GridPage>
  );
}

function JournalFilters({
  journal,
  onJournal,
  vocab,
}: {
  journal: JournalFilter;
  onJournal: (next: JournalFilter) => void;
  vocab: JournalVocabularyView | null;
}) {
  const tags = Object.keys(vocab?.tags ?? {});
  const mistakes = Object.keys(vocab?.mistakes ?? {});
  return (
    <>
      <label className="m-0" htmlFor="journal-tag">
        Tag
      </label>
      <select id="journal-tag" data-testid="journal-filter-tag" value={journal.tag ?? ""} onChange={(e) => onJournal({ ...journal, tag: e.target.value || null })}>
        <option value="">any</option>
        {tags.map((t) => (
          <option key={t} value={t}>
            {t} ({vocab?.tags[t]})
          </option>
        ))}
        {journal.tag && !tags.includes(journal.tag) ? <option value={journal.tag}>{journal.tag}</option> : null}
      </select>
      <label className="m-0" htmlFor="journal-grade">
        Grade
      </label>
      <select id="journal-grade" data-testid="journal-filter-grade" value={journal.grade ?? ""} onChange={(e) => onJournal({ ...journal, grade: e.target.value || null })}>
        <option value="">any</option>
        {NOTE_GRADES.map((g) => (
          <option key={g} value={g}>
            {g}
          </option>
        ))}
      </select>
      <label className="m-0" htmlFor="journal-mistake">
        Mistake
      </label>
      <select id="journal-mistake" data-testid="journal-filter-mistake" value={journal.mistake ?? ""} onChange={(e) => onJournal({ ...journal, mistake: e.target.value || null })}>
        <option value="">any</option>
        {mistakes.map((m) => (
          <option key={m} value={m}>
            {m} ({vocab?.mistakes[m]})
          </option>
        ))}
        {journal.mistake && !mistakes.includes(journal.mistake) ? <option value={journal.mistake}>{journal.mistake}</option> : null}
      </select>
    </>
  );
}
