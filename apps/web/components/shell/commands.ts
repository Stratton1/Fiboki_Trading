/**
 * The shell's command bus: how a keystroke or the palette asks the shell to
 * OPEN something. Every command here opens a view or a dialog; none changes
 * anything on the platform (plan §4: "no single keystroke executes a
 * mutation"). The kill-switch command opens the arm dialog, which still needs
 * a choice, a reason and, for FLATTEN, the typed phrase.
 */
export type ShellCommand = "palette" | "shortcuts" | "kill-switch";

const EVENT = "fiboki:command";

export function openCommand(command: ShellCommand) {
  window.dispatchEvent(new CustomEvent<ShellCommand>(EVENT, { detail: command }));
}

export function onCommand(listener: (command: ShellCommand) => void): () => void {
  const handler = (event: Event) => listener((event as CustomEvent<ShellCommand>).detail);
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}
