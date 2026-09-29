"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Unplug } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { LOGIN_PATH } from "@/lib/api";
import { useClock } from "@/lib/clock";
import { flushLive, patchConnection, useLive } from "@/lib/live-store";
import { reconnectStream, setStreamControl, type StreamControl } from "@/lib/stream/control";
import { Button } from "../ui/Button";

/**
 * Starts the live stream once the page is interactive. The client is a
 * separate chunk (dynamic import), so it costs nothing in the first-load
 * budget. Not on the sign-in page: the stream needs a session.
 */
export function StreamStarter() {
  const client = useQueryClient();
  const signingIn = usePathname() === LOGIN_PATH;

  useEffect(() => {
    if (signingIn || typeof EventSource === "undefined") return;
    let stopped = false;
    let control: StreamControl | null = null;
    void import("@/lib/stream/client").then(({ startStream }) => {
      if (stopped) return;
      control = startStream(client);
      setStreamControl(control);
    });
    return () => {
      stopped = true;
      control?.stop();
      setStreamControl(null);
      patchConnection({ state: "idle", role: "none", failures: 0, nextRetryAt: null });
      flushLive();
    };
  }, [client, signingIn]);

  return null;
}

/**
 * The DISCONNECTED strip (report E §6.3): after five consecutive failures.
 * The numbers on screen stay, marked stale; stream-backed views poll REST
 * every 10 s; attempts continue at the 15 s cap; Reconnect tries now.
 */
export function StreamBanner() {
  const connection = useLive((s) => s.connection);
  const now = useClock(connection.state === "disconnected");
  if (connection.state !== "disconnected") return null;
  const wait =
    connection.nextRetryAt === null
      ? null
      : Math.max(0, Math.ceil((connection.nextRetryAt - now) / 1_000));
  return (
    <div className="stream-strip" data-testid="stream-disconnected" role="alert">
      <Unplug size={14} aria-hidden="true" />
      <strong>DISCONNECTED</strong>
      <span>
        The live stream failed {connection.failures} times in a row. Figures on screen are the
        last good ones; stream-backed views are polled every 10 s.
        {wait !== null ? ` Next automatic attempt in ${wait} s.` : ""}
      </span>
      <Button size="sm" data-testid="stream-reconnect" onClick={() => reconnectStream()}>
        Reconnect
      </Button>
    </div>
  );
}
