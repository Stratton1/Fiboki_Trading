"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { Sheet } from "../ui/Sheet";

/**
 * The inspector slot: one side sheet for the shell, opened with whatever the
 * caller wants to inspect. Entity inspectors (a position, a strategy, an
 * incident) arrive with their screens; the status bar uses it today for the
 * platform health detail.
 */

interface InspectorContent {
  title: ReactNode;
  body: ReactNode;
}

const InspectorContext = createContext<{
  open: (content: InspectorContent) => void;
  close: () => void;
}>({ open: () => {}, close: () => {} });

export function useInspector() {
  return useContext(InspectorContext);
}

export function InspectorProvider({ children }: { children: ReactNode }) {
  const [content, setContent] = useState<InspectorContent | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const open = useCallback((next: InspectorContent) => {
    setContent(next);
    setIsOpen(true);
  }, []);
  const close = useCallback(() => setIsOpen(false), []);
  return (
    <InspectorContext.Provider value={{ open, close }}>
      {children}
      <Sheet open={isOpen} onOpenChange={setIsOpen} title={content?.title} testId="inspector">
        {content?.body}
      </Sheet>
    </InspectorContext.Provider>
  );
}
