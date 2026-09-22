type Variant = "ok" | "error" | "warn" | "info" | "neutral";

const STYLES: Record<Variant, string> = {
  ok: "bg-green-100 text-green-800",
  error: "bg-red-100 text-red-800",
  warn: "bg-yellow-100 text-yellow-800",
  info: "bg-blue-100 text-blue-800",
  neutral: "bg-gray-100 text-gray-600",
};

export function StatusBadge({
  variant,
  children,
}: {
  variant: Variant;
  children: React.ReactNode;
}) {
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${STYLES[variant]}`}
    >
      {children}
    </span>
  );
}
