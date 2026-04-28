"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Loader2,
  Pause,
  Play,
  Play as RunIcon,
  Trash2,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import {
  useAutoEvalPolicy,
  useAutoEvalRunList,
  useDeleteAutoEvalPolicy,
  usePausePolicy,
  useResumePolicy,
  useRunPolicyNow,
} from "@/lib/hooks/useAutoEval";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { ScoreBadge } from "@/components/ui/ScoreBadge";
import { Select } from "@/components/ui/Select";
import { StatusDot } from "@/components/ui/StatusDot";
import type { AutoEvalRunStatus, PolicyStatus } from "@/lib/types/api";
import {
  cn,
  formatCurrency,
  formatDuration,
  formatRelativeDate,
} from "@/lib/utils";
import {
  CostChart,
  EvaluatorBreakdownChart,
  RunHistoryChart,
} from "../_components/RunHistoryChart";
import { formatSchedule } from "../_components/scheduleFormat";

const STATUS_TONE: Record<PolicyStatus, "success" | "info" | "muted"> = {
  active: "success",
  paused: "info",
  deprecated: "muted",
};

const STATUS_LABEL: Record<PolicyStatus, string> = {
  active: "활성",
  paused: "일시정지",
  deprecated: "지원 종료",
};

const RUN_STATUS_FILTERS: Array<{
  id: "all" | AutoEvalRunStatus;
  label: string;
}> = [
  { id: "all", label: "전체" },
  { id: "completed", label: "완료" },
  { id: "running", label: "진행중" },
  { id: "failed", label: "실패" },
  { id: "skipped", label: "스킵" },
];

