import type {
  IChartApiBase,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { ChartTheme } from "@/lib/chart-theme";
import {
  distToSegment,
  fibLevels,
  formatSpan,
  logicalToTime,
  measure,
  newDrawingId,
  rayEnd,
  timeToLogical,
  type Pt,
} from "@/lib/drawing-geometry";
import { formatNumber } from "@/lib/format";
import type { Drawing, DrawingKind } from "@/lib/types";

/**
 * Operator drawings on the price chart: the renderer and the pointer
 * controller (plan §10 C11).
 *
 * A drawing is an annotation: pure geometry on points the operator placed,
 * stored as (UTC time, price) so it survives zoom, scroll and a timeframe
 * change (lib/drawing-geometry.ts maps time to the logical bar axis). The
 * Fibonacci tool lays the standard ratios between two points and is labelled
 * as a drawing; nothing drawn here is ever read by a strategy. The measure
 * tool is display-only and never stored.
 *
 * Interaction: when a drawing tool is active, or the pointer goes down on one
 * of the operator's own drawings, the controller claims the gesture in the
 * CAPTURE phase and stops the library's mousedown/touchstart, so the chart
 * does not pan underneath the drag; otherwise every event reaches the chart
 * untouched. Moves repaint through the primitive directly (no React render
 * per pointer move); React hears only the finished edit.
 */

export type DrawingTool = "select" | DrawingKind | "measure";

export interface DrawingAnchor {
  /** UTC seconds. */
  t: number;
  price: number;
}

export interface MeasureState {
  a: DrawingAnchor;
  b: DrawingAnchor;
}

export interface DrawingLayerState {
  /** The operator's own drawings in scope for this timeframe. */
  drawings: readonly Drawing[];
  /** Other operators' drawings: shown muted, never editable. */
  others: readonly { owner: string; drawing: Drawing }[];
  times: readonly number[];
  theme: ChartTheme;
  selectedId: string | null;
  hiddenAll: boolean;
  priceDecimals: number | null;
}

interface Transient {
  draft: Drawing | null;
  measure: MeasureState | null;
  /** The drawing being moved: drawn as the draft, not in place. */
  hideId: string | null;
}

/** What the renderer laid out last frame, for hit testing in pixels. */
interface Layout {
  id: string;
  kind: DrawingKind;
  handles: Pt[];
  hit: (p: Pt) => boolean;
}

const HIT_PX = 6;
const HANDLE_PX = 7;

type Ctx = CanvasRenderingContext2D;

export function toAnchor(point: { t: string; price: number }): DrawingAnchor {
  return { t: Math.floor(Date.parse(point.t) / 1_000), price: point.price };
}

export function fromAnchor(anchor: DrawingAnchor): { t: string; price: number } {
  return { t: new Date(anchor.t * 1_000).toISOString().replace(".000Z", "Z"), price: anchor.price };
}

class DrawingRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly layer: DrawingPrimitive) {}

  draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]) {
    const scope = this.layer.scope();
    if (!scope) return;
    const { chart, series, state, transient } = scope;
    const layouts: Layout[] = [];
    const { theme, times } = state;
    const time = chart.timeScale();
    const toPx = (a: DrawingAnchor): Pt | null => {
      const logical = timeToLogical(times, a.t);
      if (logical === null) return null;
      const x = time.logicalToCoordinate(logical as Logical);
      const y = series.priceToCoordinate(a.price);
      return x === null || y === null ? null : { x, y };
    };
    const fmt = (v: number) => formatNumber(v, "", { decimals: state.priceDecimals });

    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const { width, height } = mediaSize;
      ctx.save();
      ctx.font = `500 10.5px ${theme.fontUi}`;
      ctx.textBaseline = "bottom";
      if (!state.hiddenAll) {
        for (const other of state.others) {
          if (!other.drawing.hidden) drawOne(ctx, other.drawing, theme.drawingOther, false, other.owner);
        }
        for (const d of state.drawings) {
          if (d.id === transient.hideId) continue;
          if (d.hidden && d.id !== state.selectedId) continue;
          const layout = drawOne(ctx, d, theme.drawing, d.id === state.selectedId, null);
          if (layout) layouts.push(layout);
        }
      }
      if (transient.draft) drawOne(ctx, transient.draft, theme.drawing, true, null);
      if (transient.measure) drawMeasure(ctx, transient.measure);
      ctx.restore();
      this.layer.layouts = layouts;

      function handlesOf(points: Pt[], selected: boolean) {
        if (!selected) return;
        for (const p of points) {
          ctx.beginPath();
          ctx.rect(p.x - 3.5, p.y - 3.5, 7, 7);
          ctx.fillStyle = theme.background;
          ctx.fill();
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = theme.drawing;
          ctx.stroke();
        }
      }

      function tag(text: string, x: number, y: number, ink: string) {
        ctx.fillStyle = ink;
        ctx.fillText(text, x, y);
      }

      function drawOne(
        c: Ctx,
        d: Drawing,
        ink: string,
        selected: boolean,
        owner: string | null,
      ): Layout | null {
        const pts = d.points.map((p) => toPx(toAnchor(p)));
        if (pts.some((p) => p === null)) return null;
        const [a, b] = pts as Pt[];
        if (!a) return null;
        c.save();
        c.strokeStyle = ink;
        c.fillStyle = ink;
        c.lineWidth = selected ? 2 : 1.25;
        c.globalAlpha = d.hidden ? 0.4 : 1;
        if (owner !== null) c.setLineDash([2, 3]);
        let hit: (p: Pt) => boolean = () => false;
        const handles: Pt[] = [a, ...(b ? [b] : [])];
        switch (d.kind) {
          case "trend": {
            if (!b) break;
            c.beginPath();
            c.moveTo(a.x, a.y);
            c.lineTo(b.x, b.y);
            c.stroke();
            hit = (p) => distToSegment(p, a, b) <= HIT_PX;
            break;
          }
          case "ray": {
            if (!b) break;
            const end = rayEnd(a, b, width, height);
            c.beginPath();
            c.moveTo(a.x, a.y);
            c.lineTo(end.x, end.y);
            c.stroke();
            hit = (p) => distToSegment(p, a, end) <= HIT_PX;
            break;
          }
          case "hline": {
            c.beginPath();
            c.moveTo(0, a.y);
            c.lineTo(width, a.y);
            c.stroke();
            const level = d.points[0];
            if (level) tag(fmt(level.price), 4, a.y - 2, ink);
            hit = (p) => Math.abs(p.y - a.y) <= HIT_PX;
            break;
          }
          case "vline": {
            c.beginPath();
            c.moveTo(a.x, 0);
            c.lineTo(a.x, height);
            c.stroke();
            hit = (p) => Math.abs(p.x - a.x) <= HIT_PX;
            break;
          }
          case "rect": {
            if (!b) break;
            const x = Math.min(a.x, b.x);
            const y = Math.min(a.y, b.y);
            const w = Math.abs(b.x - a.x);
            const h = Math.abs(b.y - a.y);
            c.fillStyle = owner === null ? theme.drawingFill : "transparent";
            c.fillRect(x, y, w, h);
            c.strokeRect(x, y, w, h);
            hit = (p) =>
              p.x >= x - HIT_PX && p.x <= x + w + HIT_PX && p.y >= y - HIT_PX && p.y <= y + h + HIT_PX;
            break;
          }
          case "fib": {
            if (!b) break;
            const p1 = d.points[0]?.price;
            const p2 = d.points[1]?.price;
            if (p1 === undefined || p2 === undefined) break;
            const left = Math.min(a.x, b.x);
            const right = Math.max(a.x, b.x, left + 40);
            const levels = fibLevels(p1, p2, d.fib_extensions);
            const ys: number[] = [];
            c.beginPath();
            c.setLineDash([3, 3]);
            c.moveTo(a.x, a.y);
            c.lineTo(b.x, b.y);
            c.stroke();
            c.setLineDash(owner !== null ? [2, 3] : []);
            for (const level of levels) {
              const ly = series.priceToCoordinate(level.price);
              if (ly === null) continue;
              ys.push(ly);
              c.globalAlpha = (d.hidden ? 0.4 : 1) * (level.extension ? 0.7 : 1);
              c.beginPath();
              c.moveTo(left, ly);
              c.lineTo(right, ly);
              c.stroke();
              tag(`${level.ratio} · ${fmt(level.price)}`, right + 4, ly + 4, ink);
            }
            c.globalAlpha = d.hidden ? 0.4 : 1;
            tag("FIB (drawing)", left, Math.min(a.y, b.y) - 4, ink);
            const top = Math.min(...ys);
            const bottom = Math.max(...ys);
            hit = (p) =>
              p.x >= left - HIT_PX && p.x <= right + HIT_PX && p.y >= top - HIT_PX && p.y <= bottom + HIT_PX;
            break;
          }
          case "text": {
            const text = d.text;
            c.font = `600 11.5px ${theme.fontUi}`;
            const w = c.measureText(text).width + 10;
            const h = 18;
            c.fillStyle = theme.background;
            c.globalAlpha = 0.9;
            c.fillRect(a.x, a.y - h, w, h);
            c.globalAlpha = d.hidden ? 0.4 : 1;
            c.strokeRect(a.x, a.y - h, w, h);
            c.fillStyle = ink;
            c.fillText(text, a.x + 5, a.y - 4);
            hit = (p) => p.x >= a.x - 2 && p.x <= a.x + w + 2 && p.y >= a.y - h - 2 && p.y <= a.y + 2;
            break;
          }
        }
        if (owner !== null) {
          c.setLineDash([]);
          tag(owner, a.x + 4, a.y - 4, ink);
        }
        if (d.locked && selected) tag("locked", a.x + 4, a.y + 14, ink);
        c.restore();
        handlesOf(handles, selected && !d.locked);
        return owner === null ? { id: d.id, kind: d.kind, handles, hit } : null;
      }

      function drawMeasure(c: Ctx, m: MeasureState) {
        const a = toPx(m.a);
        const b = toPx(m.b);
        if (!a || !b) return;
        const r = measure(times, m.a, m.b);
        c.save();
        c.fillStyle = theme.drawingFill;
        c.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
        c.strokeStyle = theme.drawing;
        c.setLineDash([4, 3]);
        c.beginPath();
        c.moveTo(a.x, a.y);
        c.lineTo(b.x, b.y);
        c.stroke();
        c.setLineDash([]);
        const lines = [
          `${r.priceDelta >= 0 ? "+" : "−"}${fmt(Math.abs(r.priceDelta))}` +
            (r.percent === null ? "" : ` (${r.percent >= 0 ? "+" : "−"}${Math.abs(r.percent).toFixed(2)}%)`),
          `${r.bars} bars · ${formatSpan(r.seconds)}`,
          "measure (not saved)",
        ];
        c.font = `600 11px ${theme.fontCode}`;
        const w = Math.max(...lines.map((l) => c.measureText(l).width)) + 12;
        const h = lines.length * 15 + 6;
        const bx = Math.min(Math.max(4, b.x + 8), width - w - 4);
        const by = Math.min(Math.max(4, b.y + 8), height - h - 4);
        c.fillStyle = theme.background;
        c.globalAlpha = 0.95;
        c.fillRect(bx, by, w, h);
        c.globalAlpha = 1;
        c.strokeStyle = theme.drawing;
        c.strokeRect(bx, by, w, h);
        c.fillStyle = theme.text;
        lines.forEach((line, i) => c.fillText(line, bx + 6, by + 17 + i * 15));
        c.restore();
      }
    });
  }
}

