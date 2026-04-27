"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { useAuth } from "@/lib/auth";
import { usePromptList } from "@/lib/hooks/usePrompts";
import type { PromptSummary } from "@/lib/types/api";
import { formatRelativeDate } from "@/lib/utils";
import { PromptDetailPanel } from "./_components/PromptDetailPanel";

const LABEL_TONE: Record<string, "success" | "warning" | "neutral"> = {
  production: "success",
  staging: "warning",
  draft: "neutral",
};

const DEFAULT_PROJECT_ID = "production-api";

export default function PromptsPage() {
  const { user } = useAuth();
  const projectId =
    (user as { currentProjectId?: string } | null)?.currentProjectId ??
    DEFAULT_PROJECT_ID;
  const [selectedName, setSelectedName] = useState<string | null>(null);

  const { data, isLoading, isError, refetch } = usePromptList(projectId);
  const list: PromptSummary[] = data?.items ?? [];

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

      {isLoading ? (
        <Card>
          <div className="space-y-2 p-4">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-10 animate-pulse rounded bg-zinc-900/50" />
            ))}
          </div>
        </Card>
      ) : isError ? (
        <EmptyState
          title="프롬프트를 불러오지 못했습니다"
          description="네트워크 또는 서버 오류입니다."
          primaryAction={
            <Button variant="primary" onClick={() => refetch?.()}>
              재시도
            </Button>
          }
        />
      ) : list.length === 0 ? (
        <EmptyState
          title="프롬프트가 없습니다"
          description="새 프롬프트를 만들어 시작하세요."
        />
      ) : (
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
                    생성일
                  </th>
                  <th scope="col" className="px-4 py-2 text-left font-medium">
                    태그
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.map((p) => (
                  <tr
                    key={p.name}
                    onClick={() => setSelectedName(p.name)}
                    aria-label={`${p.name} 상세 열기`}
                    className="cursor-pointer border-t border-zinc-800 transition-colors hover:bg-zinc-900/60"
                  >
                    <td className="px-4 py-3">
                      <span className="font-medium text-zinc-100">{p.name}</span>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-zinc-300">
                      v{p.latest_version}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {p.labels.length === 0 ? (
                          <span className="text-xs text-zinc-500">—</span>
                        ) : (
                          p.labels.map((l) => (
                            <Badge key={l} tone={LABEL_TONE[l] ?? "neutral"}>
                              {l}
                            </Badge>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-zinc-400">
                      {p.created_at ? formatRelativeDate(p.created_at) : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {(p.tags ?? []).length === 0 ? (
                          <span className="text-xs text-zinc-500">—</span>
                        ) : (
                          p.tags.map((t) => (
                            <Badge key={t} tone="muted">
                              {t}
                            </Badge>
                          ))
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <PromptDetailPanel
        promptName={selectedName}
        projectId={projectId}
        open={!!selectedName}
        onClose={() => setSelectedName(null)}
      />
    </div>
  );
}
