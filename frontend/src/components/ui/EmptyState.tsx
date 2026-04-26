import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function EmptyState({
  icon,
  title,
  description,
  primaryAction,
  secondaryAction,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  primaryAction?: ReactNode;
  secondaryAction?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-zinc-800 bg-zinc-950/50 px-6 py-16 text-center",
        className
      )}
    >
      {icon && <div className="text-zinc-500">{icon}</div>}
      <h3 className="text-base font-semibold text-zinc-200">{title}</h3>
      {description && (
        <p className="max-w-md text-sm text-zinc-400">{description}</p>
      )}
      {(primaryAction || secondaryAction) && (
        <div className="mt-2 flex items-center gap-2">
          {primaryAction}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}
