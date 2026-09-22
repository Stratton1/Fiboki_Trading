"use client";

/**
 * Fiboki Trading logo — a Fibonacci spiral formed from ascending bars,
 * representing the fusion of Fibonacci analysis and Ichimoku trading.
 */

interface FibokiLogoProps {
  size?: number;
  showWordmark?: boolean;
  animate?: boolean;
  className?: string;
}

export function FibokiMark({ size = 40, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
    >
      {/* Background with subtle gradient */}
      <defs>
        <linearGradient id="fiboki-bg" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#059669" />
          <stop offset="100%" stopColor="#10B981" />
        </linearGradient>
        <linearGradient id="fiboki-shine" x1="0" y1="0" x2="0" y2="64" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="white" stopOpacity="0.15" />
          <stop offset="100%" stopColor="white" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="url(#fiboki-bg)" />
      <rect width="64" height="64" rx="14" fill="url(#fiboki-shine)" />

      {/* Fibonacci-spaced ascending bars — heights follow golden ratio */}
      {/* Bar 1: shortest (Fib 1) */}
      <rect x="12" y="42" width="7" height="8" rx="1.5" fill="white" opacity="0.6" />
      {/* Bar 2: (Fib 1) */}
      <rect x="22" y="38" width="7" height="12" rx="1.5" fill="white" opacity="0.7" />
      {/* Bar 3: (Fib 2) */}
      <rect x="32" y="32" width="7" height="18" rx="1.5" fill="white" opacity="0.85" />
      {/* Bar 4: (Fib 3) */}
      <rect x="42" y="22" width="7" height="28" rx="1.5" fill="white" opacity="0.95" />

      {/* Golden spiral arc overlay — the Fibonacci signature */}
      <path
        d="M15.5 42 C15.5 36, 20 30, 25.5 28 C31 26, 35.5 24, 38 20 C40.5 16, 45.5 14, 51 14"
        stroke="white"
        strokeWidth="2"
        strokeLinecap="round"
        fill="none"
        opacity="0.5"
      />
      {/* Spiral dot at apex */}
      <circle cx="51" cy="14" r="2.5" fill="white" opacity="0.7" />
    </svg>
  );
}

export function FibokiLogo({
  size = 40,
  showWordmark = true,
  animate = false,
  className = "",
}: FibokiLogoProps) {
  return (
    <div className={`flex items-center gap-2.5 ${className}`}>
      <FibokiMark
        size={size}
        className={animate ? "animate-[logoEntry_0.6s_ease-out_both]" : ""}
      />
      {showWordmark && (
        <div
          className={`flex flex-col leading-none ${
            animate ? "animate-[wordmarkEntry_0.5s_ease-out_0.3s_both]" : ""
          }`}
        >
          <span
            className="font-bold tracking-tight text-foreground"
            style={{ fontSize: size * 0.42 }}
          >
            Fiboki
          </span>
          <span
            className="font-medium tracking-widest uppercase text-foreground-muted"
            style={{ fontSize: size * 0.2, marginTop: size * 0.04 }}
          >
            Trading
          </span>
        </div>
      )}
    </div>
  );
}

export default FibokiLogo;
