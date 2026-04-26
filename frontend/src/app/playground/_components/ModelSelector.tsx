"use client";

import { Check, ChevronDown, Eye } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { models, providerLabels } from "@/lib/mock/data";
import type { Model, ProviderId } from "@/lib/mock/types";
import { cn, formatCurrency, formatNumber } from "@/lib/utils";

interface ModelSelectorProps {
  value: string;
  onChange: (modelId: string) => void;
}

export function ModelSelector({ value, onChange }: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const grouped = useMemo(() => {
    const map = new Map<ProviderId, Model[]>();
    models.forEach((m) => {
      const arr = map.get(m.provider) ?? [];
      arr.push(m);
      map.set(m.provider, arr);
    });
    return Array.from(map.entries());
  }, []);

  const selected = useMemo(
    () => models.find((m) => m.id === value) ?? models[0],
    [value]
  );

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "flex h-8 min-w-[220px] items-center justify-between gap-2 rounded-md border border-zinc-700",
          "bg-zinc-800 px-3 text-sm text-zinc-100 transition-colors hover:bg-zinc-700",
          "focus-visible:border-indigo-400 focus-visible:outline-none"
        )}
      >
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate font-medium">{selected.name}</span>
          <span className="truncate text-[11px] text-zinc-500">
            {providerLabels[selected.provider]}
          </span>
        </span>
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-zinc-500 transition-transform",
            open && "rotate-180"
          )}
          aria-hidden
        />
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="모델 선택"
          className={cn(
            "absolute right-0 z-30 mt-1 max-h-[420px] w-[360px] overflow-y-auto",
            "rounded-md border border-zinc-700 bg-zinc-900 p-1 shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
          )}
        >
          {grouped.map(([provider, list]) => (
            <div key={provider} className="mb-1 last:mb-0">
              <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                {providerLabels[provider]}
              </div>
              <ul className="flex flex-col">
                {list.map((m) => {
                  const isSelected = m.id === value;
                  return (
                    <li key={m.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        onClick={() => {
                          onChange(m.id);
                          setOpen(false);
                        }}
                        className={cn(
                          "flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left transition-colors",
                          "hover:bg-zinc-800",
                          isSelected && "bg-indigo-500/15"
                        )}
                      >
                        <span className="mt-0.5 w-4 shrink-0">
                          {isSelected && (
                            <Check className="h-3.5 w-3.5 text-indigo-300" />
                          )}
                        </span>
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="flex items-center gap-1.5">
                            <span className="truncate text-sm text-zinc-100">
                              {m.name}
                            </span>
                            {m.vision && (
                              <Eye
                                className="h-3 w-3 shrink-0 text-zinc-500"
                                aria-label="vision 지원"
                              />
                            )}
                          </span>
                          <span className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
                            {formatNumber(m.contextWindow)}ctx ·{" "}
                            in {formatCurrency(m.inputCostPerK, 4)}/1K · out{" "}
                            {formatCurrency(m.outputCostPerK, 4)}/1K
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
