"use client";

import {
  cloneElement,
  lazy,
  Suspense,
  useId,
  useState,
  type MouseEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import { loadPopoverLayer } from "./layers";

const PopoverLayer = lazy(loadPopoverLayer);

type TriggerProps = {
  onClick?: (event: MouseEvent<HTMLElement>) => void;
  onPointerEnter?: (event: never) => void;
  onFocus?: (event: never) => void;
};

/**
 * A click-, keyboard- and touch-openable panel anchored to a trigger. Use it
 * wherever information must be reachable without a mouse (caveats, "est",
 * MIXED counts, stale detail); a tooltip is not enough for those.
 *
 * The trigger is the caller's own element, never re-mounted, given
 * `aria-expanded`/`aria-haspopup`; the Base UI popup layer loads on first open
 * (see layers.ts). Focus moves into the popup on open and back to the trigger
 * on close; Escape and an outside press close it.
 */
export function Popover({
  trigger,
  title,
  children,
  side = "bottom",
  align = "start",
  className,
  testId,
}: {
  trigger: ReactElement<TriggerProps>;
  title?: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  className?: string;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const id = useId();
  const own = trigger.props;

  const triggerElement = cloneElement(trigger, {
    "aria-haspopup": "dialog",
    "aria-expanded": open,
    "aria-controls": open ? id : undefined,
    onPointerEnter: (event: never) => {
      own.onPointerEnter?.(event);
      void loadPopoverLayer();
    },
    onFocus: (event: never) => {
      own.onFocus?.(event);
      void loadPopoverLayer();
    },
    onClick: (event: MouseEvent<HTMLElement>) => {
      own.onClick?.(event);
      if (event.defaultPrevented) return;
      setAnchor(event.currentTarget);
      setOpen((value) => !value);
    },
  } as Record<string, unknown>);

  return (
    <>
      {triggerElement}
      {anchor ? (
        <Suspense fallback={null}>
          <PopoverLayer
            open={open}
            onOpenChange={setOpen}
            anchor={anchor}
            id={id}
            title={title}
            side={side}
            align={align}
            className={className}
            testId={testId}
          >
            {children}
          </PopoverLayer>
        </Suspense>
      ) : null}
    </>
  );
}
