/**
 * WCAG 2.2 contrast check over the design tokens in app/globals.css.
 *
 * Parses the token blocks (`:root`, `:root[data-theme="light"]`, and the two
 * `data-pnl="cvd"` presets), converts every OKLCH value to sRGB, composites
 * translucent layers over the surface they sit on, and computes the WCAG
 * relative-luminance contrast ratio for each foreground/background pair that
 * the interface actually uses.
 *
 *   text      pairs need 4.5:1 (WCAG 1.4.3, AA, normal-size text)
 *   graphics  pairs need 3:1   (WCAG 1.4.11: focus rings, control borders,
 *                                mode frames, chart series)
 *   exempt    pairs are reported only (WCAG exempts disabled controls)
 *
 * OKLCH → sRGB uses Björn Ottosson's published matrices. An out-of-gamut
 * colour is resolved two ways, by CSS Color 4 chroma reduction and by naive
 * per-channel clipping (what some engines still do), and the LOWER of the two
 * ratios is the one checked, so the result holds whichever the browser does.
 *
 * Usage:
 *   node scripts/contrast.mjs            # table; exit 1 on any failure
 *   node scripts/contrast.mjs --markdown # the same table as Markdown
 *   node scripts/contrast.mjs --suggest  # for failures, the L that passes
 *   node scripts/contrast.mjs --self-test
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS_PATH = join(HERE, "..", "app", "globals.css");

// ------------------------------------------------------------ colour maths

/** OKLCH (L 0..1, C, H degrees) to linear sRGB, unclamped. */
function oklchToLinear([L, C, H]) {
  const h = (H * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

const inGamut = (rgb) => rgb.every((v) => v >= -1e-4 && v <= 1 + 1e-4);
const clamp01 = (v) => Math.min(1, Math.max(0, v));

/** CSS Color 4 style: reduce chroma until the colour fits sRGB. */
function gamutMapLinear(lch) {
  if (lch[0] >= 1) return [1, 1, 1];
  if (lch[0] <= 0) return [0, 0, 0];
  let rgb = oklchToLinear(lch);
  if (inGamut(rgb)) return rgb.map(clamp01);
  let lo = 0;
  let hi = lch[1];
  for (let i = 0; i < 40; i += 1) {
    const mid = (lo + hi) / 2;
    rgb = oklchToLinear([lch[0], mid, lch[2]]);
    if (inGamut(rgb)) lo = mid;
    else hi = mid;
  }
  return oklchToLinear([lch[0], lo, lch[2]]).map(clamp01);
}

const clipLinear = (lch) => oklchToLinear(lch).map(clamp01);

const encode = (v) => (v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055);
const decode = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);

/** Composite a stack of {lch, alpha} layers (bottom first) in gamma-encoded sRGB, as CSS does. */
function composite(layers, toLinear) {
  let out = [0, 0, 0];
  for (const layer of layers) {
    const top = toLinear(layer.lch).map(encode);
    const a = layer.alpha;
    out = out.map((v, i) => a * top[i] + (1 - a) * v);
  }
  return out.map(decode);
}

const luminance = ([r, g, b]) => 0.2126 * r + 0.7152 * g + 0.0722 * b;

function ratio(fgLinear, bgLinear) {
  const a = luminance(fgLinear);
  const b = luminance(bgLinear);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

// ------------------------------------------------------------ token parsing

function blocks(css) {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(stripped)) !== null) {
    // A selector may be preceded by a statement such as `@import "…";`.
    const selector = m[1].slice(m[1].lastIndexOf(";") + 1);
    out.push({ selector: selector.trim().replace(/\s+/g, " "), body: m[2] });
  }
  return out;
}

function declarations(body) {
  const out = {};
  for (const part of body.split(";")) {
    const i = part.indexOf(":");
    if (i < 0) continue;
    const name = part.slice(0, i).trim();
    if (!name.startsWith("--")) continue;
    out[name] = part.slice(i + 1).trim();
  }
  return out;
}

export function themes(css = readFileSync(CSS_PATH, "utf8")) {
  const all = blocks(css);
  const pick = (selector) =>
    Object.assign(
      {},
      ...all.filter((b) => b.selector === selector).map((b) => declarations(b.body)),
    );
  const dark = pick(":root");
  const light = { ...dark, ...pick(':root[data-theme="light"]') };
  return {
    dark,
    light,
    "dark+cvd": { ...dark, ...pick(':root[data-pnl="cvd"]') },
    "light+cvd": { ...light, ...pick(':root[data-theme="light"][data-pnl="cvd"]') },
  };
}

function parseColour(value) {
  const m = /^oklch\(\s*([\d.]+%?)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+%?))?\s*\)$/.exec(value);
  if (!m) throw new Error(`not an oklch() colour: ${value}`);
  const num = (s) => (s.endsWith("%") ? parseFloat(s) / 100 : parseFloat(s));
  return { lch: [num(m[1]), num(m[2]), num(m[3])], alpha: m[4] === undefined ? 1 : num(m[4]) };
}

