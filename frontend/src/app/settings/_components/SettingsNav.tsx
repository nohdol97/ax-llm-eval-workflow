"use client";

import {
  Bell,
  Cog,
  Cpu,
  Keyboard,
  Settings as SettingsIcon,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type SettingsTab =
  | "general"
  | "models"
  | "params"
  | "notifications"
  | "shortcuts";

const ITEMS: { value: SettingsTab; label: string; icon: LucideIcon }[] = [
  { value: "general", label: "일반", icon: SettingsIcon },
  { value: "models", label: "모델 목록", icon: Cpu },
  { value: "params", label: "기본 파라미터", icon: Cog },
  { value: "notifications", label: "알림", icon: Bell },
  { value: "shortcuts", label: "단축키", icon: Keyboard },
];

export function SettingsNav({
  value,
  onChange,
}: {
  value: SettingsTab;
  onChange: (next: SettingsTab) => void;
}) {
  return (
    <nav
      aria-label="설정 분류"
      className="flex flex-col gap-0.5 rounded-lg border border-zinc-800 bg-zinc-900 p-2"
    >
      {ITEMS.map((it) => {
        const Icon = it.icon;
        const active = value === it.value;
        return (
          <button
            key={it.value}
            type="button"
            onClick={() => onChange(it.value)}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm transition-colors",
              active
                ? "bg-indigo-500/10 text-indigo-200"
                : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
            )}
          >
            <Icon className="h-4 w-4" aria-hidden />
            <span className="font-medium">{it.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
