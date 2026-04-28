"use client";

import Link from "next/link";
import { Pause, Play, Play as RunIcon, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ScoreBadge } from "@/components/ui/ScoreBadge";
import type { AutoEvalPolicy, AutoEvalRun } from "@/lib/types/api";
import { formatCurrency, formatRelativeDate } from "@/lib/utils";
import { formatSchedule } from "./scheduleFormat";

interface PolicyCardProps {
  policy: AutoEvalPolicy;
  lastRun?: AutoEvalRun;
  isAdmin?: boolean;
  isPausing?: boolean;
  isResuming?: boolean;
  isRunning?: boolean;
  onPause?: () => void;
  onResume?: () => void;
  onRunNow?: () => void;
  onDelete?: () => void;
}

const STATUS_TONE: Record<
  AutoEvalPolicy["status"],
  "success" | "info" | "muted"
> = {
  active: "success",
  paused: "info",
  deprecated: "muted",
};

const STATUS_LABEL: Record<AutoEvalPolicy["status"], string> = {
  active: "활성",
  paused: "일시정지",
  deprecated: "지원 종료",
};

export function PolicyCard({
  policy,
  lastRun,
  isAdmin = false,
  isPausing = false,
  isResuming = false,
  isRunning = false,
  onPause,
  onResume,
  onRunNow,
  onDelete,
}: PolicyCardProps) {
  const isActive = policy.status === "active";
  const isPaused = policy.status === "paused";
  const isDeprecated = policy.status === "deprecated";

  return (
    <article
      aria-label={`Auto-Eval 정책 ${policy.name}`}
      className="flex h-full flex-col gap-4 rounded-lg border border-zinc-800 bg-zinc-900 p-4 shadow-[0_1px_2px_rgba(0,0,0,0.3)] transition-colors hover:border-zinc-700"
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            href={`/auto-eval/${encodeURIComponent(policy.id)}`}
            className="block truncate text-base font-semibold text-zinc-50 hover:text-indigo-200"
          >
            {policy.name}
          </Link>
          {policy.description && (
            <p className="mt-0.5 line-clamp-2 text-xs text-zinc-400">
              {policy.description}
            </p>
          )}
        </div>
        <Badge tone={STATUS_TONE[policy.status]}>
          {STATUS_LABEL[policy.status]}
        </Badge>
      </header>

      <dl className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <dt className="text-zinc-500">스케줄</dt>
          <dd className="mt-0.5 truncate text-zinc-200">
            {formatSchedule(policy.schedule)}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-500">샘플</dt>
          <dd className="mt-0.5 text-zinc-200">
            {policy.trace_filter.sample_size ?? "전체"}
            {policy.trace_filter.sample_strategy ? (
              <span className="ml-1 text-zinc-500">
                / {policy.trace_filter.sample_strategy}
              </span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt className="text-zinc-500">평가자</dt>
          <dd className="mt-0.5 text-zinc-200">
            {policy.evaluators.length}개
          </dd>
        </div>
        <div>
          <dt className="text-zinc-500">일일 한도</dt>
          <dd className="mt-0.5 text-zinc-200">
            {policy.daily_cost_limit_usd != null
              ? formatCurrency(policy.daily_cost_limit_usd, 2)
              : "—"}
          </dd>
        </div>
      </dl>

      <div className="flex items-center gap-3 rounded-md border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-xs text-zinc-300">
        <span className="text-zinc-500">최근 실행</span>
        <span className="text-zinc-300">
          {policy.last_run_at ? formatRelativeDate(policy.last_run_at) : "없음"}
        </span>
        {lastRun && lastRun.status === "completed" && (
          <>
            <span className="text-zinc-700">·</span>
            <span className="inline-flex items-center gap-1.5">
              <span className="text-zinc-500">통과율</span>
              <ScoreBadge value={lastRun.pass_rate ?? null} size="sm" />
            </span>
            <span className="text-zinc-700">·</span>
            <span className="font-mono tabular-nums text-zinc-200">
              {formatCurrency(lastRun.cost_usd, 4)}
            </span>
          </>
        )}
      </div>

      <div className="mt-auto flex flex-wrap items-center gap-2">
        <Link
          href={`/auto-eval/${encodeURIComponent(policy.id)}`}
          className="inline-flex h-7 items-center gap-1 rounded-md border border-zinc-700 px-2.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-zinc-800"
        >
          상세
        </Link>

        {!isDeprecated && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onRunNow}
            disabled={isRunning}
            aria-label={`${policy.name} 즉시 실행`}
          >
            <RunIcon className="h-3.5 w-3.5" aria-hidden />
            {isRunning ? "실행 중…" : "즉시 실행"}
          </Button>
        )}

        {isActive && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onPause}
            disabled={isPausing}
            aria-label={`${policy.name} 일시정지`}
          >
            <Pause className="h-3.5 w-3.5" aria-hidden />
            {isPausing ? "처리 중…" : "일시정지"}
          </Button>
        )}

        {isPaused && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onResume}
            disabled={isResuming}
            aria-label={`${policy.name} 재개`}
          >
            <Play className="h-3.5 w-3.5" aria-hidden />
            {isResuming ? "처리 중…" : "재개"}
          </Button>
        )}

        {isAdmin && onDelete && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            aria-label={`${policy.name} 삭제`}
            className="ml-auto text-rose-300 hover:text-rose-200"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
            삭제
          </Button>
        )}
      </div>
    </article>
  );
}
