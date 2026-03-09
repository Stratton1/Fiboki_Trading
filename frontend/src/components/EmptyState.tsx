import type { ReactNode } from "react";
import { Inbox } from "lucide-react";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-4">
      <div className="text-foreground-muted/40 mb-3">
        {icon ?? <Inbox size={40} strokeWidth={1.5} />}
      </div>
      <p className="text-sm font-medium text-foreground-muted">{title}</p>
      {description && (
        <p className="text-xs text-foreground-muted/70 mt-1 max-w-xs text-center">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
