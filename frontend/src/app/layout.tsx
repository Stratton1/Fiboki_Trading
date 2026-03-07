import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FIBOKEI",
  description: "Multi-strategy automated trading platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        {children}
      </body>
    </html>
  );
}