export default function AutoEvalPolicyDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const policyId = params?.id ?? "";
  const { hasRole } = useAuth();
  const isAdmin = hasRole("admin");

  const policyQuery = useAutoEvalPolicy(policyId);
  const [runStatusFilter, setRunStatusFilter] = useState<
    "all" | AutoEvalRunStatus
  >("all");
  const [page, setPage] = useState(1);

  const runListQuery = useAutoEvalRunList(policyId, {
    status: runStatusFilter === "all" ? undefined : runStatusFilter,
    page,
    pageSize: 20,
  });
  const allRunsQuery = useAutoEvalRunList(policyId, { pageSize: 100 });

  const pause = usePausePolicy();
  const resume = useResumePolicy();
  const runNow = useRunPolicyNow();
  const remove = useDeleteAutoEvalPolicy();

  const allRuns = useMemo(() => allRunsQuery.data?.items ?? [], [allRunsQuery]);
  const lastRun = allRuns[0];

  if (policyQuery.isError) {
    return (
      <div className="px-6 py-6">
        <EmptyState
          icon={<AlertTriangle className="h-8 w-8" />}
          title="정책을 불러오지 못했습니다"
          description={
            (policyQuery.error as Error)?.message ?? "다시 시도해 주세요."
          }
          primaryAction={
            <Button variant="primary" onClick={() => policyQuery.refetch()}>
              재시도
            </Button>
          }
          secondaryAction={
            <Link
              href="/auto-eval"
              className="inline-flex h-8 items-center gap-2 rounded-md border border-zinc-700 bg-transparent px-3 text-sm text-zinc-200 hover:bg-zinc-800"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden />
              목록
            </Link>
          }
        />
      </div>
    );
  }

  if (policyQuery.isLoading || !policyQuery.data) {
    return (
      <div className="px-6 py-6">
        <div className="flex items-center gap-2 text-zinc-400">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          정책 정보를 불러오는 중…
        </div>
      </div>
    );
  }

  const policy = policyQuery.data;
  const isActive = policy.status === "active";
  const isPaused = policy.status === "paused";

  const handleDelete = async () => {
    if (!confirm(`정책 "${policy.name}"을 삭제하시겠습니까?`)) return;
    await remove.mutateAsync({ policyId });
    router.push("/auto-eval");
  };

  return (
    <div className="px-6 py-6">
      <Link
        href="/auto-eval"
        className="mb-3 inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200"
      >
        <ArrowLeft className="h-3 w-3" aria-hidden />
        Auto-Eval 목록
      </Link>

      <PageHeader
        title={policy.name}
        description={policy.description ?? `정책 ID: ${policy.id}`}
        actions={
          <div className="flex items-center gap-2">
            <Badge tone={STATUS_TONE[policy.status]}>
              {STATUS_LABEL[policy.status]}
            </Badge>
            <Button
              variant="secondary"
              size="md"
              onClick={() => runNow.mutate(policy.id)}
              disabled={runNow.isPending || policy.status === "deprecated"}
            >
              <RunIcon className="h-4 w-4" aria-hidden />
              {runNow.isPending ? "실행 중…" : "즉시 실행"}
            </Button>
            {isActive && (
              <Button
                variant="ghost"
                onClick={() => pause.mutate(policy.id)}
                disabled={pause.isPending}
              >
                <Pause className="h-4 w-4" aria-hidden />
                일시정지
              </Button>
            )}
            {isPaused && (
              <Button
                variant="ghost"
                onClick={() => resume.mutate(policy.id)}
                disabled={resume.isPending}
              >
                <Play className="h-4 w-4" aria-hidden />
                재개
              </Button>
            )}
            {isAdmin && (
              <Button
                variant="ghost"
                onClick={handleDelete}
                disabled={remove.isPending}
                className="text-rose-300 hover:text-rose-200"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
                삭제
              </Button>
            )}
          </div>
        }
      />

      <section className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetaCard label="스케줄" value={formatSchedule(policy.schedule)} />
        <MetaCard
          label="일일 비용 한도"
          value={
            policy.daily_cost_limit_usd != null
              ? formatCurrency(policy.daily_cost_limit_usd, 2)
              : "—"
          }
        />
        <MetaCard
          label="최근 실행"
          value={
            policy.last_run_at ? formatRelativeDate(policy.last_run_at) : "없음"
          }
        />
        <MetaCard
          label="다음 실행"
          value={
            policy.next_run_at ? formatRelativeDate(policy.next_run_at) : "—"
          }
        />
      </section>

      <section className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="평균 스코어" value={lastRun?.avg_score ?? null} />
        <KpiCard label="통과율" value={lastRun?.pass_rate ?? null} />
        <KpiCard
          label="평가된 trace"
          rawValue={
            lastRun
              ? `${lastRun.traces_evaluated.toLocaleString()} / ${lastRun.traces_total.toLocaleString()}`
              : "—"
          }
        />
        <KpiCard
          label="비용"
          rawValue={lastRun ? formatCurrency(lastRun.cost_usd, 4) : "—"}
        />
      </section>

      <section className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <ChartCard title="스코어 / 통과율 추이">
          <RunHistoryChart runs={allRuns} />
        </ChartCard>
        <ChartCard title="일일 비용">
          <CostChart
            runs={allRuns}
            dailyLimitUsd={policy.daily_cost_limit_usd}
          />
        </ChartCard>
        <ChartCard
          title="Evaluator별 스코어"
          className="xl:col-span-2"
        >
          <EvaluatorBreakdownChart runs={allRuns} />
        </ChartCard>
      </section>

      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-zinc-300">
            실행 이력
          </h2>
          <div className="flex items-center gap-2">
            <Select
              aria-label="실행 상태 필터"
              value={runStatusFilter}
              onChange={(e) => {
                setRunStatusFilter(
                  e.target.value as "all" | AutoEvalRunStatus,
                );
                setPage(1);
              }}
              className="w-[140px]"
            >
              {RUN_STATUS_FILTERS.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {runListQuery.isLoading ? (
          <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-8 text-center text-sm text-zinc-500">
            실행 이력을 불러오는 중…
          </div>
        ) : (runListQuery.data?.items.length ?? 0) === 0 ? (
          <EmptyState
            title="실행 이력이 없습니다"
            description="아직 이 정책으로 실행된 evaluation run이 없습니다."
          />
        ) : (
          <div className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900">
            <table className="w-full table-fixed border-collapse text-sm">
              <thead>
                <tr className="border-b border-zinc-800 bg-zinc-950/40 text-xs uppercase tracking-wide text-zinc-500">
                  <th className="px-3 py-2 text-left">시작 시각</th>
                  <th className="px-3 py-2 text-left w-[110px]">상태</th>
                  <th className="px-3 py-2 text-right w-[140px]">평균 스코어</th>
                  <th className="px-3 py-2 text-right w-[140px]">통과율</th>
                  <th className="px-3 py-2 text-right w-[120px]">비용</th>
                  <th className="px-3 py-2 text-right w-[120px]">소요</th>
                  <th className="px-3 py-2 text-right w-[80px]">알림</th>
                </tr>
              </thead>
              <tbody>
                {runListQuery.data?.items.map((r) => (
                  <tr
                    key={r.id}
                    className="border-b border-zinc-900 last:border-b-0 hover:bg-zinc-800/40"
                  >
                    <td className="px-3 py-2 align-middle text-zinc-200">
                      {formatRelativeDate(r.started_at)}
                      <div className="text-[11px] text-zinc-500">{r.id}</div>
                    </td>
                    <td className="px-3 py-2 align-middle">
                      <StatusDot status={r.status} />
                    </td>
                    <td className="px-3 py-2 text-right align-middle">
                      <div className="inline-flex justify-end">
                        <ScoreBadge value={r.avg_score ?? null} />
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right align-middle">
                      <div className="inline-flex justify-end">
                        <ScoreBadge value={r.pass_rate ?? null} />
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right align-middle font-mono tabular-nums text-zinc-200">
                      {formatCurrency(r.cost_usd, 4)}
                    </td>
                    <td className="px-3 py-2 text-right align-middle text-xs text-zinc-400">
                      {r.duration_ms ? formatDuration(r.duration_ms) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right align-middle text-xs">
                      {r.triggered_alerts.length > 0 ? (
                        <Badge tone="warning">{r.triggered_alerts.length}</Badge>
                      ) : (
                        <span className="text-zinc-600">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {(runListQuery.data?.total ?? 0) >
          (runListQuery.data?.items.length ?? 0) && (
          <div className="mt-3 flex items-center justify-between text-xs text-zinc-500">
            <span>
              {runListQuery.data?.items.length ?? 0}개 표시 / 전체{" "}
              {runListQuery.data?.total}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="rounded-md border border-zinc-800 px-2 py-1 hover:bg-zinc-900 disabled:opacity-50"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
              >
                이전
              </button>
              <span>page {page}</span>
              <button
                type="button"
                className="rounded-md border border-zinc-800 px-2 py-1 hover:bg-zinc-900"
                onClick={() => setPage((p) => p + 1)}
              >
                다음
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function MetaCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">
        {label}
      </div>
      <div className="mt-1 truncate text-sm text-zinc-100">{value}</div>
    </div>
  );
}

function KpiCard({
  label,
  value,
  rawValue,
}: {
  label: string;
  value?: number | null;
  rawValue?: string;
}) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-3">
      <div className="text-[10px] uppercase tracking-wide text-zinc-500">
        {label}
      </div>
      <div className="mt-1.5">
        {rawValue !== undefined ? (
          <span className="font-mono text-lg tabular-nums text-zinc-100">
            {rawValue}
          </span>
        ) : (
          <ScoreBadge value={value ?? null} />
        )}
      </div>
    </div>
  );
}

function ChartCard({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-4",
        className,
      )}
    >
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-400">
        {title}
      </h3>
      {children}
    </div>
  );
}
