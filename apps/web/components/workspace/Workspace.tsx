"use client";

import { Columns2, Maximize2, Minimize2, Rows2, Settings2, X } from "lucide-react";
import { parseAsString, useQueryState } from "nuqs";
import {
  Fragment,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { AsyncBoundary } from "@/components/AsyncBoundary";
import { Popover } from "@/components/ui/Popover";
import { useToast } from "@/components/ui/Toast";
import { formatUtcTime } from "@/lib/format";
import { useApi } from "@/lib/query";
import type { InstrumentRow, Page } from "@/lib/types";
import {
  LINK_GROUPS,
  LINK_ROLES,
  PATHS,
  addPanel,
  closePanel,
  deleteLayout,
  isSplit,
  panelOrder,
  resizeSplit,
  retarget,
  saveLayout,
  splitPanel,
  structureKey,
  updatePanel,
  type LayoutDoc,
  type LayoutNode,
  type LayoutPreset,
  type LayoutRevision,
  type LayoutSummary,
  type LinkGroup,
  type LinkRole,
  type PanelConfig,
  type PanelKind,
  type PanelKindSpec,
} from "@/lib/workspace";
import { PANELS } from "./registry";
import { useLayoutDoc, type LayoutDocState } from "./useLayoutDoc";

const LAST_KEY = "fiboki.workspace.last";
export const DEFAULT_LAYOUT = "Main";
const NARROW_PX = 760;

const ROLE_GLYPH: Record<LinkRole, string> = { source: "→", destination: "←", both: "↔" };
const ROLE_WORD: Record<LinkRole, string> = {
  source: "publishes its selection",
  destination: "follows the group",
  both: "publishes and follows",
};

function readLast(): string | null {
  try {
    return window.localStorage.getItem(LAST_KEY);
  } catch {
    return null;
  }
}

function writeLast(name: string) {
  try {
    window.localStorage.setItem(LAST_KEY, name);
  } catch {
    // Per-browser convenience only.
  }
}

/** The workspace screen (WS, F10): `?layout=<name>` (the command `LAYOUT <name>`). */
export function Workspace() {
  const [param, setParam] = useQueryState("layout", parseAsString.withOptions({ history: "push" }));
  const [fallback] = useState(() => (typeof window === "undefined" ? DEFAULT_LAYOUT : (readLast() ?? DEFAULT_LAYOUT)));
  const name = param && param.trim() !== "" ? param.trim() : fallback;
  useEffect(() => writeLast(name), [name]);
  const state = useLayoutDoc(name);
  const specs = useApi<Page<PanelKindSpec>>(PATHS.panels);
  return (
    <div className="ws" data-testid="workspace" data-layout={name}>
      <Toolbar state={state} onOpen={(next) => void setParam(next)} specs={specs.status === "success" ? specs.data.items : []} />
      {state.loadError ? (
        <div className="state state--error" role="alert" data-testid="ws-load-error">
          <div className="state__title">Could not load layout {name}</div>
          <div className="state__body">{state.loadError.message}</div>
        </div>
      ) : state.doc === null ? (
        <div className="state state--loading" role="status" data-testid="state-loading">
          <div className="state__title">Loading layout {name}…</div>
        </div>
      ) : (
        <Tiling state={state} doc={state.doc} specs={specs.status === "success" ? specs.data.items : []} />
      )}
    </div>
  );
}

// =================================================================== toolbar

function statusWord(state: LayoutDocState): { text: string; tone: string } {
  const s = state.status;
  if (!state.canEdit) return { text: "READ-ONLY", tone: "muted" };
  switch (s.kind) {
    case "loading":
      return { text: "LOADING", tone: "muted" };
    case "new":
      return { text: "NOT SAVED YET", tone: "warn" };
    case "dirty":
      return { text: "● UNSAVED", tone: "warn" };
    case "saving":
      return { text: s.action === "autosave" ? "AUTOSAVING…" : "SAVING…", tone: "muted" };
    case "clean":
      return { text: `SAVED v${state.version}${s.savedAt ? ` · ${formatUtcTime(s.savedAt)}` : ""}`, tone: "ok" };
    case "conflict":
      return { text: `CONFLICT · server v${s.current}`, tone: "critical" };
    case "error":
      return { text: "SAVE FAILED", tone: "critical" };
  }
}

function Toolbar({
  state,
  onOpen,
  specs,
}: {
  state: LayoutDocState;
  onOpen: (name: string) => void;
  specs: PanelKindSpec[];
}) {
  const layouts = useApi<Page<LayoutSummary>>(PATHS.layouts);
  const presets = useApi<Page<LayoutPreset>>(PATHS.presets);
  const notify = useToast();
  const [saveAs, setSaveAs] = useState("");
  const word = statusWord(state);
  const saved = layouts.status === "success" ? layouts.data.items : [];
  const names = Array.from(new Set([state.name, ...saved.map((l) => l.name)])).sort((a, b) => a.localeCompare(b));

  // ⌘/Ctrl+S saves now.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (state.canEdit) void state.save("save");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state]);

  const add = (kind: PanelKind) => {
    const spec = specs.find((s) => s.kind === kind);
    state.edit((doc) => {
      const result = addPanel(doc, {
        kind,
        title: "",
        link: { group: "none", role: spec?.publishes_symbol && !spec.follows_symbol ? "source" : "both" },
        symbol: spec?.follows_symbol ? "EURUSD" : null,
        settings: {},
      });
      if (!result.ok) {
        notify(result.reason);
        return doc;
      }
      return result.doc;
    });
  };

  return (
    <div className="ws-toolbar" role="toolbar" aria-label="Workspace layout" data-testid="ws-toolbar">
      <label className="ws-inline">
        <span className="ws-toolbar__label">Layout</span>
        <select
          value={state.name}
          onChange={(e) => onOpen(e.target.value)}
          data-testid="ws-layout-select"
          aria-label="Open a saved layout"
        >
          {names.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <span className={`ws-status ws-status--${word.tone}`} data-testid="ws-save-status" role="status" aria-live="polite">
        {word.text}
      </span>
      <button
        type="button"
        className="ws-btn"
        data-testid="ws-save"
        disabled={!state.canEdit || state.status.kind === "saving"}
        onClick={() => void state.save("save")}
        title="Save now (⌘/Ctrl+S)"
      >
        Save
      </button>
      <form
        className="ws-inline"
        onSubmit={async (event) => {
          event.preventDefault();
          const target = saveAs.trim();
          if (!target || !state.doc) return;
          try {
            await saveLayout(target, 0, state.doc, "save");
            setSaveAs("");
            notify(`Saved as ${target}`);
            onOpen(target);
          } catch (error) {
            notify(error instanceof Error ? error.message : String(error));
          }
        }}
      >
        <label className="sr-only" htmlFor="ws-save-as">
          Save as a new layout named
        </label>
        <input
          id="ws-save-as"
          className="ws-input"
          placeholder="Save as…"
          value={saveAs}
          maxLength={48}
          disabled={!state.canEdit}
          onChange={(e) => setSaveAs(e.target.value)}
          data-testid="ws-save-as"
        />
      </form>
      <label className="ws-inline">
        <span className="sr-only">Load a preset into this layout</span>
        <select
          value=""
          disabled={!state.canEdit || presets.status !== "success"}
          onChange={(e) => {
            const preset = presets.status === "success" ? presets.data.items.find((p) => p.key === e.target.value) : undefined;
            if (preset) state.replace(preset.layout);
          }}
          data-testid="ws-preset"
        >
          <option value="">Preset…</option>
          {presets.status === "success"
            ? presets.data.items.map((p) => (
                <option key={p.key} value={p.key} title={p.description}>
                  {p.name}
                </option>
              ))
            : null}
        </select>
      </label>
      <label className="ws-inline">
        <span className="sr-only">Add a panel</span>
        <select
          value=""
          disabled={!state.canEdit || specs.length === 0}
          onChange={(e) => {
            if (e.target.value) add(e.target.value as PanelKind);
          }}
          data-testid="ws-add"
        >
          <option value="">+ Panel…</option>
          {specs.map((s) => (
            <option key={s.kind} value={s.kind} title={s.description}>
              {s.code} · {s.label}
            </option>
          ))}
        </select>
      </label>
      <History name={state.name} version={state.version} />
      {state.version > 0 && state.canEdit ? (
        <button
          type="button"
          className="ws-btn ws-btn--ghost"
          data-testid="ws-delete"
          onClick={async () => {
            if (!window.confirm(`Delete layout ${state.name}? Its history is kept.`)) return;
            try {
              await deleteLayout(state.name, state.version);
              notify(`Deleted ${state.name}`);
              onOpen(DEFAULT_LAYOUT === state.name ? `${DEFAULT_LAYOUT}` : DEFAULT_LAYOUT);
            } catch (error) {
              notify(error instanceof Error ? error.message : String(error));
            }
          }}
        >
          Delete
        </button>
      ) : null}
      {state.status.kind === "conflict" ? (
        <div className="ws-conflict" role="alert" data-testid="ws-conflict">
          <span>{state.status.message}</span>
          <button type="button" className="ws-btn" onClick={() => void state.resolve("theirs")} data-testid="ws-conflict-theirs">
            Load theirs
          </button>
          <button type="button" className="ws-btn" onClick={() => void state.resolve("mine")} data-testid="ws-conflict-mine">
            Keep mine (new version)
          </button>
        </div>
      ) : null}
      {state.status.kind === "error" ? (
        <span className="ws-status ws-status--critical" role="alert" data-testid="ws-save-error">
          {state.status.message}
        </span>
      ) : null}
    </div>
  );
}

function History({ name, version }: { name: string; version: number }) {
  return (
    <Popover
      title={`History of ${name}`}
      testId="ws-history"
      align="end"
      trigger={
        <button type="button" className="ws-btn ws-btn--ghost" disabled={version === 0} data-testid="ws-history-open">
          History
        </button>
      }
    >
      <HistoryList name={name} />
    </Popover>
  );
}

function HistoryList({ name }: { name: string }) {
  const history = useApi<Page<LayoutRevision>>(PATHS.history(name));
  return (
    <AsyncBoundary state={history} label="layout history" onRetry={history.reload}>
      {(page) => (
        <ol className="ws-history" data-testid="ws-history-list">
          {page.items.map((r) => (
            <li key={r.version}>
              <span className="mono">v{r.version}</span> <span>{r.action}</span> <span className="mono">{formatUtcTime(r.at)}</span>{" "}
              <span>{r.actor}</span> <span className="muted">{r.panel_count} panels</span>
            </li>
          ))}
        </ol>
      )}
    </AsyncBoundary>
  );
}

// ==================================================================== tiling

interface TileActions {
  specs: Map<PanelKind, PanelKindSpec>;
  maximised: string | null;
  toggleMax: (id: string) => void;
  split: (id: string, orientation: "horizontal" | "vertical") => void;
  close: (id: string) => void;
  publish: (id: string, symbol: string) => void;
  settings: (id: string, patch: Record<string, string | boolean | number>) => void;
  configure: (id: string, patch: Partial<Omit<PanelConfig, "id">>) => void;
  readOnly: boolean;
  order: string[];
}

function Tiling({ state, doc, specs }: { state: LayoutDocState; doc: LayoutDoc; specs: PanelKindSpec[] }) {
  const [maximised, setMaximised] = useState<string | null>(null);
  const notify = useToast();
  const canvas = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(false);
  const specMap = useMemo(() => new Map(specs.map((s) => [s.kind, s])), [specs]);
  const order = useMemo(() => panelOrder(doc.root), [doc.root]);
  const { edit } = state;

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const measure = () => setNarrow(el.getBoundingClientRect().width < NARROW_PX);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const max = maximised !== null && doc.panels.some((p) => p.id === maximised) ? maximised : null;

  // Esc restores a maximised panel; ⌘/Ctrl+1..9 focus a panel by position.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && max !== null && !event.defaultPrevented) {
        const open = document.querySelector('[role="dialog"][data-open]');
        if (open) return;
        setMaximised(null);
        return;
      }
      if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && /^[1-9]$/.test(event.key)) {
        const id = order[Number(event.key) - 1];
        if (!id) return;
        event.preventDefault();
        if (max !== null && max !== id) setMaximised(null);
        window.requestAnimationFrame(() =>
          document.querySelector<HTMLElement>(`[data-tile-title="${CSS.escape(id)}"]`)?.focus(),
        );
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [max, order]);

  const actions: TileActions = {
    specs: specMap,
    maximised: max,
    order,
    readOnly: !state.canEdit,
    toggleMax: (id) => setMaximised((m) => (m === id ? null : id)),
    split: (id, orientation) =>
      edit((d) => {
        const source = d.panels.find((p) => p.id === id);
        const result = splitPanel(d, id, orientation, {
          kind: source?.kind ?? "notes",
          title: "",
          link: source?.link ?? { group: "none", role: "both" },
          symbol: source?.symbol ?? null,
          settings: {},
        });
        if (!result.ok) {
          notify(result.reason);
          return d;
        }
        return result.doc;
      }),
    close: (id) =>
      edit((d) => {
        const result = closePanel(d, id);
        if (!result.ok) {
          notify(result.reason);
          return d;
        }
        return result.doc;
      }),
    publish: (id, symbol) => edit((d) => retarget(d, id, symbol, (k) => specMap.get(k)?.follows_symbol === true)),
    settings: (id, patch) =>
      edit((d) => {
        const p = d.panels.find((x) => x.id === id);
        if (!p) return d;
        const next = { ...(p.settings ?? {}), ...patch };
        const same = Object.entries(patch).every(([k, v]) => (p.settings ?? {})[k] === v);
        return same ? d : updatePanel(d, id, { settings: next });
      }),
    configure: (id, patch) => edit((d) => updatePanel(d, id, patch)),
  };

  const byId = new Map(doc.panels.map((p) => [p.id, p]));
  let body: ReactNode;
  if (max !== null) {
    const panel = byId.get(max);
    body = panel ? <Tile panel={panel} actions={actions} position={order.indexOf(max)} /> : null;
  } else if (narrow) {
    body = (
      <div className="ws-stack" data-testid="ws-stack">
        {order.map((id, i) => {
          const panel = byId.get(id);
          return panel ? <Tile key={id} panel={panel} actions={actions} position={i} /> : null;
        })}
      </div>
    );
  } else {
    body = (
      <Node
        key={structureKey(doc.root)}
        node={doc.root}
        byId={byId}
        actions={actions}
        onResize={(splitId, sizes) => edit((d) => resizeSplit(d, splitId, sizes))}
      />
    );
  }
  return (
    <div ref={canvas} className="ws-canvas" data-testid="ws-canvas" data-maximised={max ?? undefined} data-narrow={narrow || undefined}>
      {body}
    </div>
  );
}