class View implements IPrimitivePaneView {
  constructor(private readonly impl: IPrimitivePaneRenderer) {}
  zOrder(): PrimitivePaneViewZOrder {
    return "top";
  }
  renderer(): IPrimitivePaneRenderer {
    return this.impl;
  }
}

export class DrawingPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApiBase<Time> | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private request: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];
  private transient: Transient = { draft: null, measure: null, hideId: null };
  layouts: Layout[] = [];

  constructor(private state: DrawingLayerState) {
    this.views = [new View(new DrawingRenderer(this))];
  }

  attached(param: SeriesAttachedParameter<Time>) {
    this.chart = param.chart;
    this.series = param.series;
    this.request = param.requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.request = null;
  }

  update(state: DrawingLayerState) {
    this.state = state;
    this.request?.();
  }

  setTransient(next: Partial<Transient>) {
    this.transient = { ...this.transient, ...next };
    this.request?.();
  }

  get current(): DrawingLayerState {
    return this.state;
  }

  get measureState(): MeasureState | null {
    return this.transient.measure;
  }

  paneViews() {
    return this.views;
  }

  scope() {
    if (!this.chart || !this.series) return null;
    return { chart: this.chart, series: this.series, state: this.state, transient: this.transient };
  }
}

// ================================================================ controller

