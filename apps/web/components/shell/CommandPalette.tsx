"use client";

import { Dialog as Base } from "@base-ui/react/dialog";
import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { DialogContent, DialogTitle } from "../ui/Dialog";
import { Kbd } from "../ui/Kbd";
import { openCommand } from "./commands";
import { SECTIONS } from "./sections";

/**
 * The command palette (⌘K), on cmdk inside the owned Base UI dialog (focus
 * trap, inert background, focus returned on close). Loaded on first ⌘K.
 *
 * Three groups, and nothing in any of them changes the platform:
 *  - Go to: every view, by section, with its `g` chord;
 *  - Open by id: what you typed, as a trade, strategy, instrument, audit
 *    entry or parameter space; the target screen selects that row (the
 *    platform decides whether it exists, the palette does not guess);
 *    "Open chart: SYMBOL" opens that instrument's chart workstation;
 *  - Actions: each OPENS a dialog. "Halt trading…" opens the kill-switch
 *    dialog exactly as ⇧K does; the arm still needs a choice and a reason.
 */
export default function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const [search, setSearch] = useState("");
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (open) setSearch("");
  }

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };
  const run = (command: "kill-switch" | "shortcuts") => {
    onOpenChange(false);
    // After the palette's close has started, so focus returns first.
    setTimeout(() => openCommand(command), 0);
  };

  const id = search.trim();
  const byId: { key: string; label: string; href: string }[] =
    id.length === 0
      ? []
      : [
          {
            key: "trade",
            label: `Open trade ${id}`,
            href: `/trading/execution?row=${encodeURIComponent(id)}`,
          },
          {
            key: "strategy",
            label: `Open strategy ${id}`,
            href: `/trading/candidates?row=${encodeURIComponent(id)}`,
          },
          {
            key: "instrument",
            label: `Open instrument ${id.toUpperCase()}`,
            href: `/markets?row=${encodeURIComponent(id.toUpperCase())}`,
          },
          {
            key: "chart",
            label: `Open chart: ${id.toUpperCase()}`,
            href: `/markets/${encodeURIComponent(id.toUpperCase())}`,
          },
          {
            key: "parameters",
            label: `Open the parameter space of ${id}`,
            href: `/research/parameter-lab?strategy=${encodeURIComponent(id)}`,
          },
          ...(/^\d+$/.test(id)
            ? [
                {
                  key: "audit",
                  label: `Open audit entry #${id}`,
                  href: `/system/logs?row=${encodeURIComponent(id)}`,
                },
              ]
            : []),
        ];

  return (
    <Base.Root open={open} onOpenChange={(next) => onOpenChange(next)}>
      <DialogContent testId="command-palette" size="md" className="palette">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command label="Command palette" loop className="palette__root">
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Go to a screen, open an id, or run an action…"
            className="palette__input"
            data-testid="palette-input"
          />
          <Command.List className="palette__list" data-testid="palette-list">
            <Command.Empty className="palette__empty">Nothing matches.</Command.Empty>
            <Command.Group heading="Actions (each opens a dialog)" className="palette__group">
              <Command.Item
                value="halt trading kill switch pause flatten"
                onSelect={() => run("kill-switch")}
                className="palette__item"
                data-testid="palette-action-kill-switch"
              >
                <span>Halt trading… (opens the kill-switch dialog)</span>
                <span className="palette__hint">
                  <Kbd>⇧</Kbd>
                  <Kbd>K</Kbd>
                </span>
              </Command.Item>
              <Command.Item
                value="keyboard shortcuts help"
                onSelect={() => run("shortcuts")}
                className="palette__item"
                data-testid="palette-action-shortcuts"
              >
                <span>Keyboard shortcuts</span>
                <span className="palette__hint">
                  <Kbd>?</Kbd>
                </span>
              </Command.Item>
            </Command.Group>
            {SECTIONS.map((section) => (
              <Command.Group key={section.id} heading={section.title} className="palette__group">
                {section.links.map((link, index) => (
                  <Command.Item
                    key={link.href}
                    value={`go ${section.title} ${link.label} ${link.href}`}
                    onSelect={() => go(link.href)}
                    className="palette__item"
                    data-testid={`palette-go-${link.href}`}
                  >
                    <span>{link.label}</span>
                    {index === 0 ? (
                      <span className="palette__hint">
                        <Kbd>g</Kbd>
                        <Kbd>{section.chord}</Kbd>
                      </span>
                    ) : null}
                  </Command.Item>
                ))}
              </Command.Group>
            ))}
            {byId.length > 0 ? (
              <Command.Group heading="Open by id" className="palette__group" forceMount>
                {byId.map((item) => (
                  <Command.Item
                    key={item.key}
                    value={`open ${item.key} ${id}`}
                    forceMount
                    onSelect={() => go(item.href)}
                    className="palette__item"
                    data-testid={`palette-open-${item.key}`}
                  >
                    {item.label}
                  </Command.Item>
                ))}
              </Command.Group>
            ) : null}
          </Command.List>
        </Command>
      </DialogContent>
    </Base.Root>
  );
}
