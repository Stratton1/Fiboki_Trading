"use client";

import { Menu } from "lucide-react";
import { createContext, useContext, useState, type ReactNode } from "react";

/**
 * The navigation drawer's open state, shared by the rail (which IS the drawer
 * below 1024 px) and the page header (which holds the button that opens it).
 *
 * The Sections button used to float fixed above the status bar, where on a
 * phone it covered the last line of whatever was under it: the attention
 * queue on Command, the chart on /markets/<symbol> (inventory F-10). It now
 * sits at the start of the page header row, in the document flow, and covers
 * nothing.
 */

const NavDrawerContext = createContext<{ open: boolean; setOpen: (open: boolean) => void }>({
  open: false,
  setOpen: () => {},
});

export function NavDrawerProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return <NavDrawerContext.Provider value={{ open, setOpen }}>{children}</NavDrawerContext.Provider>;
}

export function useNavDrawer() {
  return useContext(NavDrawerContext);
}

/** The Sections button: shown below 1024 px only (globals.css `.nav-toggle`). */
export function NavToggle() {
  const { open, setOpen } = useNavDrawer();
  return (
    <button
      type="button"
      className="nav-toggle"
      data-testid="nav-toggle"
      aria-expanded={open}
      aria-controls="primary-nav"
      onClick={() => setOpen(!open)}
    >
      <Menu size={16} aria-hidden="true" />
      Sections
    </button>
  );
}
