"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { pushHistory, readHistory } from "@/lib/history";
import { route } from "@/lib/routes";
import type { Hit } from "@/lib/search";
import { openCommand } from "./commands";
import { INSTRUMENT_FUNCTIONS } from "./functions";
import { SessionClocks } from "./SessionClocks";
import { KIND_LABEL, useSuggestions } from "./suggest";
import { useOperator } from "./platform";
import { SECTIONS } from "./sections";

/**
 * The command line's engine: the grammar, suggestions, history and what Enter
 * does. Split from TopBar and loaded right after hydration, which keeps the
 * shell's first-load JavaScript inside its budget.
 *
 * Grammar (components/shell/functions.ts):
 *   RSK, FLT, LAB …          a screen code
 *   FUNC, HELP               the function directory
 *   KILL                     opens the kill-switch dialog (never arms)
 *   PAUSE|RESUME|DRAIN <bot>  opens that bot's control dialog (never acts)
 *   EURUSD                   the instrument's chart
 *   EURUSD H4 | BT | NEWS …  an instrument function
 *
 * Suggestions merge the registry with real entities (the platform's
 * instrument list, matched here, and GET /api/search for strategies, bots,
 * runs, trades), exact match first. Enter on text that is neither a code nor a
 * known symbol opens a suggestion only when it is exact, the only one, or one
 * the operator highlighted; otherwise the line says so and stays put. Unknown
 * text never navigates blindly.
 *
 * History: ↑ and ↓ on an empty or recalled line walk the last 50 commands,
 * kept per operator in this browser.
 */

/** True while a dialog is open (one in its exit transition is closing, not open). */
function dialogOpen(): boolean {
  return (
    document.querySelector(
      '[role="dialog"][data-open]:not([data-ending-style]), [role="alertdialog"][data-open]:not([data-ending-style])',
    ) !== null
  );
}

