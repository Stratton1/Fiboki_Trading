"use client";

import { HeroWithClaimExample } from "@/components/HeroClaimBadge.example-layout";

/**
 * Demo route: hero section with animated claim badge (top-right) and search bar.
 * Visit /hero-demo to view. No auth required.
 */
export default function HeroDemoPage() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <main id="main-content" className="flex flex-col flex-1">
        <HeroWithClaimExample />
      </main>
    </div>
  );
}
