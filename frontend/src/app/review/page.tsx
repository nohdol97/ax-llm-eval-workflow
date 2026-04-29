"use client";

/**
 * Review Queue 목록 페이지 (Phase 8-C-9).
 *
 * AGENT_EVAL.md §19.1 명세 — KPI 카드 + 탭 + 큐 테이블.
 */
import Link from "next/link";
import { useMemo, useState } from "react";
import { ClipboardCheck, Plus } from "lucide-react";
import { useAuth } from "@/lib/auth";
import {
  useReviewItemList,
  useReviewSummary,
  type ReviewListFilters,
} from "@/lib/hooks/useReviews";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import type {
  ReviewItem,
  ReviewItemType,
  ReviewSeverity,
  ReviewStatus,
} from "@/lib/types/api";
import { cn } from "@/lib/utils";

const DEFAULT_PROJECT_ID = "production-api";

type Tab = "all" | "mine" | "high" | "user_report";

const TAB_DEFS: Array<{ id: Tab; label: string }> = [
  { id: "all", label: "전체" },
  { id: "mine", label: "내가 담당" },
  { id: "high", label: "높은 우선순위" },
  { id: "user_report", label: "User Report" },
];

const SEVERITY_LABEL: Record<ReviewSeverity, string> = {
  high: "🔴 high",
  medium: "🟡 medium",
  low: "🟢 low",
};

const TYPE_LABEL: Record<ReviewItemType, string> = {
  auto_eval_flagged: "auto_eval",
  judge_low_confidence: "judge_low",
  user_report: "user_report",
  manual_addition: "manual",
  evaluator_submission: "submission",
};

export default function ReviewQueuePage() {
  const { user, hasRole } = useAuth();
  const projectId = DEFAULT_PROJECT_ID;
  const [tab, setTab] = useState<Tab>("all");
  const [statusFilter, setStatusFilter] = useState<ReviewStatus | "all">(
    "open",
  );

  const filters: ReviewListFilters = useMemo(() => {
    const f: ReviewListFilters = {
      projectId,
      page: 1,
      pageSize: 50,
    };
    if (statusFilter !== "all") f.status = statusFilter;
    if (tab === "mine" && user?.id) {
      f.assignedTo = user.id;
      f.status = "in_review";
    } else if (tab === "high") {
      f.severity = "high";
    } else if (tab === "user_report") {
      f.type = "user_report";
    }
    return f;
  }, [projectId, statusFilter, tab, user?.id]);

  const { data, isLoading, error, refetch } = useReviewItemList(filters);
  const { data: summary } = useReviewSummary(projectId);

  const items = data?.items ?? [];
  const isReviewer = hasRole("reviewer");

  return (
    <div className="px-6 py-6">
      <PageHeader
        title="Review Queue"
        description="자동 평가 결과 + 사용자 신고를 reviewer 가 검토합니다."
        actions={
          isReviewer ? (
            <Link
              href="/review/new"
              className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-400 active:bg-indigo-600"
            >
              <Plus className="h-4 w-4" aria-hidden />
              수동 추가
            </Link>
          ) : null
        }
      />

      {/* KPI 카드 */}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard label="Open" value={summary?.open ?? 0} tone="default" />
        <KpiCard
          label="In Review"
          value={summary?.in_review ?? 0}
          tone="info"
        />
        <KpiCard
          label="Resolved (오늘)"
          value={summary?.resolved_today ?? 0}
          tone="success"
        />
        <KpiCard
          label="평균 처리 시간"
          value={
            summary?.avg_resolution_time_min != null
              ? `${summary.avg_resolution_time_min.toFixed(1)}분`
              : "—"
          }
          tone="default"
        />
      </div>

      {/* 탭 + 상태 필터 */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
          <TabsList>
            {TAB_DEFS.map((t) => (
              <TabsTrigger key={t.id} value={t.id}>
                {t.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {tab !== "mine" ? (
          <div className="flex items-center gap-1.5 text-xs">
            {(["open", "in_review", "resolved", "all"] as const).map((s) => {
              const active = statusFilter === s;
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => setStatusFilter(s)}
                  aria-pressed={active}
                  className={cn(
                    "rounded-full border px-3 py-1 font-medium transition-colors",
                    active
                      ? "border-indigo-500 bg-indigo-500/15 text-indigo-200"
                      : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200",
                  )}
                >
                  {s === "all" ? "전체" : s}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>

      {/* 테이블 */}
      <div className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950">
        {isLoading ? (
          <div className="p-8 text-center text-sm text-zinc-500">
            불러오는 중…
          </div>
        ) : error ? (
          <div className="p-8 text-center text-sm text-red-400">
            오류: {error.message}
            <Button
              size="sm"
              variant="ghost"
              className="ml-2"
              onClick={() => refetch()}
            >
              재시도
            </Button>
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            icon={<ClipboardCheck className="h-8 w-8" />}
            title="큐가 비어 있습니다"
            description="자동 평가가 새 항목을 큐에 추가하거나, 사용자가 신고하면 여기에 표시됩니다."
          />
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-800 bg-zinc-900/50 text-left text-xs font-medium text-zinc-400">
              <tr>
                <th className="px-3 py-2">우선순위</th>
                <th className="px-3 py-2">사유</th>
                <th className="px-3 py-2">type</th>
                <th className="px-3 py-2">Subject</th>
                <th className="px-3 py-2">자동 점수</th>
                <th className="px-3 py-2">상태</th>
                <th className="px-3 py-2">생성</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <ReviewRow key={it.id} item={it} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function KpiCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string;
  tone: "default" | "info" | "success" | "warn";
}) {
  const toneCls = {
    default: "text-zinc-200",
    info: "text-indigo-300",
    success: "text-green-300",
    warn: "text-yellow-300",
  }[tone];
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950 px-4 py-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={cn("mt-1 text-xl font-semibold", toneCls)}>{value}</div>
    </div>
  );
}

function ReviewRow({ item }: { item: ReviewItem }) {
  const weighted = item.automatic_scores?.weighted_score;
  const scoreText =
    typeof weighted === "number" ? weighted.toFixed(2) : "—";
  return (
    <tr className="border-b border-zinc-800/60 last:border-0 hover:bg-zinc-900/40">
      <td className="px-3 py-2">{SEVERITY_LABEL[item.severity]}</td>
      <td className="px-3 py-2 font-mono text-xs text-zinc-300">
        {item.reason}
      </td>
      <td className="px-3 py-2 text-xs text-zinc-400">
        {TYPE_LABEL[item.type]}
      </td>
      <td className="px-3 py-2 font-mono text-xs text-zinc-300">
        {item.subject_id.slice(0, 16)}…
      </td>
      <td className="px-3 py-2 text-zinc-300">{scoreText}</td>
      <td className="px-3 py-2 text-xs text-zinc-400">{item.status}</td>
      <td className="px-3 py-2 text-xs text-zinc-500">
        {new Date(item.created_at).toLocaleString("ko-KR")}
      </td>
      <td className="px-3 py-2 text-right">
        <Link
          href={`/review/${encodeURIComponent(item.id)}`}
          className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs font-medium text-zinc-200 transition-colors hover:border-indigo-500 hover:text-indigo-200"
        >
          상세
        </Link>
      </td>
    </tr>
  );
}
