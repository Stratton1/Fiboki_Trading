"use client";

import {
  MousePointer,
  TrendingUp,
  Minus,
  MoveRight,
  GitBranch,
  Columns3,
  Trash2,
} from "lucide-react";

const DRAWING_TOOLS = [
  { id: null, label: "Pointer", icon: MousePointer },
  { id: "straightLine", label: "Trend Line", icon: TrendingUp },
  { id: "horizontalStraightLine", label: "Horizontal Line", icon: Minus },
  { id: "rayLine", label: "Ray", icon: MoveRight },
  { id: "fibonacciLine", label: "Fibonacci", icon: GitBranch },
  { id: "parallelStraightLine", label: "Channel", icon: Columns3 },
] as const;

interface DrawingToolbarProps {
  activeTool: string | null;
  onToolChange: (tool: string | null) => void;
  onClearAll: () => void;
}

export default function DrawingToolbar({
  activeTool,
  onToolChange,
  onClearAll,
}: DrawingToolbarProps) {
  return (
    <div className="flex items-center gap-1">
      {DRAWING_TOOLS.map((tool) => {
        const Icon = tool.icon;
        const isActive = activeTool === tool.id;
        return (
          <button
            key={tool.id ?? "pointer"}
            onClick={() =>
              onToolChange(isActive && tool.id !== null ? null : tool.id)
            }
            className={`px-3 py-1.5 text-sm rounded-md transition flex items-center gap-1.5 ${
              isActive
                ? "bg-primary text-white font-medium"
                : "bg-background-card text-foreground-muted hover:text-foreground hover:bg-background-muted border border-gray-200"
            }`}
          >
            <Icon size={14} />
            {tool.label}
          </button>
        );
      })}

      {/* Separator */}
      <div className="w-px h-6 bg-border" />

      {/* Clear All */}
      <button
        onClick={onClearAll}
        className="px-3 py-1.5 text-sm rounded-md transition flex items-center gap-1.5 bg-background-card text-foreground-muted hover:text-red-500 hover:bg-background-muted border border-gray-200"
      >
        <Trash2 size={14} />
        Clear All
      </button>
    </div>
  );
}