function resolve(tokens, ref, seen = new Set()) {
  if (!ref.startsWith("--")) return parseColour(ref);
  if (seen.has(ref)) throw new Error(`cycle at ${ref}`);
  seen.add(ref);
  const raw = tokens[ref];
  if (raw === undefined) throw new Error(`unknown token ${ref}`);
  const v = /^var\((--[\w-]+)\)$/.exec(raw);
  return v ? resolve(tokens, v[1], seen) : parseColour(raw);
}

// ------------------------------------------------------------------ pairs

const SURFACES = ["--bg-canvas", "--bg-sunken", "--bg-surface", "--bg-raised", "--bg-overlay"];
const PANELS = ["--bg-canvas", "--bg-surface", "--bg-raised"];
const on = (fg, bgs, kind, note = "") => bgs.map((bg) => ({ fg, bg: [bg].flat(), kind, note }));

/** Every pair the interface uses. `bg` is a stack, bottom first. */
export const PAIRS = [
  ...["--fg", "--fg-muted", "--fg-subtle"].flatMap((fg) => on(fg, SURFACES, "text")),
  ...on("--fg-disabled", ["--bg-canvas", "--bg-surface"], "exempt", "disabled control"),
  ...on("--accent", PANELS, "text", "link"),
  ...["--pnl-up", "--pnl-down"].flatMap((fg) =>
    on(fg, [...PANELS, "--bg-sunken"], "text", "P&L figure"),
  ),
  ...on("--pnl-down", [["--bg-surface", "--pnl-down-bg"]], "text", "live-tick flash"),
  ...on("--pnl-up", [["--bg-surface", "--pnl-up-bg"]], "text", "live-tick flash"),
  ...["--ok", "--warn", "--critical", "--unknown"].flatMap((fg) =>
    on(fg, [...PANELS, "--bg-sunken"], "text", "health label"),
  ),
  ...on("--critical", [["--bg-surface", "--critical-bg"]], "text", "error state title"),
  ...on("--fg-muted", [["--bg-surface", "--critical-bg"]], "text", "error state body"),
  ...on("--prov-sim", [...PANELS, "--bg-sunken"], "text", "hollow chip"),
  { fg: "--prov-paper-on", bg: ["--prov-paper"], kind: "text", note: "PAPER chip" },
  { fg: "--prov-shadow-on", bg: ["--prov-shadow"], kind: "text", note: "SHADOW chip" },
  { fg: "--prov-demo-on", bg: ["--prov-demo"], kind: "text", note: "DEMO chip" },
  {
    fg: "--prov-demo-on",
    bg: ["--prov-demo", "--prov-demo-hatch"],
    kind: "text",
    note: "DEMO chip hatch stripe",
  },
  { fg: "--prov-live-on", bg: ["--prov-live"], kind: "text", note: "LIVE chip" },
  ...["--mode-backtest", "--mode-paper", "--mode-shadow", "--mode-demo", "--mode-live"].flatMap(
    (fg) => on(fg, ["--bg-canvas", "--bg-sunken"], "text", "mode label / status bar"),
  ),
  ...[
    ["--mode-backtest", "--mode-backtest-bg"],
    ["--mode-paper", "--mode-paper-bg"],
    ["--mode-shadow", "--mode-shadow-bg"],
  ].flatMap(([mode, tint]) => [
    { fg: mode, bg: ["--bg-canvas", tint], kind: "text", note: "banner mode label" },
    { fg: "--fg-muted", bg: ["--bg-canvas", tint], kind: "text", note: "banner detail" },
  ]),
  {
    fg: "--fg-muted",
    bg: ["--bg-canvas", "--mode-demo-hatch"],
    kind: "text",
    note: "demo banner detail (hatch)",
  },
  {
    fg: "--fg",
    bg: ["--bg-canvas", "--mode-demo-hatch"],
    kind: "text",
    note: "demo banner headline (hatch)",
  },
  { fg: "--fg-on-strong", bg: ["--mode-live-fill"], kind: "text", note: "live banner (inverted)" },
  {
    fg: "--critical",
    bg: ["--bg-sunken", "oklch(0.5 0 0 / 0.08)"],
    kind: "text",
    note: "MODE UNKNOWN label",
  },
  {
    fg: "--fg-muted",
    bg: ["--bg-sunken", "oklch(0.5 0 0 / 0.08)"],
    kind: "text",
    note: "unknown banner detail",
  },
  { fg: "--fg", bg: ["--bg-sunken", "--accent-bg"], kind: "text", note: "selected choice" },
  ...on("--focus-ring", SURFACES, "graphics", "focus ring"),
  ...on(
    "--border-control",
    ["--bg-canvas", "--bg-sunken", "--bg-surface", "--bg-raised"],
    "graphics",
    "control boundary",
  ),
  ...["--mode-paper", "--mode-shadow", "--mode-demo", "--mode-live", "--mode-unknown"].flatMap(
    (fg) => on(fg, ["--bg-canvas"], "graphics", "mode frame"),
  ),
  ...["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6"].flatMap(
    (fg) => on(fg, ["--bg-surface"], "graphics", "chart series"),
  ),
];

