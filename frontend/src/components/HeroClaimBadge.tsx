"use client";

/**
 * Animated hero claim badge: "Largest free indexed residential platform in England & Wales".
 *
 * Placement (top-right of hero, in the whitespace above the search bar):
 *
 *   <section className="relative min-h-[80vh] ... section-hero-premium section-dot">
 *     <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12 lg:py-16 flex flex-col min-h-[80vh]">
 *       <div className="flex justify-end w-full">
 *         <HeroClaimBadge />
 *       </div>
 *       <div className="flex-1 flex items-end justify-center pb-8">
 *         <!-- UPRN / search input at bottom -->
 *       </div>
 *     </div>
 *   </section>
 */
export function HeroClaimBadge() {
  return (
    <div
      className="hero-claim-badge relative inline-flex max-w-sm items-start gap-3 rounded-xl border border-amber-200/80 bg-gradient-to-br from-amber-50/90 to-white px-4 py-3 shadow-[0_2px_12px_rgba(0,0,0,0.04)]"
      role="status"
      aria-label="Largest free indexed residential platform in England and Wales"
    >
      {/* Animated gradient border glow */}
      <div
        className="hero-claim-shimmer pointer-events-none absolute -inset-px rounded-xl opacity-70"
        aria-hidden
      />
      <span className="hero-claim-icon relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-100 text-amber-600">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-5 w-5"
          aria-hidden
        >
          <path d="M12 2L15 8L22 9L17 14L18 21L12 18L6 21L7 14L2 9L9 8L12 2Z" />
        </svg>
      </span>
      <div className="relative min-w-0">
        <p className="hero-claim-text text-sm font-semibold leading-snug text-stone-800">
          The largest free indexed residential platform in England & Wales
        </p>
        <p className="mt-0.5 text-xs font-medium text-amber-700/90">
          One place for every property. Verified data, no sign-up to search.
        </p>
      </div>
    </div>
  );
}
