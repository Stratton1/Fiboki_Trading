import {
  ArrowUpDown,
  LayoutGrid,
  Crosshair,
  Database,
  FlaskConical,
  House,
  NotebookPen,
  Radar,
  Server,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";

/**
 * Nine screens, each answering one question with one primary action
 * (plan §4), mapped onto the routes that exist today (report E §5.1). Every
 * existing route keeps its URL; this only decides which section it lives in.
 * Each route belongs to exactly one section.
 */
export interface Section {
  id: string;
  title: string;
  question: string;
  icon: LucideIcon;
  /** The `g <chord>` key that goes to this section's first view. */
  chord: string;
  /** The function-key label of the section (the command line's codes for each view are in CommandLine.tsx). */
  mnemonic: string;
  /** Each view: its route, tab label, command-line code and one-line description. */
  links: { href: string; label: string; code: string; description: string }[];
  /**
   * Entity routes that belong to the section without being one of its views
   * (`/lifecycle/<hash>`, `/system/incidents/<id>`): the header and rail
   * place them here; they are not listed as tabs.
   */
  entityPrefixes?: string[];
}

export const SECTIONS: Section[] = [
  {
    id: "command",
    mnemonic: "CMD",
    chord: "c",
    title: "Command",
    question: "Is anything wrong, and what needs me now?",
    icon: House,
    links: [
      { href: "/", label: "Overview", code: "CMD", description: "Money strip, attention queue, fleet and loss limits." },
      { href: "/market-pulse", label: "Market Pulse", code: "PULSE", description: "Spreads and activity across the universe." },
    ],
  },
  {
    id: "fleet",
    mnemonic: "FLT",
    chord: "f",
    title: "Fleet & Positions",
    question: "What is every bot doing and what is open?",
    icon: Crosshair,
    links: [
      { href: "/trading/fleet", label: "Fleet", code: "FLT", description: "Every paper bot: state, last decision, positions, P&L; attribution." },
      { href: "/trading/portfolio", label: "Portfolio", code: "PORT", description: "The paper book: balance, equity curve and open positions." },
    ],
  },
  {
    id: "lifecycle",
    mnemonic: "LIF",
    chord: "l",
    title: "Strategy Lifecycle",
    question: "What should be promoted, demoted or retired?",
    icon: ArrowUpDown,
    links: [
      { href: "/trading/candidates", label: "Candidates", code: "LIF", description: "Strategies proposed for promotion, with their evidence." },
    ],
    entityPrefixes: ["/lifecycle"],
  },
  {
    id: "research",
    mnemonic: "LAB",
    chord: "r",
    title: "Research Lab",
    question: "What have we tried and what does the evidence say?",
    icon: FlaskConical,
    links: [
      { href: "/research", label: "Lab", code: "LAB", description: "Research overview." },
      { href: "/research/hypotheses", label: "Hypotheses", code: "HYP", description: "Hypotheses and the evidence against them." },
      { href: "/research/experiments", label: "Experiments", code: "XPR", description: "Experiments run and queued." },
      { href: "/research/strategies", label: "Strategies", code: "STR", description: "Strategy documents and their runs." },
      { href: "/research/parameter-lab", label: "Parameter Lab", code: "PARAM", description: "Parameter spaces per strategy." },
      { href: "/research/validation", label: "Validation", code: "VAL", description: "Validation ladder reports, pass and fail." },
      { href: "/research/datasets", label: "Datasets", code: "DSET", description: "Dataset versions and lineage." },
      { href: "/research/runs", label: "Runs", code: "RUNS", description: "Every stored run; tick two to six to compare." },
      { href: "/research/compare", label: "Compare", code: "CMP", description: "Runs side by side: equity, stats, correlation." },
      { href: "/research/walk-forward", label: "Walk-forward", code: "WF", description: "In-sample and out-of-sample folds per strategy." },
      { href: "/research/jobs", label: "Jobs", code: "JOBS", description: "Research jobs and study shards in flight." },
      { href: "/research/correlations", label: "Correlations", code: "CORR", description: "Return correlation across the universe." },
    ],
  },
  {
    id: "markets",
    mnemonic: "MKT",
    chord: "m",
    title: "Market Intelligence",
    question: "What is the market doing, and what did the agents conclude?",
    icon: Radar,
    links: [
      { href: "/markets", label: "Explorer", code: "MKT", description: "The instrument universe." },
      { href: "/markets/regimes", label: "Regimes", code: "REGM", description: "Market regime per instrument." },
      { href: "/markets/correlations", label: "Correlations", code: "CORR", description: "Correlation matrix." },
      { href: "/intelligence/agents", label: "Agents", code: "AGT", description: "Agent roles and capabilities." },
      { href: "/intelligence/runs", label: "Agent runs", code: "RUNS", description: "Agent run history." },
      { href: "/intelligence/research-memory", label: "Research Memory", code: "MEM", description: "What was tried and how it died." },
    ],
  },
  {
    id: "risk",
    mnemonic: "RSK",
    chord: "x",
    title: "Risk & Exposure",
    question: "How close are we to any limit?",
    icon: ShieldAlert,
    links: [
      { href: "/trading/risk", label: "Risk", code: "RSK", description: "Limit board, kill switch and gateway." },
      { href: "/trading/exposure", label: "Exposure", code: "EXPO", description: "Exposure matrix by instrument, currency and strategy." },
    ],
  },
  {
    id: "data",
    mnemonic: "DQ",
    chord: "d",
    title: "Data Quality",
    question: "Can we trust the inputs?",
    icon: Database,
    links: [
      { href: "/markets/data-quality", label: "Market data", code: "DQ", description: "Dataset quality per instrument." },
      { href: "/system/data-health", label: "Data Health", code: "DH", description: "Data stores on the System board." },
    ],
  },
  {
    id: "system",
    mnemonic: "SYS",
    chord: "s",
    title: "System & Incidents",
    question: "Is every process up, and what happened?",
    icon: Server,
    links: [
      { href: "/system", label: "Board", code: "SYS", description: "Every service, worker, bot, broker and check, one clock." },
      { href: "/alerts", label: "Alerts", code: "ALRT", description: "Alert rules, their state, last fired and history." },
      { href: "/system/logs", label: "Audit log", code: "LOG", description: "The hash-chained operator audit trail." },
      { href: "/system/settings", label: "Settings", code: "SET", description: "Process configuration." },
      { href: "/system/legend", label: "Legend", code: "LEG", description: "Colour, chip and glyph legend." },
      { href: "/system/functions", label: "Functions", code: "FUNC", description: "Every command-line function and key." },
    ],
    // The old system pages are views of the board (`/system/services` opens it
    // at services); they keep their URLs but are not tabs.
    entityPrefixes: ["/system/incidents", "/system/services", "/system/workers", "/system/broker-health"],
  },
  {
    id: "journal",
    mnemonic: "JRN",
    chord: "j",
    title: "Journal",
    question: "What did we trade, why, and what did we learn?",
    icon: NotebookPen,
    links: [
      { href: "/trading/execution", label: "Trades", code: "JRN", description: "Every closed trade, from every source." },
    ],
  },
  // wave workspace: the tenth section (F10). Contiguous block; keep together.
  {
    id: "workspace",
    mnemonic: "WS",
    chord: "w",
    title: "Workspace & Monitors",
    question: "What do I want on screen, side by side, right now?",
    icon: LayoutGrid,
    links: [
      { href: "/workspace", label: "Workspace", code: "WS", description: "Tiling panels, colour link groups, named layouts (LAYOUT <name>)." },
    ],
  },
  // end wave workspace
];

/** `g <key>` → the section's first view (plan §4: "g x chords per screen"). */
export const CHORDS: Readonly<Record<string, { href: string; title: string }>> = Object.fromEntries(
  SECTIONS.map((s) => [s.chord, { href: s.links[0]?.href ?? "/", title: s.title }]),
);

/** The section a path belongs to: exact match, else the longest route or entity prefix. */
export function sectionFor(pathname: string): Section | null {
  const exact = SECTIONS.find((s) => s.links.some((l) => l.href === pathname));
  if (exact) return exact;
  let best: { section: Section; length: number } | null = null;
  for (const section of SECTIONS) {
    const prefixes = [...section.links.map((link) => link.href), ...(section.entityPrefixes ?? [])];
    for (const href of prefixes) {
      if (href !== "/" && pathname.startsWith(`${href}/`)) {
        if (best === null || href.length > best.length) {
          best = { section, length: href.length };
        }
      }
    }
  }
  return best?.section ?? null;
}
