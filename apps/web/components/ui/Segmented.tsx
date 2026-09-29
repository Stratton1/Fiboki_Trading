"use client";

import { Radio } from "@base-ui/react/radio";
import { RadioGroup } from "@base-ui/react/radio-group";
import { useId, type ReactNode } from "react";

/**
 * A segmented control: a labelled radio group rendered as joined buttons.
 * Arrow keys move the selection, as in any radio group.
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
  return (
    <div className="flex flex-col gap-1" data-testid={testId}>
      <span id={id} className="text-2xs font-semibold uppercase tracking-[0.06em] text-fg-subtle">
        {label}
      </span>
      <RadioGroup
        aria-labelledby={id}
        value={value}
        onValueChange={(next) => onValueChange(next as T)}
        className="inline-flex w-fit gap-0.5 rounded-sm border border-line-control bg-sunken p-0.5"
      >
        {options.map((option) => (
          <Radio.Root
            key={option.value}
            value={option.value}
            nativeButton
            render={<button type="button" />}
            data-value={option.value}
            className="h-6 rounded-xs px-2.5 text-xs text-fg-muted transition-colors duration-[var(--dur-xs)] hover:text-fg data-[checked]:bg-raised data-[checked]:text-fg data-[checked]:shadow-e2"
          >
            {option.label}
          </Radio.Root>
        ))}
      </RadioGroup>
    </div>
  );
}
