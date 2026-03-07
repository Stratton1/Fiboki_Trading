"use client";

interface OverlayControlsProps {
  ichimokuEnabled: boolean;
  onIchimokuToggle: (enabled: boolean) => void;
}

export default function OverlayControls({
  ichimokuEnabled,
  onIchimokuToggle,
}: OverlayControlsProps) {
  return (
    <div className="flex items-center gap-2">
      <label className="flex items-center gap-2 text-sm text-foreground-muted cursor-pointer select-none">
        <input
          type="checkbox"
          checked={ichimokuEnabled}
          onChange={(e) => onIchimokuToggle(e.target.checked)}
          className="rounded border-gray-300 text-primary focus:ring-primary/50"
        />
        Ichimoku Cloud
      </label>
    </div>
  );
}
