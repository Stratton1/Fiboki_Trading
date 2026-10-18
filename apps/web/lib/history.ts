/**
 * Command-line history: the last 50 commands, newest last, per operator, kept
 * in this browser's localStorage so it survives a reload.
 *
 * Storage can be missing or refuse (private windows, blocked site data, a
 * full quota): every read and write is guarded and the line works without it.
 * Consecutive duplicates are collapsed, as a shell does.
 */

export const HISTORY_LIMIT = 50;
const PREFIX = "fiboki.cmd.history.v1";

function key(operator: string | null | undefined): string {
  return `${PREFIX}.${operator && operator.trim() !== "" ? operator : "anonymous"}`;
}

export function readHistory(operator: string | null | undefined): string[] {
  try {
    const raw = window.localStorage.getItem(key(operator));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string").slice(-HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
}

export function pushHistory(operator: string | null | undefined, command: string): string[] {
  const entry = command.trim();
  const current = readHistory(operator);
  if (entry === "") return current;
  const next = current[current.length - 1] === entry ? current : [...current, entry].slice(-HISTORY_LIMIT);
  try {
    window.localStorage.setItem(key(operator), JSON.stringify(next));
  } catch {
    // Storage refused: the history still works for this page's lifetime.
  }
  return next;
}
