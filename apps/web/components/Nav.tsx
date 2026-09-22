"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

/**
 * Six sections, not fourteen.
 *
 * V1 shipped 14 top-level nav items for 5 real jobs, four competing surfaces
 * answering "what should I promote?", and six routes to approve a bot. Here
 * each section is one job, and each page inside it is one question within that
 * job. There is exactly one Candidates page and exactly one promote action.
 */
export const SECTIONS: {
  title: string;
  job: string;
  links: { href: string; label: string }[];
}[] = [
  {
    title: "Command",
    job: "Is the platform healthy and is anything on fire?",
    links: [
      { href: "/", label: "Overview" },
      { href: "/market-pulse", label: "Market Pulse" },
      { href: "/alerts", label: "Alerts" },
    ],
  },
  {
    title: "Research",
    job: "What have we tried, and what does the evidence say?",
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
    title: "Markets",
    job: "What is the market doing and can we trust the data?",
    links: [
      { href: "/markets", label: "Explorer" },
      { href: "/markets/regimes", label: "Regimes" },
      { href: "/markets/correlations", label: "Correlations" },
      { href: "/markets/data-quality", label: "Data Quality" },
    ],
  },
  {
    title: "Trading",
    job: "What should be promoted, and what is the book doing?",
    links: [
      { href: "/trading/candidates", label: "Candidates" },
      { href: "/trading/portfolio", label: "Portfolio" },
      { href: "/trading/exposure", label: "Exposure" },
      { href: "/trading/risk", label: "Risk" },
      { href: "/trading/execution", label: "Execution" },
    ],
  },
  {
    title: "Intelligence",
    job: "What did the agents do, and can we prove it?",
    links: [
      { href: "/intelligence/agents", label: "Agents" },
      { href: "/intelligence/runs", label: "Runs" },
      { href: "/intelligence/research-memory", label: "Research Memory" },
    ],
  },
  {
    title: "System",
    job: "Is every service, worker and feed actually up?",
    links: [
      { href: "/system/services", label: "Services" },
      { href: "/system/workers", label: "Workers" },
      { href: "/system/data-health", label: "Data Health" },
      { href: "/system/broker-health", label: "Broker Health" },
      { href: "/system/logs", label: "Logs" },
      { href: "/system/settings", label: "Settings" },
    ],
  },
];

export function Nav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  // Close the drawer when the route changes, adjusting state during render
  // rather than in an effect: an effect leaves the drawer covering the new page
  // for a frame, which at 390px is the whole screen.
  const [lastPath, setLastPath] = useState(pathname);
  if (lastPath !== pathname) {
    setLastPath(pathname);
    if (open) setOpen(false);
  }

  return (
    <>
      <button
        type="button"
        className="nav-toggle"
        data-testid="nav-toggle"
        aria-expanded={open}
        aria-controls="primary-nav"
        onClick={() => setOpen((v) => !v)}
      >
        ☰ Sections
      </button>
      <div
        className="nav-scrim"
        data-open={open}
        data-testid="nav-scrim"
        onClick={() => setOpen(false)}
      />
      <nav
        id="primary-nav"
        className="nav"
        data-open={open}
        data-testid="primary-nav"
        aria-label="Primary"
      >
        {SECTIONS.map((section) => (
          <div className="nav__section-group" key={section.title}>
            <div className="nav__section">
              <div className="nav__section-title">{section.title}</div>
            </div>
            {section.links.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="nav__link"
                aria-current={pathname === link.href ? "page" : undefined}
              >
                {link.label}
              </Link>
            ))}
          </div>
        ))}
      </nav>
    </>
  );
}
