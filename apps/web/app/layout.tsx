import type { Metadata, Viewport } from "next";
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import { Shell } from "@/components/shell/Shell";
import { PREPAINT_SCRIPT } from "@/lib/prepaint";
import "./globals.css";

// No `title` here: the shell renders it from the execution mode ("[PAPER]
// Fiboki", "● LIVE Fiboki"), so a second <title> must not compete with it.
export const metadata: Metadata = {
  applicationName: "Fiboki",
  description: "Operator workstation for the Fiboki V2 research and trading platform.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  colorScheme: "dark light",
};

/**
 * The shell (mode banner, frame, rail, status bar) is rendered here, once,
 * around every page. The inline script applies the operator's theme and
 * density before first paint; `suppressHydrationWarning` is for exactly the
 * attributes it sets on <html>.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-GB" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: PREPAINT_SCRIPT }} />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
