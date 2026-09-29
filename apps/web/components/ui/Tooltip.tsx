"use client";

import {
  cloneElement,
  lazy,
  Suspense,
  useEffect,
  useId,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { loadTooltipLayer } from "./layers";

const TooltipLayer = lazy(loadTooltipLayer);

type Handler = (event: never) => void;
type TriggerProps = {
  onPointerEnter?: Handler;
  onPointerLeave?: Handler;
  onPointerDown?: Handler;
  onFocus?: Handler;
  onBlur?: Handler;
  onKeyDown?: Handler;
};

const OPEN_DELAY_MS = 400;

/**
 * A visual label for a control that already has an accessible name (the
 * trigger must carry an `aria-label` that matches). Opens on hover after a
 * short delay, at once on keyboard focus, and on TAP: Base UI disables
 * tooltips for touch input, which left touch operators with no way to read an
 * icon's label. A second tap, Escape, blur or a press elsewhere closes it.
 * Never put information here that is not also available another way.
 *
 * The trigger element is the caller's own and is never re-mounted; the Base
 * UI layer that draws the label loads on first use (see layers.ts).
 */
export function Tooltip({
  label,
  children,
  side = "top",
}: {
  label: ReactNode;
  children: ReactElement<TriggerProps>;
  side?: "top" | "right" | "bottom" | "left";
}) {
  const [open, setOpen] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const id = useId();
  const own = children.props;

  // Hover opens after a delay, so sweeping the pointer across a toolbar does
  // not flash every label.
  useEffect(() => {
    if (!hovering) return;
    const timer = setTimeout(() => setOpen(true), OPEN_DELAY_MS);
    return () => clearTimeout(timer);
  }, [hovering]);

  const hide = () => {
    setHovering(false);
    setOpen(false);
  };

  const trigger = cloneElement(children, {
    "aria-describedby": open ? id : undefined,
    onPointerEnter: (event: PointerEvent & { currentTarget: HTMLElement }) => {
      own.onPointerEnter?.(event as never);
      if (event.pointerType === "touch") return;
      void loadTooltipLayer();
      setAnchor(event.currentTarget);
      setHovering(true);
    },
    onPointerLeave: (event: PointerEvent) => {
      own.onPointerLeave?.(event as never);
      if (event.pointerType !== "touch") hide();
    },
    onPointerDown: (event: PointerEvent & { currentTarget: HTMLElement }) => {
      own.onPointerDown?.(event as never);
      if (event.pointerType !== "touch") return;
      setAnchor(event.currentTarget);
      setOpen((value) => !value);
    },
    onFocus: (event: FocusEvent & { currentTarget: HTMLElement }) => {
      own.onFocus?.(event as never);
      // Keyboard focus only: a mouse click that focuses the trigger, or focus
      // returned programmatically by a closing dialog, should not pop a label.
      if (event.currentTarget.matches(":focus-visible")) {
        setAnchor(event.currentTarget);
        setOpen(true);
      }
    },
    onBlur: (event: FocusEvent) => {
      own.onBlur?.(event as never);
      hide();
    },
    onKeyDown: (event: KeyboardEvent) => {
      own.onKeyDown?.(event as never);
      if (event.key === "Escape") hide();
    },
  } as Record<string, unknown>);

  return (
    <>
      {trigger}
      {anchor ? (
        <Suspense fallback={null}>
          <TooltipLayer
            open={open}
            onOpenChange={(next) => (next ? setOpen(true) : hide())}
            anchor={anchor}
            id={id}
            label={label}
            side={side}
          />
        </Suspense>
      ) : null}
    </>
  );
}
