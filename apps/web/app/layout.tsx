import type { Metadata, Viewport } from "next";
import { ModeBanner } from "@/components/ModeBanner";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "Fiboki Workstation",
  description:
    "Operator workstation for the Fiboki V2 research and trading platform.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

/**
 * The banner is rendered here, ONCE, outside the scrolling content, so it is
 * present on every page and cannot scroll away. No page opts out and no page
 * re-implements it; that is how V1 ended up with 14 of 19 pages that never read
 * the execution mode at all.
 */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-GB">
      <body>
        <ModeBanner />
        <div className="shell">
          <Nav />
          <main className="content">{children}</main>
        </div>
      </body>
    </html>
  );
}
