"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import "./research.css";

const VIEWS = [
  { href: "/research/runs", label: "Runs" },
  { href: "/research/compare", label: "Compare" },
  { href: "/research/walk-forward", label: "Walk-forward" },
  { href: "/research/jobs", label: "Job queue" },
  { href: "/research/correlations", label: "Correlations" },
];

/**
 * The research analysis views, one row, under the section's own tabs. The
 * shell's section tabs (components/shell/sections.ts) list the lab's record
 * views; these are the analytics over that record. Entity pages (a run's
 * tearsheet, a strategy) light up their parent view.
 */
export function ResearchNav() {
  const path = usePathname();
  return (
    <nav className="rs-nav" aria-label="Research analysis">
      <span className="rs-nav__label">Analysis</span>
      {VIEWS.map((v) => {
        const current = path === v.href || path.startsWith(`${v.href}/`);
        return (
          <Link key={v.href} href={v.href} className="rs-nav__link" aria-current={current ? "page" : undefined}>
            {v.label}
          </Link>
        );
      })}
    </nav>
  );
}
