"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useState } from "react";
import { uiPrefs, useUiPrefs } from "@/lib/ui-prefs";
import { IconButton } from "../ui/IconButton";
import { Tooltip } from "../ui/Tooltip";
import { SECTIONS, sectionFor } from "./sections";

/**
 * The navigation rail: 56px of section icons, expanding to 232px with every
 * page listed under its section. Under 1024px it becomes a drawer showing the
 * expanded list.
 *
 * Nine sections map onto today's routes (sections.ts); every URL still works.
 * Inside a section the page header's view tabs reach the other pages, so the
 * collapsed rail loses nothing.
 */
export function Rail() {
  const pathname = usePathname();
  const { railExpanded } = useUiPrefs();
  const [open, setOpen] = useState(false);
  // Close the drawer when the route changes, adjusting state during render
  // rather than in an effect: an effect leaves the drawer covering the new page
  // for a frame, which at 390px is the whole screen.
  const [lastPath, setLastPath] = useState(pathname);
  if (lastPath !== pathname) {
    setLastPath(pathname);
    if (open) setOpen(false);
  }
  const active = sectionFor(pathname);

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
        <Menu size={16} aria-hidden="true" />
        Sections
      </button>
      <div
        className="nav-scrim"
        data-open={open}
        data-testid="nav-scrim"
        onClick={() => setOpen(false)}
      />
      <nav
        id="primary-nav"
        className="rail"
        data-open={open}
        data-testid="primary-nav"
        aria-label="Primary"
      >
        <div className="rail__icons" data-testid="rail-icons">
          {SECTIONS.map((section) => {
            const Icon = section.icon;
            const first = section.links[0];
            if (!first) return null;
            const isActive = section === active;
            return (
              <Tooltip key={section.id} label={section.title} side="right">
                <Link
                  href={first.href}
                  className="rail__icon"
                  aria-label={section.title}
                  data-active={isActive}
                  data-testid={`rail-${section.id}`}
                  aria-current={pathname === first.href ? "page" : isActive ? "true" : undefined}
                >
                  <Icon size={18} aria-hidden="true" strokeWidth={1.75} />
                </Link>
              </Tooltip>
            );
          })}
        </div>

        <div className="rail__tree">
          {SECTIONS.map((section) => {
            const Icon = section.icon;
            return (
              <div key={section.id} data-section={section.id}>
                <div className="rail__section">
                  <div className="rail__section-title">
                    <Icon size={14} aria-hidden="true" />
                    {section.title}
                  </div>
                </div>
                {section.links.map((link) => (
                  <Link
                    key={link.href}
                    href={link.href}
                    className="rail__link"
                    aria-current={pathname === link.href ? "page" : undefined}
                  >
                    {link.label}
                  </Link>
                ))}
              </div>
            );
          })}
        </div>

        <div className="rail__foot">
          <IconButton
            label={railExpanded ? "Collapse navigation" : "Expand navigation"}
            side="right"
            data-testid="rail-toggle"
            aria-expanded={railExpanded}
            onClick={() => uiPrefs.setRailExpanded(!railExpanded)}
          >
            {railExpanded ? (
              <PanelLeftClose size={16} aria-hidden="true" />
            ) : (
              <PanelLeftOpen size={16} aria-hidden="true" />
            )}
          </IconButton>
        </div>
      </nav>
    </>
  );
}
