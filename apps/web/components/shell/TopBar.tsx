"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { openCommand } from "./commands";
import { SECTIONS, sectionFor } from "./sections";

/**
 * The terminal's top: a command line and a row of function keys.
 *
 *   FIBOKI ▸ [ RSK, EURUSD, "audit"…  <GO> ]
 *   F1 CMD │ F2 FLT │ F3 LIF │ F4 LAB │ F5 MKT │ F6 RSK │ F7 DQ │ F8 SYS │ F9 JRN
 *
 * Both only NAVIGATE or OPEN a dialog, exactly as the palette and the `g`
 * chords do; nothing here changes anything on the platform (plan §4).
 *
 * The command line takes a mnemonic (every view has one, sections.ts), a
 * word from a view's name, or an instrument symbol (opens that chart, as the
 * palette's "Open chart" does; the platform decides whether it exists). `?`
 * opens the shortcut sheet, `KILL` opens the kill-switch dialog (it never
 * arms), and an empty Enter opens the full palette. `/` focuses the line.
 * F1 to F9 (except F5, which stays the browser's reload) go to a section.
 */

interface Entry {
  code: string;
  label: string;
  section: string;
  href: string;
}

const ENTRIES: Entry[] = SECTIONS.flatMap((section) =>
  section.links.map((link) => ({
    code: link.mnemonic,
    label: link.label,
    section: section.title,
    href: link.href,
  })),
);

const SYMBOL = /^[A-Za-z0-9._/-]{2,16}$/;

export function matchEntries(query: string): Entry[] {
  const q = query.trim().toLowerCase();
  if (q === "") return [];
  const score = (entry: Entry): number => {
    const code = entry.code.toLowerCase();
    if (code === q) return 0;
    if (code.startsWith(q)) return 1;
    if (entry.label.toLowerCase().startsWith(q)) return 2;
    if (`${entry.section} ${entry.label}`.toLowerCase().includes(q)) return 3;
    return 9;
  };
  return ENTRIES.map((entry) => ({ entry, rank: score(entry) }))
    .filter((x) => x.rank < 9)
    .sort((a, b) => a.rank - b.rank)
    .slice(0, 6)
    .map((x) => x.entry);
}

function inDialog(): boolean {
  return (
    document.querySelector(
      '[role="dialog"][data-open]:not([data-ending-style]), [role="alertdialog"][data-open]:not([data-ending-style])',
    ) !== null
  );
}