const PNL_TOKENS = new Set(["--pnl-up", "--pnl-down"]);
const TARGET = { text: 4.5, graphics: 3, exempt: 0 };

function layersFor(tokens, stack) {
  return stack.map((ref) => resolve(tokens, ref));
}

function evaluate(tokens, pair) {
  const fg = resolve(tokens, pair.fg);
  const bgLayers = layersFor(tokens, pair.bg);
  const results = [gamutMapLinear, clipLinear].map((toLinear) => {
    const bg = composite(bgLayers, toLinear);
    const fgLin = fg.alpha < 1 ? composite([...bgLayers, fg], toLinear) : toLinear(fg.lch);
    return ratio(fgLin, bg);
  });
  return Math.min(...results);
}

/** The L (keeping C and H) at which `pair` first passes, searching away from the background. */
function suggest(tokens, pair) {
  const fg = resolve(tokens, pair.fg);
  const target = TARGET[pair.kind];
  const bgL = resolve(tokens, pair.bg[0]).lch[0];
  const dir = bgL < 0.5 ? 1 : -1;
  for (let step = 0; step <= 100; step += 1) {
    const L = +(fg.lch[0] + dir * step * 0.005).toFixed(3);
    if (L < 0 || L > 1) break;
    const t = { ...tokens, [pair.fg]: `oklch(${L} ${fg.lch[1]} ${fg.lch[2]})` };
    if (evaluate(t, { ...pair, fg: pair.fg }) >= target) return L;
  }
  return null;
}

export function run() {
  const all = themes();
  const rows = [];
  for (const [theme, tokens] of Object.entries(all)) {
    const cvd = theme.endsWith("+cvd");
    for (const pair of PAIRS) {
      // CVD presets only change P&L tokens; report only the rows they affect.
      if (cvd && !PNL_TOKENS.has(pair.fg)) continue;
      const value = evaluate(tokens, pair);
      const target = TARGET[pair.kind];
      rows.push({
        theme,
        ...pair,
        ratio: value,
        target,
        pass: value >= target,
        suggestion: value >= target ? null : suggest(tokens, pair),
      });
    }
  }
  return rows;
}

function selfTest() {
  const white = [1, 0, 0];
  const black = [0, 0, 0];
  const r = ratio(gamutMapLinear(white), gamutMapLinear(black));
  if (Math.abs(r - 21) > 0.01) throw new Error(`white/black should be 21:1, got ${r}`);
  const grey = ratio(gamutMapLinear([0.5, 0, 0]), gamutMapLinear(white));
  // OKLCH L 0.5 grey is sRGB ~#636363, which is 5.9:1 on white.
  if (Math.abs(grey - 5.9) > 0.15)
    throw new Error(`L0.5 grey on white should be ~5.9:1, got ${grey}`);
  const half = composite(
    [parseColour("oklch(1 0 0)"), parseColour("oklch(0 0 0 / 0.5)")],
    gamutMapLinear,
  );
  if (Math.abs(encode(half[0]) - 0.5) > 1e-6)
    throw new Error("50% black over white should be 50% grey");
  console.log("self-test ok");
}

const fmt = (bg) => bg.join(" + ");

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv.includes("--self-test")) {
    selfTest();
  } else {
    selfTest();
    const rows = run();
    const markdown = process.argv.includes("--markdown");
    const header = markdown
      ? [
          "| Theme | Foreground | Background (bottom + top) | Use | Ratio | Needs | Result |",
          "|---|---|---|---|---:|---:|---|",
        ]
      : [];
    const lines = rows.map((r) => {
      const result = r.kind === "exempt" ? "exempt" : r.pass ? "pass" : "FAIL";
      return markdown
        ? `| ${r.theme} | \`${r.fg}\` | \`${fmt(r.bg)}\` | ${r.note || r.kind} | ${r.ratio.toFixed(2)} | ${r.target || "-"} | ${result} |`
        : `${r.theme.padEnd(10)} ${r.fg.padEnd(18)} on ${fmt(r.bg).padEnd(44)} ${r.ratio.toFixed(2).padStart(6)} (${r.kind} ${r.target || "-"}) ${result}${
            r.suggestion !== null && process.argv.includes("--suggest")
              ? `  -> L ${r.suggestion}`
              : ""
          }`;
    });
    console.log([...header, ...lines].join("\n"));
    const failures = rows.filter((r) => r.kind !== "exempt" && !r.pass);
    const checked = rows.filter((r) => r.kind !== "exempt").length;
    console.log(
      `\n${checked - failures.length}/${checked} checked pairs pass; ${rows.length - checked} exempt.`,
    );
    if (failures.length > 0) process.exitCode = 1;
  }
}
