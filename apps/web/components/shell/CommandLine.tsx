"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { openCommand } from "./commands";
import { SECTIONS } from "./sections";

/**
 * The command line's engine: matching, the listbox and what Enter does, and
 * the bar's global keys. Split from TopBar and loaded right after hydration,
 * which keeps the shell's first-load JavaScript inside its budget.
 */

interface Entry {
  code: string;
  label: string;
  section: string;
  href: string;
}

/** The command-line code of every view. */
const CODES: Record<string, string> = {
  "/": "CMD",
  "/market-pulse": "PULSE",
  "/alerts": "ALRT",
  "/trading/portfolio": "FLT",
  "/trading/candidates": "LIF",
  "/research": "LAB",
  "/research/hypotheses": "HYP",
  "/research/experiments": "XPR",
  "/research/strategies": "STR",
  "/research/parameter-lab": "PARAM",
  "/research/validation": "VAL",
  "/research/datasets": "DSET",
  "/markets": "MKT",
  "/markets/regimes": "REGM",
  "/markets/correlations": "CORR",
  "/intelligence/agents": "AGT",
  "/intelligence/runs": "RUNS",
  "/intelligence/research-memory": "MEM",
  "/trading/risk": "RSK",
  "/trading/exposure": "EXPO",
  "/markets/data-quality": "DQ",
  "/system/data-health": "DH",
  "/system/services": "SYS",
  "/system/workers": "WRK",
  "/system/broker-health": "BRK",
  "/system/logs": "LOG",
  "/system/settings": "SET",
  "/system/legend": "LEG",
  "/trading/execution": "JRN",
};

const ENTRIES: Entry[] = SECTIONS.flatMap((section) =>
  section.links.map((link) => ({
    code: CODES[link.href] ?? link.label.toUpperCase(),
    label: link.label,
    section: section.title,
    href: link.href,
  })),
);

/** True while a dialog is open (one in its exit transition is closing, not open). */
function dialogOpen(): boolean {
  return (
    document.querySelector(
      '[role="dialog"][data-open]:not([data-ending-style]), [role="alertdialog"][data-open]:not([data-ending-style])',
    ) !== null
  );
}

const SYMBOL = /^[A-Za-z0-9._/-]{2,16}$/;

function matchEntries(query: string): Entry[] {
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
    <div className="cmdline" data-testid="command-line" data-ready="true">
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

/**
 * Publishes the mode banner's real height as `--banner-h`, so the command line
 * and the rail stick flush beneath it even when the banner wraps to several
 * lines on a phone. The banner is in the document flow, so the body's size
 * changes whenever the banner's does, whichever of its states it is showing.
 */
function useBannerHeight() {
  useEffect(() => {
    const root = document.documentElement;
    const publish = () => {
      const banner = document.querySelector<HTMLElement>(".mode-banner");
      if (banner) root.style.setProperty("--banner-h", `${banner.offsetHeight}px`);
    };
    publish();
    const observer = new ResizeObserver(publish);
    observer.observe(document.body);
    return () => observer.disconnect();
  }, []);
}

/**
 * The bar's global keys, installed once the engine has loaded:
 *  - `/` focuses the command line from anywhere that is not a field;
 *  - F1 to F9 go to a section (F5 is left to the browser's reload);
 *  - arrows, Home and End move along the function-key row, which is one tab
 *    stop so nine more do not sit between the skip link and the page.
 */
function TopBarKeys() {
  const router = useRouter();
  useBannerHeight();
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      const row = target instanceof HTMLElement ? target.closest(".fkeys") : null;
      if (row) {
        const links = Array.from(row.querySelectorAll<HTMLAnchorElement>("a.fkey"));
        const at = links.indexOf(target as HTMLAnchorElement);
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
        if (at >= 0 && next >= 0) {
          event.preventDefault();
          links[next]?.focus();
          return;
        }
      }
      if (dialogOpen()) return;
      if (event.key === "/") {
        if (
          target instanceof HTMLElement &&
          (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))
        ) {
          return;
        }
        const input = document.querySelector<HTMLInputElement>('[data-testid="command-line-input"]');
        if (!input) return;
        event.preventDefault();
        input.focus();
        input.select();
        return;
      }
      const match = event.shiftKey ? null : /^F([1-9])$/.exec(event.key);
      const first = match && match[1] !== "5" ? SECTIONS[Number(match[1]) - 1]?.links[0] : undefined;
      if (first) {
        event.preventDefault();
        router.push(first.href);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);
  return null;
}

/** What TopBar loads after hydration: the command line and the bar's keys. */
export default function TopBarEngine() {
  return (
    <>
      <CommandLine />
      <TopBarKeys />
    </>
  );
}
