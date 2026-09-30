"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { NavToggle } from "./NavDrawer";
import { sectionFor } from "./sections";

/**
 * The page header slot: the section the page belongs to, the section's views
 * as navigation tabs, and a slot pages fill with <PageActions>.
 */

const SlotContext = createContext<{
  slot: HTMLElement | null;
  setSlot: (el: HTMLElement | null) => void;
}>({ slot: null, setSlot: () => {} });

export function PageHeaderProvider({ children }: { children: ReactNode }) {
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  return <SlotContext.Provider value={{ slot, setSlot }}>{children}</SlotContext.Provider>;
}

/** Render page-level actions into the header's action slot. */
export function PageActions({ children }: { children: ReactNode }) {
  const { slot } = useContext(SlotContext);
  return slot ? createPortal(children, slot) : null;
}

export function PageHeader() {
  const pathname = usePathname();
  const section = sectionFor(pathname);
  const { setSlot } = useContext(SlotContext);
  const Icon = section?.icon;
  return (
    <div className="page-header" data-testid="page-header" data-section={section?.id}>
      <NavToggle />
      {section && Icon ? (
        <span className="page-header__section" title={section.question}>
          <Icon size={14} aria-hidden="true" />
          {section.title}
        </span>
      ) : null}
      {section && section.links.length > 1 ? (
        <nav className="page-header__tabs" aria-label={`${section.title} views`}>
          {section.links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="page-header__tab"
              aria-current={pathname === link.href ? "page" : undefined}
            >
              {link.label}
            </Link>
          ))}
        </nav>
      ) : null}
      <div className="page-header__actions" ref={setSlot} data-testid="page-actions" />
    </div>
  );
}
