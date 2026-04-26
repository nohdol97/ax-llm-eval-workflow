"use client";

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";

interface Shortcut {
  keys: string[];
  description: string;
}

const SHORTCUTS: Shortcut[] = [
  { keys: ["⌘", "Enter"], description: "실행 (Run)" },
  { keys: ["⌘", "S"], description: "저장 (프롬프트/실험)" },
  { keys: ["⌘", "K"], description: "전역 검색 열기" },
  { keys: ["⌘", "N"], description: "새 실험" },
  { keys: ["⌘", "/"], description: "단축키 도움말" },
  { keys: ["Esc"], description: "중단 / 모달 닫기" },
  { keys: ["Tab"], description: "다음 포커스로 이동" },
  { keys: ["Shift", "Tab"], description: "이전 포커스로 이동" },
  { keys: ["⌘", "Shift", "1"], description: "단일 테스트 (Playground)" },
  { keys: ["⌘", "Shift", "2"], description: "결과 비교/분석" },
  { keys: ["⌘", "Shift", "3"], description: "데이터셋" },
  { keys: ["⌘", "Shift", "4"], description: "프롬프트" },
  { keys: ["⌘", "Shift", "5"], description: "평가" },
  { keys: ["⌘", "Shift", "6"], description: "설정" },
];

export function ShortcutsTab() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>단축키</CardTitle>
        <CardDescription>
          키보드 위주 워크플로우를 위한 단축키 모음입니다.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-zinc-800 rounded-md border border-zinc-800">
          {SHORTCUTS.map((s) => (
            <li
              key={s.description}
              className="flex items-center justify-between px-4 py-2.5"
            >
              <span className="text-sm text-zinc-200">{s.description}</span>
              <span className="flex items-center gap-1">
                {s.keys.map((k, i) => (
                  <span key={i} className="flex items-center gap-1">
                    <kbd className="rounded border border-zinc-700 bg-zinc-950 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
                      {k}
                    </kbd>
                    {i < s.keys.length - 1 && (
                      <span className="text-zinc-600">+</span>
                    )}
                  </span>
                ))}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
