"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Activity, Plus } from "lucide-react";
import { useAuth } from "@/lib/auth";
import {
  useAutoEvalPolicyList,
  useAutoEvalRunList,
  useDeleteAutoEvalPolicy,
  usePausePolicy,
  useResumePolicy,
  useRunPolicyNow,
} from "@/lib/hooks/useAutoEval";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import type { AutoEvalPolicy, PolicyStatus } from "@/lib/types/api";
import { cn } from "@/lib/utils";
import { PolicyCard } from "./_components/PolicyCard";

const DEFAULT_PROJECT_ID = "production-api";

type StatusFilter = "all" | PolicyStatus;

const STATUS_FILTERS: Array<{ id: StatusFilter; label: string }> = [
  { id: "all", label: "전체" },
  { id: "active", label: "활성" },
  { id: "paused", label: "일시정지" },
  { id: "deprecated", label: "지원 종료" },
];

export default function AutoEvalPage() {
  const { hasRole } = useAuth();
  const isAdmin = hasRole("admin");
  const projectId = DEFAULT_PROJECT_ID;
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");

  const { data, isLoading, error, refetch } = useAutoEvalPolicyList(projectId, {
    status: statusFilter === "all" ? undefined : statusFilter,
  });

  const pause = usePausePolicy();
  const resume = useResumePolicy();
  const runNow = useRunPolicyNow();
  const remove = useDeleteAutoEvalPolicy();

  const items = useMemo(() => data?.items ?? [], [data]);

  return (
    <div className="px-6 py-6">
      <PageHeader
        title="Auto-Eval Policies"
        description="Production agent를 자동 평가하는 정책을 관리합니다."
        actions={
          <Link
            href="/auto-eval/new"
            className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-400 active:bg-indigo-600"
          >
            <Plus className="h-4 w-4" aria-hidden />새 정책
          </Link>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {STATUS_FILTERS.map((f) => {
          const active = statusFilter === f.id;
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => setStatusFilter(f.id)}
              aria-pressed={active}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                active
                  ? "border-indigo-500 bg-indigo-500/15 text-indigo-200"
                  : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200",
              )}
            >
              {f.label}
            </button>
          );
        })}
      </div>

      {error ? (
        <EmptyState
          icon={<Activity className="h-8 w-8" />}
          title="정책 목록을 불러오지 못했습니다"
          description={(error as Error).message ?? "다시 시도해 주세요."}
          primaryAction={
            <Button variant="primary" onClick={() => refetch()}>
              재시도
            </Button>
          }
        />
      ) : isLoading ? (
        <PolicyGridSkeleton />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Activity className="h-12 w-12" />}
          title="아직 정책이 없습니다"
          description="첫 Auto-Eval 정책을 만들어 production agent를 매일 자동 평가하세요."
          primaryAction={
            <Link
              href="/auto-eval/new"
              className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-400"
            >
              <Plus className="h-4 w-4" aria-hidden />
              정책 만들기
            </Link>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {items.map((p) => (
            <PolicyCardWithLastRun
              key={p.id}
              policy={p}
              isAdmin={isAdmin}
              onPause={() => pause.mutate(p.id)}
              onResume={() => resume.mutate(p.id)}
              onRunNow={() => runNow.mutate(p.id)}
              onDelete={() => remove.mutate({ policyId: p.id })}
              isPausing={pause.isPending && pause.variables === p.id}
              isResuming={resume.isPending && resume.variables === p.id}
              isRunning={runNow.isPending && runNow.variables === p.id}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function PolicyCardWithLastRun(props: {
  policy: AutoEvalPolicy;
  isAdmin: boolean;
  onPause: () => void;
  onResume: () => void;
  onRunNow: () => void;
  onDelete: () => void;
  isPausing: boolean;
  isResuming: boolean;
  isRunning: boolean;
}) {
  // 마지막 run을 가져와 카드 메타에 노출
  const { data } = useAutoEvalRunList(props.policy.id, { pageSize: 1 });
  const lastRun = data?.items[0];
  return <PolicyCard {...props} lastRun={lastRun} />;
}

function PolicyGridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="h-[220px] animate-pulse rounded-lg border border-zinc-800 bg-zinc-900"
        />
      ))}
    </div>
  );
}
