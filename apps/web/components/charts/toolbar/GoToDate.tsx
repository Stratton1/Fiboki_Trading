"use client";

import { CalendarSearch } from "lucide-react";
import { useState } from "react";
import { isoToZonedInput, TIME_ZONES, zonedInputToIso, type TimeZoneId } from "../time";

/**
 * Go to a date (programme feature 15). The operator types a wall time in the
 * DISPLAY zone; it is converted to a UTC instant here and everything after
 * that is UTC: the chart scrolls to the bar open at or before it, or, when it
 * is outside the loaded bars, the workstation asks the API for a window
 * centred on it (`?at=`, GET /bars?around=).
 */
export function GoToDate({
  tz,
  at,
  onGoTo,
  onLatest,
}: {
  tz: TimeZoneId;
  at: string | null;
  onGoTo: (iso: string) => void;
  onLatest: () => void;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const zone = TIME_ZONES.find((z) => z.id === tz)?.label ?? tz;
  return (
    <form
      className="cw-goto"
      data-testid="goto-date"
      onSubmit={(e) => {
        e.preventDefault();
        const iso = zonedInputToIso(value, tz);
        if (!iso) {
          setError("Enter a date and time.");
          return;
        }
        setError(null);
        onGoTo(iso);
      }}
    >
      <label htmlFor="cw-goto-input" className="sr-only">
        Go to a date and time ({zone})
      </label>
      <input
        id="cw-goto-input"
        type="datetime-local"
        data-testid="goto-input"
        value={value || (at ? isoToZonedInput(at, tz) : "")}
        onChange={(e) => setValue(e.target.value)}
        aria-invalid={error !== null}
        aria-describedby={error ? "cw-goto-error" : undefined}
      />
      <button type="submit" className="cw-replay__btn cw-replay__btn--text" data-testid="goto-go" title={`Go to this ${zone} time`}>
        <CalendarSearch size={12} aria-hidden="true" /> Go
      </button>
      {at ? (
        <button type="button" className="cw-replay__btn cw-replay__btn--text" data-testid="goto-latest" onClick={onLatest}>
          Latest
        </button>
      ) : null}
      {error ? (
        <span id="cw-goto-error" className="cw-goto__error" role="alert">
          {error}
        </span>
      ) : null}
    </form>
  );
}

export function TimeZoneSelect({ tz, onTz }: { tz: TimeZoneId; onTz: (tz: TimeZoneId) => void }) {
  return (
    <label className="cw-replay__field" title="Display time zone: labels only; every time is stored and sent in UTC">
      <span className="sr-only">Display time zone</span>
      <select data-testid="chart-tz" value={tz} onChange={(e) => onTz(e.target.value as TimeZoneId)}>
        {TIME_ZONES.map((z) => (
          <option key={z.id} value={z.id}>
            {z.label}
          </option>
        ))}
      </select>
    </label>
  );
}
