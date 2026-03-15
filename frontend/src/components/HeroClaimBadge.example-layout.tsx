/**
 * Example layout matching the hero with dotted background and UPRN search.
 * Copy this structure into your property app and keep <HeroClaimBadge /> in the top-right.
 */
"use client";

import { HeroClaimBadge } from "./HeroClaimBadge";

export function HeroWithClaimExample() {
  return (
    <section className="relative min-h-[80vh] flex flex-col items-center section-hero-premium section-dot bg-[#fafaf9]">
      <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-16 flex flex-col min-h-[80vh]">
        {/* Top-right: animated claim badge (fills the whitespace in the top-right of the box) */}
        <div className="flex justify-end w-full">
          <HeroClaimBadge />
        </div>
        {/* Spacer so search sits at bottom */}
        <div className="flex-1 min-h-[200px]" />
        {/* Bottom: search / UPRN input */}
        <div className="flex items-center justify-center pb-8">
          <div className="flex gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 shadow-sm max-w-md w-full">
            <input
              type="text"
              placeholder="e.g. UPRN 1238541614"
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-gray-400"
            />
            <button
              type="button"
              className="rounded-md bg-emerald-500 px-4 py-1.5 text-sm font-medium text-white hover:bg-emerald-600"
            >
              Search
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