const childId = (node: LayoutNode) => (isSplit(node) ? `s_${node.id}` : `p_${node.panel}`);

function Node({
  node,
  byId,
  actions,
  onResize,
}: {
  node: LayoutNode;
  byId: Map<string, PanelConfig>;
  actions: TileActions;
  onResize: (splitId: string, sizes: number[]) => void;
}) {
  if (!isSplit(node)) {
    const panel = byId.get(node.panel);
    return panel ? <Tile panel={panel} actions={actions} position={actions.order.indexOf(panel.id)} /> : null;
  }
  const ids = node.children.map(childId);
  const defaultLayout = Object.fromEntries(ids.map((id, i) => [id, node.sizes[i] ?? 100 / ids.length]));
  return (
    <Group
      id={`ws-${node.id}`}
      orientation={node.orientation}
      defaultLayout={defaultLayout}
      className="ws-group"
      onLayoutChanged={(layout, meta) => {
        if (!meta.isUserInteraction) return;
        const sizes = ids.map((id, i) => {
          const size = layout[id];
          return typeof size === "number" ? size : (node.sizes[i] as number);
        });
        onResize(node.id, sizes);
      }}
    >
      {node.children.map((child, i) => (
        <Fragment key={ids[i]}>
          {i > 0 ? (
            <Separator
              className={`ws-sep ws-sep--${node.orientation}`}
              aria-label={`Resize ${node.orientation === "horizontal" ? "columns" : "rows"}`}
            />
          ) : null}
          <Panel id={ids[i]} minSize="8" className="ws-cell">
            <Node node={child} byId={byId} actions={actions} onResize={onResize} />
          </Panel>
        </Fragment>
      ))}
    </Group>
  );
}

