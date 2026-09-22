"use client";

interface BookmarkButtonProps {
  isBookmarked: boolean;
  onToggle: () => void;
  size?: "sm" | "md";
}

export function BookmarkButton({ isBookmarked, onToggle, size = "sm" }: BookmarkButtonProps) {
  const sizeClass = size === "sm" ? "w-4 h-4" : "w-5 h-5";
  return (
    <button
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onToggle();
      }}
      className="text-foreground-muted hover:text-amber-500 transition-colors"
      title={isBookmarked ? "Remove bookmark" : "Add bookmark"}
    >
      <svg
        className={sizeClass}
        viewBox="0 0 24 24"
        fill={isBookmarked ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth={2}
        style={{ color: isBookmarked ? "#F59E0B" : undefined }}
      >
        <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
      </svg>
    </button>
  );
}
