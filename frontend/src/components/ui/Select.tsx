"use client";

import { ChevronDown } from "lucide-react";
import { forwardRef, type SelectHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(({ className, children, ...props }, ref) => (
  <div className="relative inline-block w-full">
    <select
      ref={ref}
      className={cn(
        "h-8 w-full appearance-none rounded-md border border-zinc-700 bg-zinc-800 pl-3 pr-8 text-sm text-zinc-100 focus-visible:border-indigo-400 focus-visible:outline-none disabled:opacity-50",
        className
      )}
      {...props}
    >
      {children}
    </select>
    <ChevronDown
      className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500"
      aria-hidden
    />
  </div>
));
Select.displayName = "Select";
