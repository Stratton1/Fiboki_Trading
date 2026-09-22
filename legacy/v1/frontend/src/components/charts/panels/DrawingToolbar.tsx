"use client";

import { useState } from "react";
import {
  MousePointer,
  TrendingUp,
  Minus,
  MoveRight,
  GitBranch,
  Columns3,
  Trash2,
  Save,
  FolderOpen,
} from "lucide-react";
import { api } from "@/lib/api";
import type { DrawingTemplate } from "@/types/contracts/drawings";

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
  drawings?: Record<string, unknown>[];
  onLoadTemplate?: (drawings: Record<string, unknown>[]) => void;
}

export default function DrawingToolbar({
  activeTool,
  onToolChange,
  onClearAll,
  drawings,
  onLoadTemplate,
}: DrawingToolbarProps) {
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [showLoadDialog, setShowLoadDialog] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [templates, setTemplates] = useState<DrawingTemplate[]>([]);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);

  const handleSaveTemplate = async () => {
    if (!templateName.trim() || !drawings?.length) return;
    setSaving(true);
    try {
      await api.createDrawingTemplate({
        name: templateName.trim(),
        drawings,
      });
      setTemplateName("");
      setShowSaveDialog(false);
    } catch {
      // error handled by apiFetch
    } finally {
      setSaving(false);
    }
  };

  const handleOpenLoad = async () => {
    setShowLoadDialog(true);
    setLoading(true);
    try {
      const list = await api.listDrawingTemplates();
      setTemplates(list);
    } catch {
      setTemplates([]);
    } finally {
      setLoading(false);
    }
  };

  const handleLoadTemplate = (t: DrawingTemplate) => {
    onLoadTemplate?.(t.drawings);
    setShowLoadDialog(false);
  };

  const handleDeleteTemplate = async (id: number) => {
    try {
      await api.deleteDrawingTemplate(id);
      setTemplates((prev) => prev.filter((t) => t.id !== id));
    } catch {
      // error handled by apiFetch
    }
  };

  return (
    <div className="flex items-center gap-1 relative">
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

      {/* Template actions */}
      <button
        onClick={() => {
          if (drawings?.length) setShowSaveDialog(true);
        }}
        disabled={!drawings?.length}
        title="Save as Template"
        className="px-3 py-1.5 text-sm rounded-md transition flex items-center gap-1.5 bg-background-card text-foreground-muted hover:text-foreground hover:bg-background-muted border border-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <Save size={14} />
        Save
      </button>
      <button
        onClick={handleOpenLoad}
        title="Load Template"
        className="px-3 py-1.5 text-sm rounded-md transition flex items-center gap-1.5 bg-background-card text-foreground-muted hover:text-foreground hover:bg-background-muted border border-gray-200"
      >
        <FolderOpen size={14} />
        Load
      </button>

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

      {/* Save Dialog */}
      {showSaveDialog && (
        <div className="absolute top-full left-0 mt-2 z-50 bg-background-card border border-border rounded-lg shadow-lg p-4 w-72">
          <h4 className="text-sm font-medium mb-2">Save as Template</h4>
          <input
            type="text"
            value={templateName}
            onChange={(e) => setTemplateName(e.target.value)}
            placeholder="Template name"
            className="w-full px-3 py-1.5 text-sm border border-border rounded-md bg-background mb-3"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSaveTemplate();
              if (e.key === "Escape") setShowSaveDialog(false);
            }}
          />
          <div className="flex justify-end gap-2">
            <button
              onClick={() => setShowSaveDialog(false)}
              className="px-3 py-1 text-sm rounded-md hover:bg-background-muted"
            >
              Cancel
            </button>
            <button
              onClick={handleSaveTemplate}
              disabled={!templateName.trim() || saving}
              className="px-3 py-1 text-sm rounded-md bg-primary text-white disabled:opacity-40"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}

      {/* Load Dialog */}
      {showLoadDialog && (
        <div className="absolute top-full left-0 mt-2 z-50 bg-background-card border border-border rounded-lg shadow-lg p-4 w-80 max-h-64 overflow-y-auto">
          <div className="flex justify-between items-center mb-2">
            <h4 className="text-sm font-medium">Load Template</h4>
            <button
              onClick={() => setShowLoadDialog(false)}
              className="text-foreground-muted hover:text-foreground text-xs"
            >
              Close
            </button>
          </div>
          {loading ? (
            <p className="text-sm text-foreground-muted">Loading…</p>
          ) : templates.length === 0 ? (
            <p className="text-sm text-foreground-muted">No saved templates.</p>
          ) : (
            <ul className="space-y-1">
              {templates.map((t) => (
                <li
                  key={t.id}
                  className="flex items-center justify-between px-2 py-1.5 rounded hover:bg-background-muted"
                >
                  <button
                    onClick={() => handleLoadTemplate(t)}
                    className="text-sm text-left flex-1 truncate"
                  >
                    {t.name}
                    <span className="text-foreground-muted ml-1 text-xs">
                      ({t.drawings.length} drawings)
                    </span>
                  </button>
                  <button
                    onClick={() => handleDeleteTemplate(t.id)}
                    className="text-foreground-muted hover:text-red-500 ml-2"
                  >
                    <Trash2 size={12} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
