"use client";

import { ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface CollapsiblePanelProps {
  title: string;
  description?: string;
  defaultOpen?: boolean;
  rightSlot?: ReactNode;
  children: ReactNode;
}

export function CollapsiblePanel({
  title,
  description,
  defaultOpen = false,
  rightSlot,
  children,
}: CollapsiblePanelProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900/40">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(
          "flex w-full items-center justify-between gap-2 px-3 py-2 text-left",
          "rounded-md transition-colors hover:bg-zinc-800/40 focus-visible:outline-none"
        )}
      >
        <span className="flex min-w-0 items-center gap-2">
          <ChevronRight
            className={cn(
              "h-4 w-4 shrink-0 text-zinc-500 transition-transform",
              open && "rotate-90"
            )}
            aria-hidden
          />
          <span className="truncate text-sm font-medium text-zinc-200">
            {title}
          </span>
          {description && (
            <span className="truncate text-[11px] text-zinc-500">
              {description}
            </span>
          )}
        </span>
        {rightSlot && (
          <span className="shrink-0 text-xs text-zinc-500">{rightSlot}</span>
        )}
      </button>
      {open && (
        <div className="border-t border-zinc-800 px-3 py-3">{children}</div>
      )}
    </div>
  );
}