function Tile({ panel, actions, position }: { panel: PanelConfig; actions: TileActions; position: number }) {
  const spec = actions.specs.get(panel.kind);
  const entry = PANELS[panel.kind];
  const Body = entry.Body;
  const title = panel.title || spec?.label || panel.kind;
  const isMax = actions.maximised === panel.id;
  const group = panel.link.group;
  const body = useRef<HTMLDivElement>(null);
  const onSymbol = useCallback((symbol: string) => actions.publish(panel.id, symbol), [actions, panel.id]);
  const onSettings = useCallback(
    (patch: Record<string, string | boolean | number>) => {
      if (!actions.readOnly) actions.settings(panel.id, patch);
    },
    [actions, panel.id],
  );

  // Tab and ⇧Tab on a tile title move to the next or previous tile title;
  // Enter or ↓ steps into the panel body.
  const onTitleKey = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "Tab" && !event.altKey && !event.ctrlKey && !event.metaKey) {
      const next = actions.order[position + (event.shiftKey ? -1 : 1)];
      if (next === undefined || actions.maximised !== null) return;
      const el = document.querySelector<HTMLElement>(`[data-tile-title="${CSS.escape(next)}"]`);
      if (!el) return;
      event.preventDefault();
      el.focus();
    } else if (event.key === "ArrowDown" || event.key === "Enter") {
      event.preventDefault();
      const target = body.current?.querySelector<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      (target ?? body.current)?.focus();
    }
  };

  return (
    <section
      className="ws-tile"
      data-testid="ws-tile"
      data-kind={panel.kind}
      data-panel={panel.id}
      data-group={group}
      data-max={isMax || undefined}
      aria-label={`${title} panel ${position + 1}`}
    >
      <header className="ws-tile__head" onDoubleClick={() => actions.toggleMax(panel.id)}>
        <span className="ws-tile__pos mono" aria-hidden="true">
          {position + 1 <= 9 ? position + 1 : "·"}
        </span>
        <span className="ws-tile__code mono">{entry.code}</span>
        <button
          type="button"
          className="ws-tile__title"
          data-tile-title={panel.id}
          data-testid="ws-tile-title"
          onKeyDown={onTitleKey}
          title={`${spec?.description ?? ""} Double-click the header to ${isMax ? "restore" : "maximise"}.`}
        >
          {title}
        </button>
        {panel.symbol && spec?.follows_symbol ? (
          <span className="ws-tile__sym mono" data-testid="ws-tile-symbol">
            {panel.symbol}
          </span>
        ) : null}
        {group !== "none" ? (
          <span
            className="ws-link-chip"
            data-group={group}
            data-testid="ws-link-chip"
            title={`Link group ${group}: ${ROLE_WORD[panel.link.role]}`}
          >
            <span className="sr-only">
              Link group {group}, {ROLE_WORD[panel.link.role]}
            </span>
            <span aria-hidden="true">{ROLE_GLYPH[panel.link.role]}</span>
          </span>
        ) : null}
        <span className="ws-tile__spacer" />
        {!actions.readOnly ? (
          <PanelSettings panel={panel} spec={spec} onChange={(patch) => actions.configure(panel.id, patch)} />
        ) : null}
        {!actions.readOnly ? (
          <>
            <button type="button" className="ws-icon" aria-label={`Split ${title} side by side`} title="Split right" onClick={() => actions.split(panel.id, "horizontal")} data-testid="ws-split-h">
              <Columns2 size={12} aria-hidden="true" />
            </button>
            <button type="button" className="ws-icon" aria-label={`Split ${title} top and bottom`} title="Split down" onClick={() => actions.split(panel.id, "vertical")} data-testid="ws-split-v">
              <Rows2 size={12} aria-hidden="true" />
            </button>
          </>
        ) : null}
        <button
          type="button"
          className="ws-icon"
          aria-label={isMax ? `Restore ${title} (Esc)` : `Maximise ${title}`}
          aria-pressed={isMax}
          title={isMax ? "Restore (Esc)" : "Maximise (double-click header)"}
          onClick={() => actions.toggleMax(panel.id)}
          data-testid="ws-max"
        >
          {isMax ? <Minimize2 size={12} aria-hidden="true" /> : <Maximize2 size={12} aria-hidden="true" />}
        </button>
        {!actions.readOnly ? (
          <button type="button" className="ws-icon" aria-label={`Close ${title}`} title="Close panel" onClick={() => actions.close(panel.id)} data-testid="ws-close">
            <X size={12} aria-hidden="true" />
          </button>
        ) : null}
      </header>
      <div ref={body} className="ws-panel" tabIndex={-1} data-testid="ws-panel">
        <Suspense
          fallback={
            <p className="muted" role="status">
              Loading the {title} panel…
            </p>
          }
        >
          <Body
            panel={panel}
            symbol={panel.symbol ?? null}
            onSymbol={onSymbol}
            onSettings={onSettings}
            maximised={isMax}
            readOnly={actions.readOnly}
          />
        </Suspense>
      </div>
    </section>
  );
}