export interface DrawingControls {
  tool: DrawingTool;
  canEdit: boolean;
  lockedAll: boolean;
  /** The timeframe scope a NEW drawing gets (null: every timeframe). */
  scope: string | null;
}

export interface DrawingCallbacks {
  controls: () => DrawingControls;
  /** The operator's whole set changed (create, move, delete): persist it. */
  commit: (next: Drawing[], selectId: string | null) => void;
  select: (id: string | null) => void;
  /** A text tool click (or a double click on a note): ask for its text. */
  requestText: (at: { t: string; price: number }, screen: Pt, editId: string | null) => void;
  /** A drawing was finished with a one-shot tool: go back to select. */
  toolDone: () => void;
  /** All drawings of the operator, every timeframe (the set to persist). */
  allDrawings: () => Drawing[];
  /** A finished measurement in words, or null when it is cleared. */
  measured: (text: string | null) => void;
}

type Gesture =
  | { kind: "create"; tool: DrawingTool; start: Pt; a: DrawingAnchor; clickMode: boolean }
  | { kind: "move"; id: string; start: Pt; origin: Drawing; handle: number | null; moved: boolean };

/**
 * Pointer handling for drawing, selecting and moving. Owned by PriceChart,
 * which gives it the chart, the candle series and the chart's box.
 */
