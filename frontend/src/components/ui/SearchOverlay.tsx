"use client";

/**
 * 글로벌 검색 오버레이 (⌘K).
 *
 * - useGlobalSearch (alias `useSearch`) 훅으로 디바운스된 쿼리를 보낸다.
 * - 결과는 SearchResponse.results.{prompts,datasets,experiments} 형태.
 * - ESC, 외부 클릭, 결과 클릭 시 onClose() 호출.
 */

import { AnimatePresence, motion } from "framer-motion";
import { Database, FlaskConical, Loader2, MessageSquare, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useGlobalSearch } from "@/lib/hooks/useSearch";
import type { SearchResult, SearchResultType } from "@/lib/types/api";
import { cn } from "@/lib/utils";

interface SearchOverlayProps {
  open: boolean;
  onClose: () => void;
}

interface FlatResult {
  type: SearchResultType;
  id: string;
  name: string;
  snippet?: string;
  href: string;
}

const GROUP_META: Record<
  SearchResultType,
  { label: string; icon: React.ComponentType<{ className?: string }> }
> = {
  prompt: { label: "프롬프트", icon: MessageSquare },
  dataset: { label: "데이터셋", icon: Database },
  experiment: { label: "실험", icon: FlaskConical },
};

const DEFAULT_PROJECT_ID = "production-api";

function toHref(r: SearchResult): string {
  switch (r.type) {
    case "prompt":
      return `/prompts?selected=${encodeURIComponent(r.name)}`;
    case "dataset":
      return `/datasets/${encodeURIComponent(r.name)}`;
    case "experiment":
      return `/experiments/${encodeURIComponent(r.id)}`;
    default:
      return "/";
  }
}

export function SearchOverlay({ open, onClose }: SearchOverlayProps) {
  const { user } = useAuth();
  const projectId =
    (user as { currentProjectId?: string } | null)?.currentProjectId ??
    DEFAULT_PROJECT_ID;
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  // 200ms debounce
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 200);
    return () => clearTimeout(t);
  }, [query]);

  // 오픈 시 입력창 포커스 + 상태 초기화
  useEffect(() => {
    if (!open) return;
    setActiveIndex(0);
    const id = window.setTimeout(() => inputRef.current?.focus(), 50);
    return () => window.clearTimeout(id);
  }, [open]);

  // ESC 처리
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // 검색 훅 — 디바운스된 쿼리만 보낸다.
  const { data, isLoading } = useGlobalSearch(projectId, debounced);

  const groups = useMemo(() => {
    const r = data?.results ?? {};
    const toFlat = (arr: SearchResult[] | undefined): FlatResult[] =>
      (arr ?? []).map((x) => ({
        type: x.type,
        id: x.id,
        name: x.name,
        snippet: x.snippet ?? x.match_context,
        href: toHref(x),
      }));
    return {
      prompt: toFlat(r.prompts),
      dataset: toFlat(r.datasets),
      experiment: toFlat(r.experiments),
    };
  }, [data]);

  const flat = useMemo(
    () => [...groups.prompt, ...groups.dataset, ...groups.experiment],
    [groups]
  );

  // 화살표 네비게이션
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (flat.length === 0) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % flat.length);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + flat.length) % flat.length);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const item = flat[activeIndex];
        if (item) {
          router.push(item.href);
          onClose();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, flat, activeIndex, router, onClose]);

  const handlePick = (item: FlatResult) => {
    router.push(item.href);
    onClose();
  };

  return (
    <AnimatePresence>
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center px-4 pt-[12vh]"
          role="presentation"
        >
          <motion.div
            className="absolute inset-0 bg-zinc-950/80 backdrop-blur-sm"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.12 }}
            onClick={onClose}
            aria-hidden
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="검색"
            initial={{ opacity: 0, y: -8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            className="relative z-10 w-full max-w-2xl overflow-hidden rounded-xl border border-zinc-800 bg-zinc-900 shadow-[0_8px_24px_rgba(0,0,0,0.5)]"
          >
            <div className="flex items-center gap-2 border-b border-zinc-800 px-4 py-3">
              <Search className="h-4 w-4 text-zinc-400" aria-hidden />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActiveIndex(0);
                }}
                placeholder="프롬프트 · 데이터셋 · 실험 검색…"
                className="flex-1 bg-transparent text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none"
                aria-label="검색어"
              />
              {isLoading && (
                <Loader2 className="h-4 w-4 animate-spin text-zinc-500" aria-hidden />
              )}
              <kbd className="rounded border border-zinc-700 bg-zinc-950 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                ESC
              </kbd>
            </div>

            <div className="max-h-[60vh] overflow-y-auto py-2">
              {debounced.trim() === "" ? (
                <div className="px-4 py-8 text-center text-xs text-zinc-500">
                  검색어를 입력하세요. ↑↓로 이동, Enter로 열기.
                </div>
              ) : flat.length === 0 && !isLoading ? (
                <div className="px-4 py-8 text-center text-xs text-zinc-500">
                  &lsquo;{debounced}&rsquo; 검색 결과가 없습니다.
                </div>
              ) : (
                <ul role="listbox" aria-label="검색 결과">
                  {(["prompt", "dataset", "experiment"] as const).map((type) => {
                    const items = groups[type];
                    if (items.length === 0) return null;
                    const Meta = GROUP_META[type];
                    const Icon = Meta.icon;
                    return (
                      <li key={type} className="px-1 py-1">
                        <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                          {Meta.label}
                        </div>
                        <ul>
                          {items.map((item) => {
                            const flatIndex = flat.findIndex(
                              (x) => x.id === item.id && x.type === item.type
                            );
                            const active = flatIndex === activeIndex;
                            return (
                              <li key={`${item.type}_${item.id}`}>
                                <button
                                  type="button"
                                  role="option"
                                  aria-selected={active}
                                  onMouseEnter={() => setActiveIndex(flatIndex)}
                                  onClick={() => handlePick(item)}
                                  className={cn(
                                    "flex w-full items-center gap-3 px-3 py-2 text-left text-sm transition-colors",
                                    active
                                      ? "bg-indigo-500/15 text-zinc-50"
                                      : "text-zinc-200 hover:bg-zinc-800/60"
                                  )}
                                >
                                  <Icon
                                    className={cn(
                                      "h-4 w-4 shrink-0",
                                      active ? "text-indigo-300" : "text-zinc-500"
                                    )}
                                    aria-hidden
                                  />
                                  <div className="min-w-0 flex-1">
                                    <div className="truncate text-sm">
                                      {item.name}
                                    </div>
                                    {item.snippet && (
                                      <div className="truncate text-xs text-zinc-500">
                                        {item.snippet}
                                      </div>
                                    )}
                                  </div>
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div className="flex items-center justify-between border-t border-zinc-800 bg-zinc-950/40 px-4 py-2 text-[10px] text-zinc-500">
              <div className="flex gap-3">
                <span>
                  <kbd className="rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 font-mono">↑</kbd>{" "}
                  <kbd className="rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 font-mono">↓</kbd>{" "}
                  이동
                </span>
                <span>
                  <kbd className="rounded border border-zinc-700 bg-zinc-950 px-1 py-0.5 font-mono">Enter</kbd>{" "}
                  열기
                </span>
              </div>
              <span>{flat.length > 0 ? `${flat.length}건` : ""}</span>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