function PanelSettings({
  panel,
  spec,
  onChange,
}: {
  panel: PanelConfig;
  spec: PanelKindSpec | undefined;
  onChange: (patch: Partial<Omit<PanelConfig, "id">>) => void;
}) {
  return (
    <Popover
      title={`Panel: ${panel.title || spec?.label || panel.kind}`}
      testId="ws-panel-settings"
      align="end"
      trigger={
        <button type="button" className="ws-icon" aria-label="Panel link and title" title="Link and title" data-testid="ws-panel-settings-open">
          <Settings2 size={12} aria-hidden="true" />
        </button>
      }
    >
      <PanelSettingsForm panel={panel} spec={spec} onChange={onChange} />
    </Popover>
  );
}

function PanelSettingsForm({
  panel,
  spec,
  onChange,
}: {
  panel: PanelConfig;
  spec: PanelKindSpec | undefined;
  onChange: (patch: Partial<Omit<PanelConfig, "id">>) => void;
}) {
  const instruments = useApi<Page<InstrumentRow>>("/api/markets/instruments");
  const symbols = instruments.status === "success" ? instruments.data.items.map((i) => i.symbol) : [];
  const [symbol, setSymbol] = useState(panel.symbol ?? "");
  const known = symbols.includes(symbol.toUpperCase());
  return (
    <div className="ws-form" data-testid="ws-panel-form">
      <label>
        <span className="field-label">Title</span>
        <input
          className="ws-input"
          defaultValue={panel.title}
          maxLength={48}
          placeholder={spec?.label}
          onBlur={(e) => onChange({ title: e.target.value.trim() })}
        />
      </label>
      <label>
        <span className="field-label">Link group</span>
        <select
          value={panel.link.group}
          onChange={(e) => onChange({ link: { ...panel.link, group: e.target.value as LinkGroup } })}
          data-testid="ws-link-group"
        >
          {LINK_GROUPS.map((g) => (
            <option key={g} value={g}>
              {g === "none" ? "Not linked" : g}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span className="field-label">Role in the group</span>
        <select
          value={panel.link.role}
          onChange={(e) => onChange({ link: { ...panel.link, role: e.target.value as LinkRole } })}
          data-testid="ws-link-role"
        >
          {LINK_ROLES.map((r) => (
            <option key={r} value={r} disabled={(r !== "destination" && spec?.publishes_symbol === false) || (r !== "source" && spec?.follows_symbol === false)}>
              {r} ({ROLE_WORD[r]})
            </option>
          ))}
        </select>
      </label>
      {spec?.follows_symbol ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (known) onChange({ symbol: symbol.toUpperCase() });
          }}
        >
          <label>
            <span className="field-label">Symbol</span>
            <input
              className="ws-input mono"
              list="ws-symbols"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              data-testid="ws-panel-symbol"
            />
          </label>
          <datalist id="ws-symbols">
            {symbols.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
          <button type="submit" className="ws-btn" disabled={!known}>
            Show {known ? symbol.toUpperCase() : "…"}
          </button>
          {!known && symbol !== "" ? <p className="muted">Not a symbol the platform lists.</p> : null}
        </form>
      ) : null}
      <p className="muted">
        Reads: <span className="mono">{spec?.reads.join(", ") || "nothing (layout only)"}</span>
      </p>
    </div>
  );
}
