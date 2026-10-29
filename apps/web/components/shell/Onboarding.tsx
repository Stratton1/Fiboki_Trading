"use client";

import { Dialog as Base } from "@base-ui/react/dialog";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "../ui/Button";
import { DialogContent, DialogDescription, DialogTitle } from "../ui/Dialog";

export interface GuideStep {
  id: string;
  kicker: string;
  nav: string;
  title: string;
  href?: string;
  hrefLabel?: string;
  points: string[];
}

/**
 * How the desk is actually used. Copy stays inside what the platform does:
 * a missing figure is missing, the two books do not add, agents do not execute.
 */
export const GUIDE_STEPS: readonly GuideStep[] = [
  {
    id: "mode",
    kicker: "01",
    nav: "Mode",
    title: "Read the banner before the numbers",
    points: [
      "The strip across the top is the execution mode. PAPER means this process writes the paper book.",
      "A stale banner is a warning. The mode it shows may be older than the page.",
      "Live execution is compiled out of this build. The banner cannot be switched to live from here.",
    ],
  },
  {
    id: "numbers",
    kicker: "02",
    nav: "Figures",
    title: "A blank figure is not zero",
    points: [
      "Every number is a labelled figure: provenance, unit, and caveats. Open the caveat mark beside it.",
      "“no data” means the platform refused to invent a value. Do not read it as nought.",
      "The status bar’s “data as of” is the oldest view on this screen. Click it to see each one.",
    ],
  },
  {
    id: "books",
    kicker: "03",
    nav: "Books",
    title: "Two books, kept apart",
    href: "/",
    hrefLabel: "Open Command",
    points: [
      "The crypto paper book is in pounds. The FX paper book is in dollars. They are not one account.",
      "Combined equity stays “no data” while the currencies differ. No conversion is applied.",
      "Abandoned positions are named. Their unrealised P&L was not carried, and a later trade does not clear that line.",
    ],
  },
  {
    id: "attention",
    kicker: "04",
    nav: "Attention",
    title: "Attention is a queue, not a score",
    href: "/",
    hrefLabel: "Open Command",
    points: [
      "Needs attention is grouped by severity. Critical and error stay open. Warning and info can fold.",
      "The abandon warning stays listed. Hiding a group does not acknowledge it.",
      "Incidents further down the page are history. They are not the same list as the queue.",
    ],
  },
  {
    id: "command",
    kicker: "05",
    nav: "Command line",
    title: "The command line is the fast path",
    points: [
      "Press / to focus it. CMD, FLT, RSK, AGT, MEM open screens. EURUSD opens that chart. EURUSD H4 or EURUSD BT adds a function.",
      "↑ walks the last 50 commands. FUNC lists every code. An unknown word does not navigate.",
      "KILL opens the kill-switch dialog and does not arm it. GUIDE reopens this walkthrough.",
    ],
  },
  {
    id: "sections",
    kicker: "06",
    nav: "Sections",
    title: "Nine sections, one question each",
    points: [
      "The rail is Command, Fleet, Lifecycle, Research, Markets, Risk, Data, System, Journal.",
      "g then a letter jumps to a section. F1 to F9 do the same, except F5, which reloads the browser.",
      "On a phone, Sections opens the drawer. Tab stays inside it. Escape closes it and returns to that button.",
    ],
  },
  {
    id: "chart",
    kicker: "07",
    nav: "Chart",
    title: "The chart draws what the platform sent",
    href: "/markets/EURUSD",
    hrefLabel: "Open EURUSD",
    points: [
      "Candles and indicator series are computed on the platform. The browser does not invent a value.",
      "Drawings are yours. Undo restores them. Measure is display only and is not saved.",
      "A disabled run picker names the reason, usually that no run is stored for this symbol and timeframe.",
    ],
  },
  {
    id: "research",
    kicker: "08",
    nav: "Research",
    title: "Ask the ledger before the work",
    href: "/intelligence/research-memory",
    hrefLabel: "Open Research Memory",
    points: [
      "Research Memory answers from the experiment ledger. A missing rediscovery rate is not zero.",
      "“Not novel”, or a count of incomparable experiments, is a stop. Keyword similarity is estimated overlap.",
      "Strategies are ranked on the procedure, not on headline profit. The holdout is claimed once.",
    ],
  },
  {
    id: "agents",
    kicker: "09",
    nav: "Agents",
    title: "Agents annotate. They do not trade",
    href: "/intelligence/agents",
    hrefLabel: "Open Agent Desk",
    points: [
      "Every role says Can execute: NO. That capability is not one the desk can grant.",
      "A candidate awaiting review cannot be sent to paper from this page.",
      "One smoke call does not finish the 20-run acceptance protocol. The sentence on the desk says so.",
    ],
  },
  {
    id: "risk",
    kicker: "10",
    nav: "Risk",
    title: "Risk, then stop",
    href: "/trading/risk",
    hrefLabel: "Open Risk",
    points: [
      "The risk page is the limit board, the gateway, and the kill switch.",
      "Shift+K opens the dialog. You still choose pause or flatten and write a reason. There is no disarm key.",
      "This laptop is the paper host. Moving the book, a longer quote tape, and a new calendar version are separate decisions.",
    ],
  },
];

