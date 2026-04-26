"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import type { CompareTab } from "./types";

interface TabDef {
  id: CompareTab;
  label: string;
}

const TABS: TabDef[] = [
  { id: "score", label: "스코어" },
  { id: "latency", label: "지연시간" },
  { id: "cost", label: "비용" },
  { id: "tokens", label: "토큰" },
];

interface ComparisonTabsProps {
  active: CompareTab;
  onChange: (tab: CompareTab) => void;
}

export function ComparisonTabs({ active, onChange }: ComparisonTabsProps) {
  return (
    <div
      role="tablist"
      aria-label="비교 지표"
      className="flex items-center gap-1 border-b border-zinc-800"
    >
      {TABS.map((tab) => {
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={isActive}
            aria-controls={`tabpanel-${tab.id}`}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onChange(tab.id)}
            className={cn(
              "relative px-4 py-2 text-sm font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/50 focus-visible:ring-offset-0",
              isActive
                ? "text-zinc-50"
                : "text-zinc-400 hover:text-zinc-200"
            )}
          >
            {tab.label}
            {isActive && (
              <motion.span
                layoutId="compare-tab-underline"
                className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-indigo-400"
                transition={{ type: "spring", stiffness: 380, damping: 30 }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}
