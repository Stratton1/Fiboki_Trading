"use client";

import { useExecutionMode } from "@/components/shell/platform";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

/** A delete of a display document (a layout, a watchlist) through the one shared ConfirmDialog. */
export function ConfirmDelete({
  open,
  title,
  body,
  consequences,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  consequences: string[];
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { mode } = useExecutionMode();
  return (
    <ConfirmDialog
      open={open}
      title={title}
      executionMode={mode}
      choices={[{ id: "delete", title: "Delete", body, consequences, destructive: true, phrase: null }]}
      confirmLabel="Delete"
      onCancel={onCancel}
      onConfirm={() => onConfirm()}
    />
  );
}
