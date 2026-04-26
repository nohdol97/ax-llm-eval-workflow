"use client";

import { Bell, ChevronDown, Search } from "lucide-react";
import { currentProject, currentUser, notifications, projects } from "@/lib/mock/data";

export function TopBar() {
  const unread = notifications.filter((n) => !n.read).length;
  return (
    <header className="sticky top-0 z-30 flex h-12 items-center justify-between border-b border-zinc-900 bg-zinc-950/95 px-4 backdrop-blur">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="grid h-7 w-7 place-items-center rounded-md bg-gradient-to-br from-indigo-400 to-indigo-600 text-[11px] font-bold text-white">
            GL
          </div>
          <span className="text-sm font-semibold text-zinc-100">
            GenAI Labs
          </span>
        </div>
        <div className="ml-2 h-5 w-px bg-zinc-800" />
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-zinc-800 bg-zinc-900 px-2.5 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          {currentProject.name}
          <ChevronDown className="h-3.5 w-3.5 text-zinc-500" aria-hidden />
        </button>
        <select
          aria-label="프로젝트 변경"
          className="sr-only"
          defaultValue={currentProject.id}
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <button
        type="button"
        className="group flex w-[420px] items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-500 hover:border-zinc-700 hover:bg-zinc-800/80 hover:text-zinc-300"
      >
        <Search className="h-3.5 w-3.5" aria-hidden />
        <span className="flex-1 text-left">
          프롬프트 · 데이터셋 · 실험 검색…
        </span>
        <kbd className="rounded border border-zinc-700 bg-zinc-950 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
          ⌘K
        </kbd>
      </button>

      <div className="flex items-center gap-2">
        <button
          type="button"
          aria-label="알림"
          className="relative grid h-8 w-8 place-items-center rounded-md text-zinc-300 hover:bg-zinc-800"
        >
          <Bell className="h-4 w-4" />
          {unread > 0 && (
            <span className="absolute right-1 top-1 grid h-4 min-w-4 place-items-center rounded-full bg-rose-500 px-1 text-[10px] font-semibold text-white">
              {unread}
            </span>
          )}
        </button>
        <div className="ml-1 flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900 py-1 pl-1.5 pr-2.5">
          <div className="grid h-6 w-6 place-items-center rounded-full bg-indigo-500 text-[11px] font-semibold text-white">
            {currentUser.initials}
          </div>
          <span className="text-xs font-medium text-zinc-200">
            {currentUser.name}
          </span>
          <span className="rounded-full bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
            {currentUser.role}
          </span>
        </div>
      </div>
    </header>
  );
}
