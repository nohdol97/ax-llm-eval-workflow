"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  createContext,
  useContext,
  useId,
  useMemo,
  useRef,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { cn } from "@/lib/utils";

interface TabsContextValue {
  value: string;
  onValueChange: (next: string) => void;
  baseId: string;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabs() {
  const ctx = useContext(TabsContext);
  if (!ctx) throw new Error("Tabs components must be used inside <Tabs>.");
  return ctx;
}

interface TabsProps {
  value: string;
  onValueChange: (next: string) => void;
  children: ReactNode;
  className?: string;
}

export function Tabs({ value, onValueChange, children, className }: TabsProps) {
  const baseId = useId();
  const ctx = useMemo<TabsContextValue>(
    () => ({ value, onValueChange, baseId }),
    [value, onValueChange, baseId]
  );
  return (
    <TabsContext.Provider value={ctx}>
      <div className={className}>{children}</div>
    </TabsContext.Provider>
  );
}

interface TabsListProps {
  children: ReactNode;
  className?: string;
  "aria-label"?: string;
}

export function TabsList({
  children,
  className,
  "aria-label": ariaLabel,
}: TabsListProps) {
  const listRef = useRef<HTMLDivElement>(null);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (!listRef.current?.contains(target)) return;
    const tabs = Array.from(
      listRef.current.querySelectorAll<HTMLButtonElement>('[role="tab"]')
    );
    const idx = tabs.findIndex((t) => t === target);
    if (idx < 0) return;
    let nextIdx = idx;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      nextIdx = (idx + 1) % tabs.length;
      e.preventDefault();
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      nextIdx = (idx - 1 + tabs.length) % tabs.length;
      e.preventDefault();
    } else if (e.key === "Home") {
      nextIdx = 0;
      e.preventDefault();
    } else if (e.key === "End") {
      nextIdx = tabs.length - 1;
      e.preventDefault();
    }
    if (nextIdx !== idx) {
      tabs[nextIdx]?.focus();
      tabs[nextIdx]?.click();
    }
  };

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-zinc-800 bg-zinc-900 p-1",
        className
      )}
    >
      {children}
    </div>
  );
}

interface TabsTriggerProps {
  value: string;
  children: ReactNode;
  className?: string;
  disabled?: boolean;
}

export function TabsTrigger({
  value,
  children,
  className,
  disabled,
}: TabsTriggerProps) {
  const { value: active, onValueChange, baseId } = useTabs();
  const selected = active === value;
  return (
    <button
      type="button"
      role="tab"
      id={`${baseId}-tab-${value}`}
      aria-controls={`${baseId}-panel-${value}`}
      aria-selected={selected}
      tabIndex={selected ? 0 : -1}
      disabled={disabled}
      onClick={() => onValueChange(value)}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-xs font-medium transition-colors",
        selected
          ? "bg-indigo-500/15 text-indigo-200"
          : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200",
        disabled && "pointer-events-none opacity-50",
        className
      )}
    >
      {children}
    </button>
  );
}

interface TabsContentProps {
  value: string;
  children: ReactNode;
  className?: string;
}

export function TabsContent({ value, children, className }: TabsContentProps) {
  const { value: active, baseId } = useTabs();
  const selected = active === value;
  return (
    <div
      role="tabpanel"
      id={`${baseId}-panel-${value}`}
      aria-labelledby={`${baseId}-tab-${value}`}
      hidden={!selected}
      className={className}
    >
      <AnimatePresence mode="wait">
        {selected && (
          <motion.div
            key={value}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.18 }}
          >
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
