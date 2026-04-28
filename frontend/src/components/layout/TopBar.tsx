"use client";

import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, Check, ChevronDown, GitCompare, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useBasket } from "@/lib/basket";
import { useProjectList, useSwitchProject } from "@/lib/hooks/useProjects";
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useNotificationList,
} from "@/lib/hooks/useNotifications";
import type { Notification } from "@/lib/types/api";
import { SearchOverlay } from "@/components/ui/SearchOverlay";
import { cn } from "@/lib/utils";

const DEFAULT_PROJECT_ID = "production-api";

function formatRelativeShort(iso?: string): string {
  if (!iso) return "—";
  try {
    const t = new Date(iso).getTime();
    const diff = Date.now() - t;
    const min = Math.floor(diff / 60_000);
    if (min < 1) return "방금";
    if (min < 60) return `${min}분 전`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}시간 전`;
    const d = Math.floor(hr / 24);
    if (d < 7) return `${d}일 전`;
    return new Date(iso).toLocaleDateString("ko-KR");
  } catch {
    return "—";
  }
}

export function TopBar() {
  const { user } = useAuth();
  const userProjectId =
    (user as { currentProjectId?: string } | null)?.currentProjectId ??
    DEFAULT_PROJECT_ID;
  const {
    items: basketItems,
    count: basketCount,
    remove: basketRemove,
    clear: basketClear,
  } = useBasket();
  const projectsQuery = useProjectList();
  const switchProject = useSwitchProject();

  const [projectId, setProjectId] = useState(userProjectId);
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [basketOpen, setBasketOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);

  const projectMenuRef = useRef<HTMLDivElement>(null);
  const bellRef = useRef<HTMLDivElement>(null);
  const basketRef = useRef<HTMLDivElement>(null);

  const projects = useMemo(
    () => projectsQuery.data?.projects ?? [],
    [projectsQuery.data?.projects]
  );
  const currentProject = projects.find((p) => p.id === projectId) ?? projects[0];

  // 프로젝트 목록 로드 시 초기 동기화
  useEffect(() => {
    if (projects.length > 0 && !projects.find((p) => p.id === projectId)) {
      setProjectId(projects[0].id);
    }
  }, [projects, projectId]);

  // 알림 목록 (자동 30초 polling은 hook 내부에서 처리)
  const { data: notifData } = useNotificationList(projectId, {
    unreadOnly: false,
  });
  const notifications: Notification[] = useMemo(() => {
    const list = (notifData?.items ?? notifData?.notifications ?? []) as Notification[];
    return [...list].sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
  }, [notifData]);
  const unreadCount = useMemo(
    () => notifications.filter((n) => !n.read).length,
    [notifications]
  );

  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();

  // 외부 클릭 닫기
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (projectMenuOpen && projectMenuRef.current && !projectMenuRef.current.contains(target)) {
        setProjectMenuOpen(false);
      }
      if (bellOpen && bellRef.current && !bellRef.current.contains(target)) {
        setBellOpen(false);
      }
      if (basketOpen && basketRef.current && !basketRef.current.contains(target)) {
        setBasketOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [projectMenuOpen, bellOpen, basketOpen]);

  // ⌘K / Ctrl+K 단축키
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const isMod = e.metaKey || e.ctrlKey;
      if (isMod && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handlePickProject = (id: string) => {
    setProjectMenuOpen(false);
    if (id === currentProject?.id) return;
    setProjectId(id);
    switchProject.mutate?.(id);
  };

  const handleNotificationClick = (n: Notification) => {
    if (!n.read) markRead.mutate?.({ id: n.id });
    setBellOpen(false);
    const link = n.link ?? n.target_url;
    if (link && typeof window !== "undefined") {
      window.location.assign(link);
    }
  };

  const handleMarkAllRead = () => {
    if (unreadCount === 0) return;
    markAll.mutate?.();
  };

  const compareHref =
    basketItems.length > 0
      ? `/compare?runs=${encodeURIComponent(basketItems.join(","))}`
      : "/compare";

  const initial = user?.name?.charAt(0) ?? user?.email?.charAt(0) ?? "?";

  return (
    <header className="sticky top-0 z-30 flex h-12 items-center justify-between border-b border-zinc-900 bg-zinc-950/95 px-4 backdrop-blur">
      {/* Left: brand + project picker */}
      <div className="flex items-center gap-3">
        <Link href="/" className="flex items-center gap-2">
          <div className="grid h-7 w-7 place-items-center rounded-md bg-gradient-to-br from-indigo-400 to-indigo-600 text-[11px] font-bold text-white">
            GL
          </div>
          <span className="text-sm font-semibold text-zinc-100">GenAI Labs</span>
        </Link>
        <div className="ml-2 h-5 w-px bg-zinc-800" aria-hidden />
        <div ref={projectMenuRef} className="relative">
          <button
            type="button"
            onClick={() => setProjectMenuOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={projectMenuOpen}
            className="inline-flex items-center gap-1.5 rounded-md border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" aria-hidden />
            {currentProject?.name ?? "프로젝트"}
            <ChevronDown className="h-3.5 w-3.5 text-zinc-500" aria-hidden />
          </button>
          <AnimatePresence>
            {projectMenuOpen && (
              <motion.div
                role="menu"
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.12 }}
                className="absolute left-0 top-full z-40 mt-1 w-56 overflow-hidden rounded-md border border-zinc-800 bg-zinc-900 shadow-md"
              >
                {projects.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-zinc-500">
                    프로젝트가 없습니다
                  </div>
                ) : (
                  projects.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      role="menuitem"
                      onClick={() => handlePickProject(p.id)}
                      className={cn(
                        "flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-zinc-800",
                        p.id === currentProject?.id ? "text-indigo-200" : "text-zinc-200"
                      )}
                    >
                      <span className="truncate">{p.name}</span>
                      {p.id === currentProject?.id && (
                        <Check className="h-3.5 w-3.5 text-indigo-300" aria-hidden />
                      )}
                    </button>
                  ))
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>

      {/* Center: search trigger */}
      <button
        type="button"
        onClick={() => setSearchOpen(true)}
        aria-label="검색 (Cmd+K)"
        className="group flex w-[420px] items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-500 hover:border-zinc-700 hover:bg-zinc-800/80 hover:text-zinc-300"
      >
        <Search className="h-3.5 w-3.5" aria-hidden />
        <span className="flex-1 text-left">프롬프트 · 데이터셋 · 실험 검색…</span>
        <kbd className="rounded border border-zinc-700 bg-zinc-950 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
          ⌘K
        </kbd>
      </button>

      {/* Right: basket + bell + user */}
      <div className="flex items-center gap-2">
        {/* Compare Basket */}
        <div ref={basketRef} className="relative">
          <button
            type="button"
            onClick={() => setBasketOpen((o) => !o)}
            aria-label={`비교 장바구니 (${basketCount}개)`}
            aria-haspopup="menu"
            aria-expanded={basketOpen}
            className="relative grid h-8 w-8 place-items-center rounded-md text-zinc-300 hover:bg-zinc-800"
          >
            <GitCompare className="h-4 w-4" />
            {basketCount > 0 && (
              <span className="absolute right-0.5 top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-indigo-500 px-1 text-[10px] font-semibold text-white">
                {basketCount}
              </span>
            )}
          </button>
          <AnimatePresence>
            {basketOpen && (
              <motion.div
                role="menu"
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.12 }}
                className="absolute right-0 top-full z-40 mt-1 w-72 overflow-hidden rounded-md border border-zinc-800 bg-zinc-900 shadow-md"
              >
                <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
                  <span className="text-xs font-semibold text-zinc-200">
                    비교 장바구니 ({basketCount}/5)
                  </span>
                  {basketCount > 0 && (
                    <button
                      type="button"
                      onClick={() => basketClear()}
                      className="text-[10px] text-zinc-400 hover:text-rose-300"
                    >
                      비우기
                    </button>
                  )}
                </div>
                {basketCount === 0 ? (
                  <div className="px-3 py-6 text-center text-xs text-zinc-500">
                    비교할 항목이 없습니다.
                    <br />
                    실험/Run 카드의 + 버튼으로 추가하세요.
                  </div>
                ) : (
                  <>
                    <ul className="max-h-60 overflow-y-auto py-1">
                      {basketItems.map((id) => (
                        <li
                          key={id}
                          className="flex items-center justify-between gap-2 px-3 py-1.5 text-xs text-zinc-200 hover:bg-zinc-800/60"
                        >
                          <span className="truncate font-mono">{id}</span>
                          <button
                            type="button"
                            onClick={() => basketRemove(id)}
                            aria-label={`${id} 제거`}
                            className="grid h-6 w-6 shrink-0 place-items-center rounded text-zinc-500 hover:bg-zinc-800 hover:text-rose-300"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </li>
                      ))}
                    </ul>
                    <div className="border-t border-zinc-800 p-2">
                      <Link
                        href={compareHref}
                        onClick={() => setBasketOpen(false)}
                        className="block w-full rounded-md bg-indigo-500 px-3 py-1.5 text-center text-xs font-medium text-white hover:bg-indigo-400"
                      >
                        비교 보기 →
                      </Link>
                    </div>
                  </>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Notification Bell */}
        <div ref={bellRef} className="relative">
          <button
            type="button"
            onClick={() => setBellOpen((o) => !o)}
            aria-label={`알림 (${unreadCount}개 안 읽음)`}
            aria-haspopup="menu"
            aria-expanded={bellOpen}
            className="relative grid h-8 w-8 place-items-center rounded-md text-zinc-300 hover:bg-zinc-800"
          >
            <Bell className="h-4 w-4" />
            {unreadCount > 0 && (
              <span className="absolute right-1 top-1 grid h-4 min-w-4 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">
                {unreadCount}
              </span>
            )}
          </button>
          <AnimatePresence>
            {bellOpen && (
              <motion.div
                role="menu"
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.12 }}
                className="absolute right-0 top-full z-40 mt-1 w-80 overflow-hidden rounded-md border border-zinc-800 bg-zinc-900 shadow-md"
              >
                <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
                  <span className="text-xs font-semibold text-zinc-200">알림</span>
                  <button
                    type="button"
                    onClick={handleMarkAllRead}
                    disabled={unreadCount === 0}
                    className="text-[10px] text-zinc-400 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    모두 읽음
                  </button>
                </div>
                <ul className="max-h-80 overflow-y-auto">
                  {notifications.length === 0 ? (
                    <li className="px-3 py-8 text-center text-xs text-zinc-500">
                      새 알림이 없습니다.
                    </li>
                  ) : (
                    notifications.map((n) => (
                      <li key={n.id}>
                        <button
                          type="button"
                          onClick={() => handleNotificationClick(n)}
                          className={cn(
                            "flex w-full flex-col gap-0.5 border-b border-zinc-800/60 px-3 py-2.5 text-left transition-colors hover:bg-zinc-800/60",
                            !n.read && "bg-indigo-500/5"
                          )}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <span className={cn("text-xs font-medium", n.read ? "text-zinc-300" : "text-zinc-50")}>
                              {n.title}
                            </span>
                            {!n.read && (
                              <span
                                aria-hidden
                                className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400"
                              />
                            )}
                          </div>
                          {(n.body ?? n.message) && (
                            <span className="line-clamp-2 text-[11px] text-zinc-400">
                              {n.body ?? n.message}
                            </span>
                          )}
                          <span className="text-[10px] text-zinc-500">
                            {formatRelativeShort(n.created_at)}
                          </span>
                        </button>
                      </li>
                    ))
                  )}
                </ul>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* User pill */}
        <div className="ml-1 flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 py-1 pl-1.5 pr-2.5">
          <div className="grid h-6 w-6 place-items-center rounded-full bg-indigo-500 text-[11px] font-semibold text-white">
            {initial}
          </div>
          <span className="text-xs font-medium text-zinc-200">
            {user?.name ?? user?.email ?? "—"}
          </span>
          {user?.role && (
            <span className="rounded-full bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
              {user.role}
            </span>
          )}
        </div>
      </div>

      <SearchOverlay open={searchOpen} onClose={() => setSearchOpen(false)} />
    </header>
  );
}
