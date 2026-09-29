"use client";

import { lazy, Suspense, useState } from "react";
import { loadConfirmDialogLayer } from "./layers";

const ConfirmDialogLayer = lazy(loadConfirmDialogLayer);

/** One server-supplied qualifier the operator must tick before confirming. */
export interface Acknowledgement {
  code: string;
  text: string;
}

export interface ConfirmChoice {
  id: string;
  title: string;
  body: string;
  /** Server-computed consequences. Never written into this component. */
  consequences: string[];
  destructive?: boolean;
}

/** The typed phrase every mutating dialog requires while the platform is LIVE. */
export const LIVE_CONFIRM_PHRASE = "REAL MONEY";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  executionMode: string;
  choices: ConfirmChoice[];
  requireReason?: boolean;
  reasonMinLength?: number;
  confirmPhrase?: string;
  confirmLabel?: string;
  busy?: boolean;
  errorMessage?: string | null;
  /** A non-error status line, e.g. while server-computed consequences load. */
  notice?: string | null;
  acknowledgements?: Acknowledgement[];
  onCancel: () => void;
  onConfirm: (choiceId: string, reason: string, acknowledged: string[]) => void;
}

/**
 * THE confirm dialog. Every destructive or capital-committing action goes
 * through it; the behaviour is documented on ConfirmDialogLayer.
 *
 * Nothing is mounted until the first open, and the Base UI dialog loads then
 * (prefetched once the page is idle). After that it stays mounted, so the
 * close animation runs and focus returns to the control that opened it.
 */
export function ConfirmDialog(props: ConfirmDialogProps) {
  const [used, setUsed] = useState(props.open);
  if (props.open && !used) setUsed(true);
  if (!used) return null;
  return (
    <Suspense fallback={null}>
      <ConfirmDialogLayer {...props} />
    </Suspense>
  );
}
