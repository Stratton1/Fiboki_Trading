"use client";

import { useEffect, useRef } from "react";
import { useKillSwitchControl } from "./KillSwitchControl";
import { useToast } from "./ui/Toast";

/**
 * The kill-switch arm dialog, opened from anywhere by ⇧K or the palette
 * (loaded on first use by ShellCommands). It OPENS the dialog; it never arms.
 * The operator still chooses PAUSE or FLATTEN (no default), gives a reason,
 * and types FLATTEN for FLATTEN, exactly as from the Risk screen, because it
 * is the same flow (KillSwitchControl).
 *
 * When the dialog may not be opened (mode unknown or loading, disconnected,
 * or a role that cannot arm), it says why in a toast and opens nothing.
 */
export default function GlobalKillSwitch({ request }: { request: number }) {
  const control = useKillSwitchControl();
  const notify = useToast();
  const { open, blocked } = control;
  const handled = useRef(0);

  useEffect(() => {
    if (request === handled.current) return;
    handled.current = request;
    if (blocked) notify(`Kill switch: ${blocked.text}`);
    else open("arm");
  }, [request, blocked, open, notify]);

  return <>{control.dialogs}</>;
}