function Stage({ id }: { id: string }) {
  if (id === "mode") {
    return (
      <div className="guide-stage" aria-hidden="true">
        <div className="guide-banner">PAPER — simulated fills, no venue contacted</div>
        <div className="guide-stage__row">
          <span>mode</span>
          <span>stream</span>
          <span>worker</span>
          <span>as of</span>
        </div>
      </div>
    );
  }
  if (id === "numbers") {
    return (
      <div className="guide-stage" aria-hidden="true">
        <div className="guide-figures">
          <span>Equity <strong>no data</strong></span>
          <span>Day P&amp;L <strong>no data</strong></span>
          <span>Open <strong>0</strong></span>
        </div>
      </div>
    );
  }
  if (id === "books") {
    return (
      <div className="guide-stage guide-stage--split" aria-hidden="true">
        <div>
          <span>GBP</span>
          <strong>£ book</strong>
        </div>
        <div>
          <span>USD</span>
          <strong>$ book</strong>
        </div>
      </div>
    );
  }
  if (id === "attention") {
    return (
      <div className="guide-stage" aria-hidden="true">
        <div className="guide-queue">
          <span data-sev="critical">critical</span>
          <span data-sev="error">error</span>
          <span data-sev="warning">warning</span>
        </div>
      </div>
    );
  }
  if (id === "command") {
    return (
      <div className="guide-stage" aria-hidden="true">
        <div className="guide-line">
          <span>/</span> EURUSD H4
        </div>
      </div>
    );
  }
  if (id === "chart") {
    return (
      <div className="guide-stage" aria-hidden="true">
        <div className="guide-candles">
          <i /><i /><i /><i /><i /><i /><i />
        </div>
      </div>
    );
  }
  return (
    <div className="guide-stage" aria-hidden="true">
      <div className="guide-line">
        <span>g</span> then a letter · GUIDE · ⇧K
      </div>
    </div>
  );
}

export default function Onboarding({
  open,
  onOpenChange,
  startAt = 0,
  onNavigate,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Zero-based step to show when the dialog opens. */
  startAt?: number;
  /** Called before close when a step's own page is opened, so the next open can resume. */
  onNavigate?: (index: number) => void;
}) {
  const [index, setIndex] = useState(startAt);
  const step = GUIDE_STEPS[index] ?? GUIDE_STEPS[0]!;
  const last = index === GUIDE_STEPS.length - 1;

  useEffect(() => {
    if (!open) return;
    const clamped = Math.min(Math.max(0, startAt), GUIDE_STEPS.length - 1);
    setIndex(clamped);
  }, [open, startAt]);

  const close = () => onOpenChange(false);
  const next = () => setIndex((n) => Math.min(GUIDE_STEPS.length - 1, n + 1));
  const prev = () => setIndex((n) => Math.max(0, n - 1));

  return (
    <Base.Root open={open} onOpenChange={(nextOpen) => onOpenChange(nextOpen)}>
      <DialogContent
        testId="onboarding"
        size="lg"
        className="guide p-0!"
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") {
            event.preventDefault();
            next();
          } else if (event.key === "ArrowLeft") {
            event.preventDefault();
            prev();
          }
        }}
      >
        <div className="guide__layout">
          <ol className="guide__nav">
            {GUIDE_STEPS.map((item, i) => (
              <li key={item.id}>
                <button
                  type="button"
                  data-current={i === index ? "true" : undefined}
                  onClick={() => setIndex(i)}
                >
                  <span className="mono">{item.kicker}</span>
                  {item.nav}
                </button>
              </li>
            ))}
          </ol>
          <div className="guide__body">
            <p className="guide__kicker mono">
              {step.kicker} / {String(GUIDE_STEPS.length).padStart(2, "0")}
            </p>
            <DialogTitle>{step.title}</DialogTitle>
            <DialogDescription className="guide__lede">
              Ten steps. Nothing on this walkthrough places an order, sizes a position, or changes a limit.
            </DialogDescription>
            <Stage id={step.id} />
            <ul className="guide__points">
              {step.points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
            <div className="guide__actions">
              <Button variant="ghost" onClick={close}>
                Close
              </Button>
              <span className="guide__actions-end">
                {step.href ? (
                  <Link
                    href={step.href}
                    className="guide__open"
                    onClick={() => {
                      onNavigate?.(index);
                      close();
                    }}
                  >
                    {step.hrefLabel}
                  </Link>
                ) : null}
                <Button variant="secondary" onClick={prev} disabled={index === 0} aria-disabled={index === 0}>
                  Back
                </Button>
                {last ? (
                  <Button variant="primary" onClick={close}>
                    Done
                  </Button>
                ) : (
                  <Button variant="primary" onClick={next}>
                    Next
                  </Button>
                )}
              </span>
            </div>
          </div>
        </div>
      </DialogContent>
    </Base.Root>
  );
}
