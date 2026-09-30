"use client";

import { lazy, Suspense, useState, type ReactNode } from "react";
import { loadSheetLayer } from "./layers";

const SheetLayer = lazy(loadSheetLayer);

/**
 * A panel that slides in from the right edge: the inspector. Focus-trapped,
 * closed by Escape, focus returned to the opener. The Base UI layer mounts on
 * first open and stays mounted so its close animation and focus return run.
 */
export function Sheet(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  children: ReactNode;
  testId?: string;
  closeLabel?: string;
}) {
  const [used, setUsed] = useState(props.open);
  if (props.open && !used) setUsed(true);
  if (!used) return null;
  return (
    <Suspense fallback={null}>
      <SheetLayer {...props} />
    </Suspense>
  );
}
