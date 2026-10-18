"use client";

import { Dialog as Base } from "@base-ui/react/dialog";
import { Command } from "cmdk";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { pushHistory } from "@/lib/history";
import type { Hit } from "@/lib/search";
import { DialogContent, DialogTitle } from "../ui/Dialog";
import { Kbd } from "../ui/Kbd";
import { openCommand } from "./commands";
import { SCREEN_FUNCTIONS } from "./functions";
import { useOperator } from "./platform";
import { KIND_LABEL, useSuggestions } from "./suggest";

/**
 * The command palette (⌘K), on cmdk inside the owned Base UI dialog (focus
 * trap, inert background, focus returned on close). Loaded on first ⌘K.
 *
 * It searches what EXISTS: screens and functions from the registry, the
 * platform's instruments, and strategies, bots, lifecycle entities, runs and
 * trades from GET /api/search, ranked once, exact match first (lib/search.ts),
 * so "EURUSD" puts the instrument EURUSD first, never a screen. cmdk's own
 * fuzzy filter is off: the order on screen is that ranking. Typed text that
 * matches nothing offers nothing; the palette never invents an entity.
 *
 * Actions OPEN a dialog only. "Halt trading…" opens the kill-switch dialog
 * exactly as ⇧K does; the arm still needs a choice and a reason.
 */
export default function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const router = useRouter();
  const operator = useOperator();
  const [search, setSearch] = useState("");
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (open) setSearch("");
  }
  const { hits, pending } = useSuggestions(search, open, 14);

  const go = (hit: Hit) => {
    pushHistory(operator?.user_id, hit.id);
    onOpenChange(false);
    router.push(hit.href);
  };
  const run = (command: "kill-switch" | "shortcuts") => {
    onOpenChange(false);
    // After the palette's close has started, so focus returns first.
    setTimeout(() => openCommand(command), 0);
  };

  const typing = search.trim() !== "";

  return (
    <Base.Root open={open} onOpenChange={(next) => onOpenChange(next)}>
      <DialogContent testId="command-palette" size="md" className="palette">
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <Command label="Command palette" loop shouldFilter={false} className="palette__root">
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Instrument, strategy, bot, run, trade, screen or code…"
            className="palette__input"
            data-testid="palette-input"
          />
          <Command.List className="palette__list" data-testid="palette-list">
            {typing && hits.length === 0 && !pending ? (
              <p className="palette__empty" data-testid="palette-empty">
                Nothing matches &ldquo;{search.trim()}&rdquo;. Codes are listed under FUNC.
              </p>
            ) : null}
            {typing && hits.length > 0 ? (
              <Command.Group heading="Best matches" className="palette__group">
                {hits.map((hit, index) => (
                  <Command.Item
                    key={`${hit.kind}:${hit.id}`}
                    value={`${index}:${hit.kind}:${hit.id}`}
                    onSelect={() => go(hit)}
                    className="palette__item"
                    data-testid="palette-hit"
                    data-kind={hit.kind}
                    data-id={hit.id}
                    data-tier={hit.tier}
                  >
                    <span className="palette__kind">{KIND_LABEL[hit.kind]}</span>
                    <span className="palette__label">
                      <span className="mono">{hit.id}</span>
                      {hit.label !== hit.id ? <span> {hit.label}</span> : null}
                    </span>
                    <span className="palette__hint palette__detail">{hit.detail}</span>
                  </Command.Item>
                ))}
              </Command.Group>
            ) : null}
            {typing ? null : (
              <Command.Group heading="Screens" className="palette__group">
                {SCREEN_FUNCTIONS.map((fn) => (
                  <Command.Item
                    key={fn.href}
                    value={`go ${fn.code}`}
                    onSelect={() =>
                      go({ kind: "screen", id: fn.code, label: fn.label, detail: fn.description, href: fn.href, tier: 0 })
                    }
                    className="palette__item"
                    data-testid={`palette-go-${fn.href}`}
                  >
                    <span className="palette__kind">{fn.code}</span>
                    <span className="palette__label">
                      {fn.label} <span className="muted">· {fn.section}</span>
                    </span>
                    {fn.keys.length > 0 ? (
                      <span className="palette__hint">
                        {fn.keys.map((key) => (
                          <Kbd key={key}>{key}</Kbd>
                        ))}
                      </span>
                    ) : null}
                  </Command.Item>
                ))}
              </Command.Group>
            )}
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
          </Command.List>
        </Command>
      </DialogContent>
    </Base.Root>
  );
}
