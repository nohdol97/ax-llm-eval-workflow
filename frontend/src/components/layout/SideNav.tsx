"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BarChart3,
  Database,
  FileText,
  FlaskConical,
  HelpCircle,
  Settings,
  Target,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  match: (pathname: string) => boolean;
}

const ITEMS: NavItem[] = [
  {
    href: "/playground",
    label: "단일 테스트",
    icon: FlaskConical,
    match: (p) => p === "/" || p.startsWith("/playground") || p.startsWith("/experiments"),
  },
  {
    href: "/compare",
    label: "결과 비교/분석",
    icon: BarChart3,
    match: (p) => p.startsWith("/compare"),
  },
  {
    href: "/datasets",
    label: "데이터셋",
    icon: Database,
    match: (p) => p.startsWith("/datasets"),
  },
  {
    href: "/prompts",
    label: "프롬프트",
    icon: FileText,
    match: (p) => p.startsWith("/prompts"),
  },
  {
    href: "/evaluators",
    label: "평가 (Evaluator)",
    icon: Target,
    match: (p) => p.startsWith("/evaluators"),
  },
  {
    href: "/auto-eval",
    label: "Auto-Eval",
    icon: Activity,
    match: (p) => p.startsWith("/auto-eval"),
  },
  {
    href: "/settings",
    label: "설정",
    icon: Settings,
    match: (p) => p.startsWith("/settings"),
  },
];

export function SideNav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="주 네비게이션"
      className="sticky top-12 z-20 flex h-[calc(100dvh-3rem)] w-14 flex-col items-center justify-between border-r border-zinc-900 bg-zinc-950 py-3"
    >
      <ul className="flex flex-col items-center gap-1">
        {ITEMS.map((item) => {
          const Icon = item.icon;
          const active = item.match(pathname);
          return (
            <li key={item.href} className="group relative">
              <Link
                href={item.href}
                aria-label={item.label}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "grid h-10 w-10 place-items-center rounded-md transition-colors",
                  active
                    ? "bg-indigo-500/15 text-indigo-300"
                    : "text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200"
                )}
              >
                <Icon className="h-[18px] w-[18px]" />
              </Link>
              <span
                role="tooltip"
                className="pointer-events-none absolute left-12 top-1/2 z-50 -translate-y-1/2 whitespace-nowrap rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-200 opacity-0 shadow-md transition-opacity group-hover:opacity-100"
              >
                {item.label}
              </span>
            </li>
          );
        })}
      </ul>

      <button
        type="button"
        aria-label="단축키 도움말 (⌘+/)"
        className="grid h-10 w-10 place-items-center rounded-md text-zinc-500 hover:bg-zinc-900 hover:text-zinc-200"
      >
        <HelpCircle className="h-[18px] w-[18px]" />
      </button>
    </nav>
  );
}
