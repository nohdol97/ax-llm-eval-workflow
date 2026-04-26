import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium leading-none",
  {
    variants: {
      tone: {
        neutral: "bg-zinc-800 text-zinc-300 border border-zinc-700",
        accent: "bg-indigo-950/60 text-indigo-300 border border-indigo-900",
        success: "bg-emerald-950/60 text-emerald-300 border border-emerald-900",
        warning: "bg-amber-950/60 text-amber-300 border border-amber-900",
        error: "bg-rose-950/60 text-rose-300 border border-rose-900",
        info: "bg-sky-950/60 text-sky-300 border border-sky-900",
        muted: "bg-zinc-900 text-zinc-500 border border-zinc-800",
      },
    },
    defaultVariants: {
      tone: "neutral",
    },
  }
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
