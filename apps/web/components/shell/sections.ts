import {
  ArrowUpDown,
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
  links: { href: string; label: string }[];
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
    chord: "c",
    title: "Command",
    question: "Is anything wrong, and what needs me now?",
    icon: House,
    links: [
      { href: "/", label: "Overview" },
      { href: "/market-pulse", label: "Market Pulse" },
      { href: "/alerts", label: "Alerts" },
    ],
  },
  {
    id: "fleet",
    chord: "f",
    title: "Fleet & Positions",
    question: "What is every bot doing and what is open?",
    icon: Crosshair,
    links: [{ href: "/trading/portfolio", label: "Portfolio" }],
  },
  {
    id: "lifecycle",
    chord: "l",
    title: "Strategy Lifecycle",
    question: "What should be promoted, demoted or retired?",
    icon: ArrowUpDown,
    links: [{ href: "/trading/candidates", label: "Candidates" }],
    entityPrefixes: ["/lifecycle"],
  },
  {
    id: "research",
    chord: "r",
    title: "Research Lab",
    question: "What have we tried and what does the evidence say?",
    icon: FlaskConical,
    links: [
      { href: "/research", label: "Lab" },
      { href: "/research/hypotheses", label: "Hypotheses" },
      { href: "/research/experiments", label: "Experiments" },
      { href: "/research/strategies", label: "Strategies" },
      { href: "/research/parameter-lab", label: "Parameter Lab" },
      { href: "/research/validation", label: "Validation" },
      { href: "/research/datasets", label: "Datasets" },
    ],
  },
  {
    id: "markets",
    chord: "m",
    title: "Market Intelligence",
    question: "What is the market doing, and what did the agents conclude?",
    icon: Radar,
    links: [
      { href: "/markets", label: "Explorer" },
      { href: "/markets/regimes", label: "Regimes" },
      { href: "/markets/correlations", label: "Correlations" },
      { href: "/intelligence/agents", label: "Agents" },
      { href: "/intelligence/runs", label: "Agent runs" },
      { href: "/intelligence/research-memory", label: "Research Memory" },
    ],
  },
  {
    id: "risk",
    chord: "x",
    title: "Risk & Exposure",
    question: "How close are we to any limit?",
    icon: ShieldAlert,
    links: [
      { href: "/trading/risk", label: "Risk" },
      { href: "/trading/exposure", label: "Exposure" },
    ],
  },
  {
    id: "data",
    chord: "d",
    title: "Data Quality",
    question: "Can we trust the inputs?",
    icon: Database,
    links: [
      { href: "/markets/data-quality", label: "Market data" },
      { href: "/system/data-health", label: "Data Health" },
    ],
  },
  {
    id: "system",
    chord: "s",
    title: "System & Incidents",
    question: "Is every process up, and what happened?",
    icon: Server,
    links: [
      { href: "/system/services", label: "Services" },
      { href: "/system/workers", label: "Workers" },
      { href: "/system/broker-health", label: "Broker Health" },
      { href: "/system/logs", label: "Audit log" },
      { href: "/system/settings", label: "Settings" },
      { href: "/system/legend", label: "Legend" },
    ],
    entityPrefixes: ["/system/incidents"],
  },
  {
    id: "journal",
    chord: "j",
    title: "Journal",
    question: "What did we trade, why, and what did we learn?",
    icon: NotebookPen,
    links: [{ href: "/trading/execution", label: "Trades" }],
  },
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