export class DrawingController {
  private gesture: Gesture | null = null;
  private claimed = false;
  private lastTool: DrawingTool = "select";
  private readonly cleanup: (() => void)[] = [];

  constructor(
    private readonly el: HTMLElement,
    private readonly chart: IChartApiBase<Time>,
    private readonly series: ISeriesApi<SeriesType, Time>,
    private readonly primitive: DrawingPrimitive,
    private readonly cb: DrawingCallbacks,
  ) {
    const capture = { capture: true } as const;
    const block = (event: Event) => {
      if (this.claimed) event.stopPropagation();
    };
    const down = (event: PointerEvent) => this.onDown(event);
    const move = (event: PointerEvent) => this.onMove(event);
    const up = (event: PointerEvent) => this.onUp(event);
    const dbl = (event: MouseEvent) => this.onDouble(event);
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && (this.gesture !== null || this.primitive.measureState)) this.cancel();
    };
    window.addEventListener("keydown", key, capture);
    this.cleanup.push(() => window.removeEventListener("keydown", key, capture));
    el.addEventListener("pointerdown", down, capture);
    el.addEventListener("mousedown", block, capture);
    el.addEventListener("touchstart", block, { capture: true, passive: true });
    el.addEventListener("dblclick", dbl, capture);
    el.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    this.cleanup.push(
      () => el.removeEventListener("pointerdown", down, capture),
      () => el.removeEventListener("mousedown", block, capture),
      () => el.removeEventListener("touchstart", block, capture),
      () => el.removeEventListener("dblclick", dbl, capture),
      () => el.removeEventListener("pointermove", move),
      () => window.removeEventListener("pointerup", up),
    );
  }

  dispose() {
    for (const fn of this.cleanup.splice(0)) fn();
    this.setScroll(true);
  }

  /** Escape: drop an unfinished drawing and the measure read-out. */
  cancel() {
    const hadMeasure = this.primitive.measureState !== null;
    this.gesture = null;
    this.claimed = false;
    this.primitive.setTransient({ draft: null, measure: null, hideId: null });
    this.setScroll(true);
    if (hadMeasure) this.cb.measured(null);
  }

  /** Re-evaluate whether the chart may pan (a drawing tool is active or not). */
  sync() {
    const { tool } = this.cb.controls();
    if (tool !== this.lastTool) {
      // A new tool abandons whatever the old one had started.
      this.lastTool = tool;
      if (this.gesture?.kind === "create") this.cancel();
      this.clearMeasure();
    }
    this.setScroll(tool === "select" && this.gesture === null);
  }

  private clearMeasure() {
    if (!this.primitive.measureState) return;
    this.primitive.setTransient({ measure: null });
    this.cb.measured(null);
  }

  private setScroll(enabled: boolean) {
    this.chart.applyOptions({ handleScroll: enabled, handleScale: enabled });
  }

  private local(event: { clientX: number; clientY: number }): Pt {
    const rect = this.el.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  /** Inside the price pane's plotting area (not an axis, not another pane)? */
  private inPricePane(p: Pt): boolean {
    const pane = this.chart.panes()[0];
    const height = pane ? pane.getHeight() : this.el.clientHeight;
    const axis = this.chart.priceScale("right").width();
    return p.x >= 0 && p.y >= 0 && p.y <= height && p.x <= this.el.clientWidth - axis;
  }

  private anchorAt(p: Pt, snap = true): DrawingAnchor | null {
    const times = this.primitive.current.times;
    const logical = this.chart.timeScale().coordinateToLogical(p.x);
    const price = this.series.coordinateToPrice(p.y);
    if (logical === null || price === null) return null;
    const l = snap ? Math.round(logical) : logical;
    const t = logicalToTime(times, l);
    return t === null ? null : { t: Math.round(t), price };
  }

  private hitTest(p: Pt): { id: string; handle: number | null } | null {
    const selected = this.primitive.current.selectedId;
    // Handles of the selected drawing first, then bodies, topmost first.
    for (const layout of this.primitive.layouts) {
      if (layout.id !== selected) continue;
      const i = layout.handles.findIndex((h) => Math.hypot(h.x - p.x, h.y - p.y) <= HANDLE_PX);
      if (i >= 0) return { id: layout.id, handle: i };
    }
    for (let i = this.primitive.layouts.length - 1; i >= 0; i -= 1) {
      const layout = this.primitive.layouts[i] as Layout;
      if (layout.hit(p)) return { id: layout.id, handle: null };
    }
    return null;
  }

  private draftFor(tool: DrawingTool, a: DrawingAnchor, b: DrawingAnchor): Drawing | null {
    if (tool === "select" || tool === "measure") return null;
    const controls = this.cb.controls();
    const one = tool === "hline" || tool === "vline" || tool === "text";
    return {
      id: "draft",
      kind: tool,
      points: one ? [fromAnchor(a)] : [fromAnchor(a), fromAnchor(b)],
      timeframe: controls.scope,
      text: "",
      locked: false,
      hidden: false,
      fib_extensions: false,
    };
  }

  private onDown(event: PointerEvent) {
    if (event.button !== 0) return;
    const p = this.local(event);
    if (!this.inPricePane(p)) return;
    const controls = this.cb.controls();
    const g = this.gesture;
    // Second click of a click-click drawing: finish it.
    if (g && g.kind === "create" && g.clickMode) {
      this.claim(event);
      this.finishCreate(this.anchorAt(p) ?? g.a);
      return;
    }
    const tool = controls.tool;
    if (tool !== "select") {
      if (tool !== "measure" && !controls.canEdit) return;
      const a = this.anchorAt(p);
      if (!a) return;
      this.claim(event);
      if (tool === "measure") {
        this.primitive.setTransient({ measure: { a, b: a } });
        this.gesture = { kind: "create", tool, start: p, a, clickMode: false };
        return;
      }
      if (tool === "hline" || tool === "vline" || tool === "text") {
        this.gesture = { kind: "create", tool, start: p, a, clickMode: false };
        this.primitive.setTransient({ draft: this.draftFor(tool, a, a) });
        return;
      }
      this.gesture = { kind: "create", tool, start: p, a, clickMode: false };
      this.primitive.setTransient({ draft: this.draftFor(tool, a, a) });
      return;
    }
    // Select mode: grab one of the operator's own drawings, or let the chart pan.
    this.clearMeasure();
    const hit = this.hitTest(p);
    if (!hit) {
      if (this.primitive.current.selectedId !== null) this.cb.select(null);
      return;
    }
    const origin = this.cb.allDrawings().find((d) => d.id === hit.id);
    if (!origin) return;
    this.cb.select(hit.id);
    if (!controls.canEdit || controls.lockedAll || origin.locked) {
      // Selected, but not movable: still claim so the click does not pan.
      this.claim(event);
      this.gesture = null;
      this.release();
      return;
    }
    this.claim(event);
    this.gesture = { kind: "move", id: hit.id, start: p, origin, handle: hit.handle, moved: false };
  }

  private claim(event: PointerEvent) {
    this.claimed = true;
    event.stopPropagation();
    event.preventDefault();
    this.setScroll(false);
    try {
      this.el.setPointerCapture(event.pointerId);
    } catch {
      // Synthetic events in tests may not be capturable; the window listener still ends the gesture.
    }
  }

  private release() {
    this.claimed = false;
    this.sync();
  }

  private onMove(event: PointerEvent) {
    const g = this.gesture;
    if (!g) return;
    const p = this.local(event);
    if (g.kind === "create") {
      const b = this.anchorAt(p);
      if (!b) return;
      if (g.tool === "measure") {
        this.primitive.setTransient({ measure: { a: g.a, b } });
        return;
      }
      if (g.tool === "hline" || g.tool === "vline" || g.tool === "text") {
        this.primitive.setTransient({ draft: this.draftFor(g.tool, b, b) });
        g.a = b;
        return;
      }
      this.primitive.setTransient({ draft: this.draftFor(g.tool, g.a, b) });
      return;
    }
    // Moving: shift whole bars and raw price, so anchors stay on bar opens.
    const times = this.primitive.current.times;
    const ts = this.chart.timeScale();
    const l0 = ts.coordinateToLogical(g.start.x);
    const l1 = ts.coordinateToLogical(p.x);
    const p0 = this.series.coordinateToPrice(g.start.y);
    const p1 = this.series.coordinateToPrice(p.y);
    if (l0 === null || l1 === null || p0 === null || p1 === null) return;
    const dl = Math.round(l1 - l0);
    const dp = p1 - p0;
    if (dl === 0 && Math.abs(p.y - g.start.y) < 1) return;
    g.moved = true;
    const shift = (pt: { t: string; price: number }, k: number) => {
      if (g.handle !== null && g.handle !== k) return pt;
      const a = toAnchor(pt);
      const l = timeToLogical(times, a.t);
      const t = l === null ? a.t : (logicalToTime(times, Math.round(l) + dl) ?? a.t);
      const kind = g.origin.kind;
      return fromAnchor({
        t: kind === "hline" ? a.t : Math.round(t),
        price: kind === "vline" ? a.price : a.price + dp,
      });
    };
    const moved: Drawing = { ...g.origin, points: g.origin.points.map(shift) };
    this.primitive.setTransient({ draft: moved, hideId: g.id });
  }

  private onUp(event: PointerEvent) {
    const g = this.gesture;
    if (!g) {
      if (this.claimed) this.release();
      return;
    }
    const p = this.local(event);
    if (g.kind === "create") {
      const far = Math.hypot(p.x - g.start.x, p.y - g.start.y) > 5;
      const two = !(g.tool === "hline" || g.tool === "vline" || g.tool === "text");
      if (two && !far && !g.clickMode) {
        // A click, not a drag: the next click sets the second point.
        g.clickMode = true;
        this.claimed = false;
        return;
      }
      this.finishCreate(this.anchorAt(p) ?? g.a);
      return;
    }
    // Move finished.
    const draft = this.primitive.scope()?.transient.draft ?? null;
    this.gesture = null;
    this.primitive.setTransient({ draft: null, hideId: null });
    this.release();
    if (g.moved && draft) {
      const next = this.cb.allDrawings().map((d) => (d.id === g.id ? { ...draft, id: g.id } : d));
      this.cb.commit(next, g.id);
    }
  }

  private finishCreate(b: DrawingAnchor) {
    const g = this.gesture;
    if (!g || g.kind !== "create") return;
    this.gesture = null;
    if (g.tool === "measure") {
      this.primitive.setTransient({ measure: { a: g.a, b } });
      this.release();
      this.cb.measured(this.describeMeasure({ a: g.a, b }));
      return;
    }
    const one = g.tool === "hline" || g.tool === "vline" || g.tool === "text";
    const draft = this.draftFor(g.tool, one ? b : g.a, b);
    this.primitive.setTransient({ draft: null });
    this.release();
    if (!draft) return;
    if (g.tool === "text") {
      const at = draft.points[0];
      const px = at ? this.pxOf(toAnchor(at)) : null;
      if (at) this.cb.requestText(at, px ?? g.start, null);
      this.cb.toolDone();
      return;
    }
    const drawing: Drawing = { ...draft, id: newDrawingId() };
    this.cb.commit([...this.cb.allDrawings(), drawing], drawing.id);
    this.cb.toolDone();
  }

  private describeMeasure(m: MeasureState): string {
    const r = measure(this.primitive.current.times, m.a, m.b);
    const decimals = this.primitive.current.priceDecimals;
    const delta = formatNumber(Math.abs(r.priceDelta), "", { decimals });
    const sign = r.priceDelta > 0 ? "+" : r.priceDelta < 0 ? "−" : "";
    const pct =
      r.percent === null ? "" : ` (${r.percent > 0 ? "+" : r.percent < 0 ? "−" : ""}${Math.abs(r.percent).toFixed(2)}%)`;
    return `Measured ${sign}${delta}${pct} over ${r.bars} bars, ${formatSpan(r.seconds)}. Display only; not saved.`;
  }

  private pxOf(a: DrawingAnchor): Pt | null {
    const logical = timeToLogical(this.primitive.current.times, a.t);
    if (logical === null) return null;
    const x = this.chart.timeScale().logicalToCoordinate(logical as Logical);
    const y = this.series.priceToCoordinate(a.price);
    return x === null || y === null ? null : { x, y };
  }

  private onDouble(event: MouseEvent) {
    const p = this.local(event);
    const hit = this.hitTest(p);
    if (!hit) return;
    const d = this.cb.allDrawings().find((x) => x.id === hit.id);
    const controls = this.cb.controls();
    if (!d || d.kind !== "text" || !controls.canEdit || d.locked || controls.lockedAll) return;
    event.stopPropagation();
    const at = d.points[0];
    if (at) this.cb.requestText(at, this.pxOf(toAnchor(at)) ?? p, d.id);
  }
}
