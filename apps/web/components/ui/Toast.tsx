"use client";

import { X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

/**
 * A minimal in-house toast: one polite live region, at most three messages,
 * each dismissed after a few seconds or by its close button. It confirms
 * display changes (density, theme); it never reports a trading outcome, which
 * belongs in the dialog that performed it.
 */

interface ToastItem {
  id: number;
  message: string;
}

const ToastContext = createContext<(message: string) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

const LIFETIME_MS = 3500;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const notify = useCallback((message: string) => {
    const id = nextId.current;
    nextId.current += 1;
    setItems((current) => [...current.slice(-2), { id, message }]);
  }, []);

  const dismiss = useCallback((id: number) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div
        role="status"
        aria-live="polite"
        data-testid="toast-region"
        className="pointer-events-none fixed bottom-[calc(var(--statusbar-h)+12px)] right-4 z-[260] flex w-[min(320px,calc(100vw-32px))] flex-col gap-2"
      >
        {items.map((item) => (
          <ToastCard key={item.id} item={item} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastCard({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const timer = setTimeout(() => onDismiss(item.id), LIFETIME_MS);
    return () => clearTimeout(timer);
  }, [item.id, onDismiss, paused]);
  return (
    <div
      data-testid="toast"
      className="pointer-events-auto flex items-center gap-2 rounded-md border border-line-strong bg-overlay px-3 py-2 text-sm text-fg shadow-e3"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <span className="flex-1">{item.message}</span>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => onDismiss(item.id)}
        className="inline-flex h-6 w-6 items-center justify-center rounded-sm text-fg-muted hover:bg-raised hover:text-fg"
      >
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  );
}
