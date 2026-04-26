"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { prompts } from "@/lib/mock/data";
import type { Prompt } from "@/lib/mock/types";
import { formatNumber, formatRelativeDate } from "@/lib/utils";
import { PromptDetailPanel } from "./_components/PromptDetailPanel";

const LABEL_TONE: Record<
  "production" | "staging" | "draft",
  "success" | "warning" | "neutral"
> = {
  production: "success",
  staging: "warning",
  draft: "neutral",
};

export default function PromptsPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selected: Prompt | null =
    prompts.find((p) => p.id === selectedId) ?? null;

  return (
    <div className="px-8 py-6">
      <PageHeader
        title="프롬프트"
        description="버전 관리 + 라벨 기반 승격 워크플로우"
        actions={
          <Button variant="primary">
            <Plus className="h-4 w-4" aria-hidden />새 프롬프트
          </Button>
        }
      />

      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-800 bg-zinc-950/40 text-xs text-zinc-400">
              <tr>
                <th scope="col" className="px-4 py-2 text-left font-medium">
                  이름
                </th>
                <th scope="col" className="px-4 py-2 text-left font-medium">
                  최신 버전
                </th>
                <th scope="col" className="px-4 py-2 text-left font-medium">
                  라벨
                </th>
                <th scope="col" className="px-4 py-2 text-left font-medium">
                  최근 사용
                </th>
                <th scope="col" className="px-4 py-2 text-right font-medium">
                  사용 횟수
                </th>
              </tr>
            </thead>
            <tbody>
              {prompts.map((p) => (
                <tr
                  key={p.id}
                  onClick={() => setSelectedId(p.id)}
                  aria-label={`${p.name} 상세 열기`}
                  className="cursor-pointer border-t border-zinc-800 transition-colors hover:bg-zinc-900/60"
                >
                  <td className="px-4 py-3">
                    <div className="flex flex-col">
                      <span className="font-medium text-zinc-100">
                        {p.name}
                      </span>
                      {p.description && (
                        <span className="mt-0.5 line-clamp-1 text-xs text-zinc-500">
                          {p.description}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-zinc-300">
                    v{p.latestVersion}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {p.labels.length === 0 ? (
                        <span className="text-xs text-zinc-500">—</span>
                      ) : (
                        p.labels.map((l) => (
                          <Badge key={l} tone={LABEL_TONE[l]}>
                            {l}
                          </Badge>
                        ))
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-zinc-400">
                    {formatRelativeDate(p.lastUsed)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono tabular-nums text-xs text-zinc-300">
                    {formatNumber(p.usageCount)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <PromptDetailPanel
        prompt={selected}
        open={!!selectedId}
        onClose={() => setSelectedId(null)}
      />
    </div>
  );
}
