"use client";

/**
 * Animated loading/intro screen for Fiboki Trading.
 * Fibonacci spiral draws itself, bars rise in sequence, wordmark fades in.
 */

export function LoadingScreen() {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-6">
        {/* Animated logo mark */}
        <svg
          width="96"
          height="96"
          viewBox="0 0 64 64"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          className="drop-shadow-lg"
        >
          <defs>
            <linearGradient id="loading-bg" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stopColor="#059669" />
              <stop offset="100%" stopColor="#10B981" />
            </linearGradient>
            <linearGradient id="loading-shine" x1="0" y1="0" x2="0" y2="64" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stopColor="white" stopOpacity="0.15" />
              <stop offset="100%" stopColor="white" stopOpacity="0" />
            </linearGradient>
          </defs>

          {/* Background square scales in */}
          <rect
            width="64" height="64" rx="14"
            fill="url(#loading-bg)"
            className="animate-[scaleIn_0.5s_ease-out_both]"
            style={{ transformOrigin: "center" }}
          />
          <rect
            width="64" height="64" rx="14"
            fill="url(#loading-shine)"
            className="animate-[scaleIn_0.5s_ease-out_both]"
            style={{ transformOrigin: "center" }}
          />

          {/* Bars rise up in Fibonacci sequence timing */}
          <rect
            x="12" y="42" width="7" height="8" rx="1.5"
            fill="white" opacity="0.6"
            className="animate-[barRise_0.4s_ease-out_0.3s_both]"
            style={{ transformOrigin: "bottom" }}
          />
          <rect
            x="22" y="38" width="7" height="12" rx="1.5"
            fill="white" opacity="0.7"
            className="animate-[barRise_0.4s_ease-out_0.45s_both]"
            style={{ transformOrigin: "bottom" }}
          />
          <rect
            x="32" y="32" width="7" height="18" rx="1.5"
            fill="white" opacity="0.85"
            className="animate-[barRise_0.5s_ease-out_0.6s_both]"
            style={{ transformOrigin: "bottom" }}
          />
          <rect
            x="42" y="22" width="7" height="28" rx="1.5"
            fill="white" opacity="0.95"
            className="animate-[barRise_0.5s_ease-out_0.75s_both]"
            style={{ transformOrigin: "bottom" }}
          />

          {/* Spiral arc draws itself */}
          <path
            d="M15.5 42 C15.5 36, 20 30, 25.5 28 C31 26, 35.5 24, 38 20 C40.5 16, 45.5 14, 51 14"
            stroke="white"
            strokeWidth="2"
            strokeLinecap="round"
            fill="none"
            opacity="0.5"
            strokeDasharray="80"
            strokeDashoffset="80"
            className="animate-[drawSpiral_0.8s_ease-out_0.9s_both]"
          />

          {/* Apex dot pops in */}
          <circle
            cx="51" cy="14" r="2.5"
            fill="white" opacity="0"
            className="animate-[dotPop_0.3s_ease-out_1.5s_both]"
          />
        </svg>

        {/* Wordmark fades in below */}
        <div className="flex flex-col items-center animate-[fadeUp_0.5s_ease-out_1.2s_both]">
          <span className="text-2xl font-bold tracking-tight text-foreground">
            Fiboki
          </span>
          <span className="text-xs font-medium tracking-[0.25em] uppercase text-foreground-muted mt-0.5">
            Trading
          </span>
        </div>

        {/* Subtle pulse indicator */}
        <div className="flex gap-1.5 animate-[fadeUp_0.4s_ease-out_1.6s_both]">
          <span className="w-1.5 h-1.5 rounded-full bg-primary/40 animate-[pulse_1s_ease-in-out_infinite]" />
          <span className="w-1.5 h-1.5 rounded-full bg-primary/40 animate-[pulse_1s_ease-in-out_0.2s_infinite]" />
          <span className="w-1.5 h-1.5 rounded-full bg-primary/40 animate-[pulse_1s_ease-in-out_0.4s_infinite]" />
        </div>
      </div>
    </div>
  );
}

export default LoadingScreen;
