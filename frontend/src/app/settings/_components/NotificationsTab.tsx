"use client";

import { useState } from "react";
import { Bell } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

interface ToggleItem {
  id: string;
  label: string;
  description: string;
  defaultOn: boolean;
}

const ITEMS: ToggleItem[] = [
  {
    id: "exp_complete",
    label: "실험 완료 알림",
    description: "Run 8개 모두 종료되면 알림을 받습니다.",
    defaultOn: true,
  },
  {
    id: "exp_failed",
    label: "실험 실패 알림",
    description: "Run이 실패하거나 중단되면 즉시 알림을 받습니다.",
    defaultOn: true,
  },
  {
    id: "evaluator_approved",
    label: "평가 함수 승인 알림",
    description: "내가 제출한 Custom 평가 함수의 승인/반려 결과 알림.",
    defaultOn: true,
  },
];

export function NotificationsTab() {
  const [state, setState] = useState<Record<string, boolean>>(
    Object.fromEntries(ITEMS.map((it) => [it.id, it.defaultOn]))
  );
  const [browserPerm, setBrowserPerm] = useState<"default" | "granted">(
    "default"
  );

  const toggle = (id: string) =>
    setState((s) => ({ ...s, [id]: !s[id] }));

  const requestBrowser = () => setBrowserPerm("granted");

  return (
    <Card>
      <CardHeader>
        <CardTitle>알림</CardTitle>
        <CardDescription>
          이메일/브라우저 알림 채널 설정. 채널 자체는 사내 Auth 프로필에서 변경하세요.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2.5">
        {ITEMS.map((it) => {
          const on = state[it.id] ?? it.defaultOn;
          return (
            <label
              key={it.id}
              className="flex cursor-pointer items-start justify-between gap-4 rounded-md border border-zinc-800 bg-zinc-950/40 px-4 py-3 hover:bg-zinc-900/60"
            >
              <div>
                <span className="text-sm font-medium text-zinc-100">
                  {it.label}
                </span>
                <p className="mt-0.5 text-xs text-zinc-400">{it.description}</p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={on}
                onClick={() => toggle(it.id)}
                aria-label={it.label}
                className={cn(
                  "relative h-5 w-9 shrink-0 rounded-full transition-colors",
                  on ? "bg-indigo-500" : "bg-zinc-700"
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform",
                    on ? "translate-x-[18px]" : "translate-x-0.5"
                  )}
                />
              </button>
              <input
                type="checkbox"
                checked={on}
                onChange={() => toggle(it.id)}
                className="sr-only"
                aria-hidden
                tabIndex={-1}
              />
            </label>
          );
        })}

        <div className="mt-4 flex items-center justify-between gap-3 rounded-md border border-zinc-800 bg-zinc-950/40 px-4 py-3">
          <div className="flex items-start gap-3">
            <span
              aria-hidden
              className="grid h-8 w-8 place-items-center rounded-md bg-indigo-500/10 text-indigo-300"
            >
              <Bell className="h-4 w-4" />
            </span>
            <div>
              <p className="text-sm font-medium text-zinc-100">
                브라우저 알림 권한
              </p>
              <p className="mt-0.5 text-xs text-zinc-400">
                탭이 백그라운드일 때도 알림을 받으려면 권한이 필요합니다.
              </p>
            </div>
          </div>
          <Button
            variant={browserPerm === "granted" ? "ghost" : "primary"}
            onClick={requestBrowser}
            disabled={browserPerm === "granted"}
          >
            {browserPerm === "granted" ? "허용됨" : "권한 요청"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
