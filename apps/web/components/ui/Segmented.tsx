"use client";

import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import { useId, useSyncExternalStore, type ReactNode } from "react";

const noop = () => () => undefined;

/**
 * True once the component is running in a hydrated client, false in server
 * HTML and during hydration (so the first client render matches the server's).
 */
function useHydrated(): boolean {
  return useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
}

const GROUP =
  "inline-flex w-fit gap-0.5 rounded-sm border border-line-control bg-sunken p-0.5";
const ITEM =
  "h-6 rounded-xs px-2.5 text-xs text-fg-muted transition-colors duration-[var(--dur-xs)] hover:text-fg data-[checked]:bg-raised data-[checked]:text-fg data-[checked]:shadow-e2";

/**
 * A segmented control: a labelled radio group rendered as joined buttons.
 * Arrow keys move the selection, as in any radio group.
 *
 * Server HTML (and the hydration pass) renders the same group as plain
 * buttons: Base UI's Radio adds a visually hidden <input> with an inline
 * `style` attribute, which the CSP forbids in server-rendered markup
 * (style-src 'self', no 'unsafe-inline'; tests/e2e/csp.spec.ts). After
 * hydration the Base UI group takes over, positioned through the CSSOM like
 * every other Base UI part. Until then the control shows the current value
 * and does nothing, which is all a not-yet-hydrated page can do anyway.
 */
export function Segmented<T extends string>({
  label,
  value,
  onValueChange,
  options,
  testId,
}: {
  label: string;
  value: T;
  onValueChange: (value: T) => void;
  options: { value: T; label: ReactNode }[];
  testId?: string;
}) {
  const id = useId();
  const hydrated = useHydrated();
  return (
    <div className="flex flex-col gap-1" data-testid={testId}>
      <span id={id} className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-subtle">
        {label}
      </span>
      {hydrated ? (
        <RadioGroup
          aria-labelledby={id}
          value={value}
          onValueChange={(next) => onValueChange(next as T)}
          className={GROUP}
        >
          {options.map((option) => (
            <Radio.Root
              key={option.value}
              value={option.value}
              nativeButton
              render={<button type="button" />}
              data-value={option.value}
              className={ITEM}
            >
              {option.label}
            </Radio.Root>
          ))}
        </RadioGroup>
      ) : (
        <div role="radiogroup" aria-labelledby={id} className={GROUP}>
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={option.value === value}
              data-checked={option.value === value ? "" : undefined}
              data-value={option.value}
              tabIndex={option.value === value ? 0 : -1}
              className={ITEM}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