function CommandLine() {
  const router = useRouter();
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const matches = useMemo(() => matchEntries(text), [text]);
  const showList = open && matches.length > 0;
  const current = Math.min(active, Math.max(0, matches.length - 1));

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key !== "/" || inDialog()) return;
      const el = event.target;
      if (el instanceof HTMLElement && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))) {
        return;
      }
      event.preventDefault();
      input.current?.focus();
      input.current?.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const reset = () => {
    setText("");
    setOpen(false);
    setActive(0);
  };

  const run = (entry: Entry | null) => {
    const raw = text.trim();
    if (entry) {
      reset();
      input.current?.blur();
      router.push(entry.href);
      return;
    }
    const word = raw.toUpperCase();
    if (word === "") {
      openCommand("palette");
      return;
    }
    if (word === "?" || word === "HELP") {
      reset();
      openCommand("shortcuts");
      return;
    }
    if (word === "KILL" || word === "HALT") {
      reset();
      openCommand("kill-switch");
      return;
    }
    if (SYMBOL.test(raw)) {
      reset();
      input.current?.blur();
      router.push(`/markets/${encodeURIComponent(word)}`);
      return;
    }
    // Nothing recognised: hand the text to the full palette's search.
    openCommand("palette");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" && matches.length > 0) {
      event.preventDefault();
      setOpen(true);
      setActive((current + 1) % matches.length);
    } else if (event.key === "ArrowUp" && matches.length > 0) {
      event.preventDefault();
      setActive((current - 1 + matches.length) % matches.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      // An exact mnemonic wins over the highlighted suggestion's ranking.
      const exact = matches.find((m) => m.code.toLowerCase() === text.trim().toLowerCase());
      run(exact ?? (showList ? (matches[current] ?? null) : (matches[0] ?? null)));
    } else if (event.key === "Escape") {
      if (text !== "" || open) {
        event.preventDefault();
        reset();
      } else {
        input.current?.blur();
      }
    }
  };

  return (
    <div className="cmdline" data-testid="command-line">
      <label className="cmdline__prompt" htmlFor={`${listId}-input`}>
        <span aria-hidden="true">▸</span>
        <span className="sr-only">Command line</span>
      </label>
      <input
        ref={input}
        id={`${listId}-input`}
        type="text"
        className="cmdline__input"
        data-testid="command-line-input"
        placeholder="Mnemonic or symbol: RSK, LAB, EURUSD  ( / to focus, ? help )"
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showList ? `${listId}-${current}` : undefined}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      <button
        type="button"
        className="cmdline__go"
        data-testid="command-line-go"
        tabIndex={-1}
        // mousedown, not click: the input's blur would close the list first.
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => run(showList ? (matches[current] ?? null) : (matches[0] ?? null))}
      >
        GO
      </button>
      <ul
        id={listId}
        role="listbox"
        aria-label="Matching screens"
        className="cmdline__list"
        hidden={!showList}
        data-testid="command-line-list"
      >
        {matches.map((entry, index) => (
          <li
            key={entry.href}
            id={`${listId}-${index}`}
            role="option"
            aria-selected={index === current}
            className="cmdline__option"
            data-active={index === current}
            onMouseDown={(event) => {
              event.preventDefault();
              run(entry);
            }}
          >
            <span className="cmdline__code">{entry.code}</span>
            <span className="cmdline__label">{entry.label}</span>
            <span className="cmdline__section">{entry.section}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function FunctionKeys() {
  const pathname = usePathname();
  const router = useRouter();
  const active = sectionFor(pathname);
  const nav = useRef<HTMLElement>(null);
  // One tab stop for the whole row (the current section's key, else the
  // first); arrows, Home and End move between keys. Nine more tab stops
  // before the rail and the page would be nine more for every keyboard user.
  const tabStop = SECTIONS.findIndex((section) => section === active);

  const onNavKey = (event: KeyboardEvent<HTMLElement>) => {
    const links = Array.from(nav.current?.querySelectorAll<HTMLAnchorElement>("a.fkey") ?? []);
    const at = links.indexOf(document.activeElement as HTMLAnchorElement);
    if (at < 0) return;
    const next =
      event.key === "ArrowRight"
        ? (at + 1) % links.length
        : event.key === "ArrowLeft"
          ? (at - 1 + links.length) % links.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? links.length - 1
              : -1;
    if (next < 0) return;
    event.preventDefault();
    links[next]?.focus();
  };

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) {
        return;
      }
      const match = /^F([1-9])$/.exec(event.key);
      if (!match || match[1] === "5" || inDialog()) return;
      const section = SECTIONS[Number(match[1]) - 1];
      const first = section?.links[0];
      if (!first) return;
      event.preventDefault();
      router.push(first.href);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  return (
    <nav
      ref={nav}
      className="fkeys"
      aria-label="Section keys"
      data-testid="function-keys"
      onKeyDown={onNavKey}
    >
      {SECTIONS.map((section, index) => {
        const first = section.links[0];
        if (!first) return null;
        return (
          <Link
            key={section.id}
            href={first.href}
            className="fkey"
            data-active={section === active}
            data-testid={`fkey-${section.id}`}
            tabIndex={index === Math.max(0, tabStop) ? 0 : -1}
            aria-current={section === active ? "true" : undefined}
            aria-description={`${section.title}. ${section.question}`}
            title={`${section.title}${index === 4 ? " (F5 stays the browser's reload; click)" : ""}`}
          >
            <span className="fkey__k">F{index + 1}</span>
            <span className="fkey__l">{section.mnemonic}</span>
          </Link>
        );
      })}
    </nav>
  );
}

export function TopBar() {
  return (
    <header className="topbar" data-testid="topbar">
      <div className="topbar__line">
        <Link href="/" className="topbar__brand" aria-label="Fiboki, go to Command" tabIndex={-1}>
          FIBOKI
        </Link>
        <CommandLine />
      </div>
      <FunctionKeys />
    </header>
  );
}
