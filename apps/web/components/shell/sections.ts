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
  links: { href: string; label: string }[];
}

export const SECTIONS: Section[] = [
  {
    id: "command",
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
    title: "Fleet & Positions",
    question: "What is every bot doing and what is open?",
    icon: Crosshair,
    links: [{ href: "/trading/portfolio", label: "Portfolio" }],
  },
  {
    id: "lifecycle",
    title: "Strategy Lifecycle",
    question: "What should be promoted, demoted or retired?",
    icon: ArrowUpDown,
    links: [{ href: "/trading/candidates", label: "Candidates" }],
  },
  {
    id: "research",
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
  },
  {
    id: "journal",
    title: "Journal",
    question: "What did we trade, why, and what did we learn?",
    icon: NotebookPen,
    links: [{ href: "/trading/execution", label: "Trades" }],
  },
];

export const ALL_ROUTES = SECTIONS.flatMap((s) => s.links.map((l) => l.href));

/** The section a path belongs to: exact match, else the longest route prefix. */
export function sectionFor(pathname: string): Section | null {
  const exact = SECTIONS.find((s) => s.links.some((l) => l.href === pathname));
  if (exact) return exact;
  let best: { section: Section; length: number } | null = null;
  for (const section of SECTIONS) {
    for (const link of section.links) {
      if (link.href !== "/" && pathname.startsWith(`${link.href}/`)) {
        if (best === null || link.href.length > best.length) {
          best = { section, length: link.href.length };
        }
      }
    }
  }
  return best?.section ?? null;
}