function CommandLine() {
  const router = useRouter();
  const operator = useOperator();
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [used, setUsed] = useState(false);
  const [active, setActive] = useState(0);
  const [chosen, setChosen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [recall, setRecall] = useState<number | null>(null);
  const { parsed, hits, pending } = useSuggestions(text, used);
  // Enter pressed while the instrument list or the search was still in
  // flight: run again once they have answered, never on half the facts.
  const queued = useRef(false);
  const showList = open && hits.length > 0;
  const current = Math.min(active, Math.max(0, hits.length - 1));

  const reset = () => {
    setText("");
    setOpen(false);
    setActive(0);
    setChosen(false);
    setRecall(null);
  };

  const go = (href: string, command: string) => {
    pushHistory(operator?.user_id, command);
    reset();
    setMessage(null);
    input.current?.blur();
    router.push(href);
  };

  const pick = (hit: Hit) => go(hit.href, hit.kind === "function" || hit.kind === "screen" ? hit.id : hit.id);

  const run = () => {
    const raw = text.trim();
    switch (parsed.kind) {
      case "empty":
        openCommand("palette");
        return;
      case "command":
        pushHistory(operator?.user_id, "KILL");
        reset();
        openCommand("kill-switch");
        return;
      case "control":
        // wave ops: open the bot's page with the act chosen; the dialog there
        // still needs a reason and a confirm. Without a bot: the fleet.
        if (parsed.session === null) {
          go(route.fleet(), parsed.verb);
          setMessage(`${parsed.verb} needs a bot: ${parsed.verb} <bot id> (the fleet lists them).`);
          return;
        }
        go(route.sessionDetail(parsed.session, parsed.verb.toLowerCase()), `${parsed.verb} ${parsed.session}`);
        return;
      case "alias":
        go(parsed.href, parsed.code);
        return;
      case "screen":
        go(parsed.fn.href, parsed.fn.code);
        return;
      case "instrument":
        if (parsed.fn === null) {
          setMessage(
            `${parsed.rest.toUpperCase()} is not a function for ${parsed.symbol}. Try ${INSTRUMENT_FUNCTIONS.map((f) => f.code).join(", ")}.`,
          );
          return;
        }
        go(parsed.fn.href(parsed.symbol), parsed.rest === "" ? parsed.symbol : `${parsed.symbol} ${parsed.fn.code}`);
        return;
      case "text": {
        if (pending) {
          queued.current = true;
          return;
        }
        const top = hits[current];
        if (top && (chosen || top.tier === 0 || hits.length === 1)) {
          pick(top);
          return;
        }
        setOpen(true);
        setMessage(
          hits.length === 0
            ? `Nothing matches "${raw}". Type a code (FUNC lists them) or a symbol the platform lists.`
            : `No exact match for "${raw}": choose one with ↓, or press Enter on it.`,
        );
      }
    }
  };

  const runLatest = useRef(() => {});
  useEffect(() => {
    runLatest.current = run;
  });
  useEffect(() => {
    if (!queued.current || pending) return;
    queued.current = false;
    runLatest.current();
  }, [pending]);

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" && showList) {
      event.preventDefault();
      // The first ↓ chooses the top suggestion; each later one the next.
      setActive(chosen ? (current + 1) % hits.length : 0);
      setChosen(true);
      return;
    }
    if (event.key === "ArrowUp" && showList && chosen) {
      event.preventDefault();
      setActive((current - 1 + hits.length) % hits.length);
      return;
    }
    if ((event.key === "ArrowUp" || event.key === "ArrowDown") && (text === "" || recall !== null)) {
      const history = readHistory(operator?.user_id);
      if (history.length === 0) return;
      event.preventDefault();
      const at = recall ?? history.length;
      const next = event.key === "ArrowUp" ? Math.max(0, at - 1) : at + 1;
      if (next >= history.length) {
        setRecall(null);
        setText("");
        return;
      }
      setRecall(next);
      setText(history[next] ?? "");
      setOpen(false);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (showList && chosen) {
        const hit = hits[current];
        if (hit) pick(hit);
        return;
      }
      run();
      return;
    }
    if (event.key === "Escape") {
      setMessage(null);
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
        placeholder="Code or symbol: RSK, FLT, EURUSD H4, EURUSD BT  ( / focus, ↑ history, FUNC list )"
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-describedby={message ? `${listId}-msg` : undefined}
        aria-activedescendant={showList && chosen ? `${listId}-${current}` : undefined}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setOpen(true);
          setActive(0);
          setChosen(false);
          setRecall(null);
          setMessage(null);
          queued.current = false;
        }}
        onFocus={() => {
          setUsed(true);
          setOpen(true);
        }}
        onBlur={() => {
          setOpen(false);
          setMessage(null);
        }}
        onKeyDown={onKeyDown}
      />
      <button
        type="button"
        className="cmdline__go"
        data-testid="command-line-go"
        tabIndex={-1}
        // mousedown, not click: the input's blur would close the list first.
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          if (showList && chosen && hits[current]) pick(hits[current]);
          else run();
        }}
      >
        GO
      </button>
      <ul
        id={listId}
        role="listbox"
        aria-label="Suggestions"
        className="cmdline__list"
        hidden={!showList}
        data-testid="command-line-list"
      >
        {hits.map((hit, index) => (
          <li
            key={`${hit.kind}:${hit.id}`}
            id={`${listId}-${index}`}
            role="option"
            aria-selected={chosen && index === current}
            className="cmdline__option"
            data-active={chosen && index === current}
            data-kind={hit.kind}
            data-tier={hit.tier}
            data-testid="command-line-option"
            onMouseDown={(event) => {
              event.preventDefault();
              pick(hit);
            }}
          >
            <span className="cmdline__code">{hit.id}</span>
            <span className="cmdline__label">{hit.label === hit.id ? "" : hit.label}</span>
            <span className="cmdline__section">
              <span className="cmdline__kind">{KIND_LABEL[hit.kind]}</span> {hit.detail}
            </span>
          </li>
        ))}
      </ul>
      {message ? (
        <p id={`${listId}-msg`} className="cmdline__msg" role="status" data-testid="command-line-message">
          {message}
        </p>
      ) : null}
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
 *  - F1 to F9 go to a section (F5 is left to the browser's reload); the rail
 *    shows each section's key.
 */
function TopBarKeys() {
  const router = useRouter();
  useBannerHeight();
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
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

/** What TopBar loads after hydration: the command line, the clocks and the bar's keys. */
export default function TopBarEngine() {
  return (
    <>
      <CommandLine />
      <SessionClocks />
      <TopBarKeys />
    </>
  );
}
