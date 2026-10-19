"use client";

import { AlertsMonitor } from "@/components/monitors/AlertsMonitor";
import { PageHead } from "@/components/primitives";

/** SYSTEM · Alert notifications (NTF): price and level alerts that fired, the read marker, and the rules. */
export default function NotificationsPage() {
  return (
    <>
      <PageHead
        title="Notifications"
        intro="Price and level alerts you set, and every time one fired, newest first. The platform evaluates them over recorded quotes in a separate process (the quote recorder, after each poll) and says whether anything is evaluating; the browser never does. Marking read moves your read marker on the server, so every browser agrees on what is unread. Browser notifications are off until you turn them on. A fired alert is a message to you; it never places, sizes or closes anything."
      />
      <AlertsMonitor />
    </>
  );
}
