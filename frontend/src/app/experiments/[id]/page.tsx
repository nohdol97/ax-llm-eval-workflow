"use client";

import Link from "next/link";
import { notFound, useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  CircleCheck,
  Clock,
  DollarSign,
  Gauge,
  Pause,
  Play,
  RotateCw,
  Square,
  Trash2,
  TrendingUp,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusDot } from "@/components/ui/StatusDot";
import { RequireRole } from "@/lib/auth";
import {
  useDeleteExperiment,
  useExperimentControl,
  useExperimentDetail,
  type ExperimentControlAction,
} from "@/lib/hooks/useExperiments";
import { useExperimentStream } from "@/lib/hooks/useSSE";
import type {
  ExperimentStatus,
  ExperimentStreamEvent,
  RunSummary,
} from "@/lib/types/api";
import {
  cn,
  formatCurrency,
  formatDuration,
  formatNumber,
  formatRelativeDate,
} from "@/lib/utils";
import { RunProgressCard } from "../_components/RunProgressCard";

function getRunAvgScore(r: RunSummary): number | null {
  return (
    r.avg_score ?? r.summary?.avg_score ?? null
  );
}

function getRunCost(r: RunSummary): number {
  return r.total_cost ?? r.summary?.total_cost ?? 0;
}

function getRunLatency(r: RunSummary): number | null {
  return (
    r.avg_latency_ms ??
    r.summary?.avg_latency_ms ??
    r.summary?.avg_latency ??
    null
  );
}

export default function ExperimentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const experimentId = params.id;

  const { data: experiment, isLoading, error, refetch } = useExperimentDetail(
    experimentId
  );

  const runs = useMemo<RunSummary[]>(
    () => experiment?.runs ?? [],
    [experiment]
  );
  const experimentStatus: ExperimentStatus =
    experiment?.status ?? "pending";

  // SSE stream — only when running
  const [progress, setProgress] = useState<{
    completed: number;
    total: number;
  } | null>(null);

  useExperimentStream({
    experimentId: experimentStatus === "running" ? experimentId : null,
    enabled: experimentStatus === "running",
    onEvent: (evt: ExperimentStreamEvent) => {
      switch (evt.type) {
        case "progress":
          setProgress({
            completed: evt.data.completed,
            total: evt.data.total,
          });
          break;
        case "run_complete":
          refetch();
          break;
        case "experiment_complete":
          refetch();
          router.push(`/compare?experiment=${experimentId}`);
          break;
        default:
          break;
      }
    },
  });

  const control = useExperimentControl();
  const deleteExp = useDeleteExperiment();

  // Reset progress when experiment id changes
  useEffect(() => {
    setProgress(null);
  }, [experimentId]);

  if (error) {
    notFound();
  }
  if (isLoading || !experiment) {
    return (
      <div className="px-6 py-6">
        <div className="rounded-md border border-zinc-800 bg-zinc-900 px-4 py-8 text-center text-sm text-zinc-500">
          실험 정보를 불러오는 중…
        </div>
      </div>
    );
  }

  const totalItems =
    progress?.total ??
    experiment.progress?.total ??
    runs.reduce((s, r) => s + (r.items_total ?? 0), 0);
  const completedItems =
    progress?.completed ??
    experiment.progress?.completed ??
    experiment.progress?.processed ??
    runs.reduce((s, r) => s + (r.items_completed ?? 0), 0);
  const overallPct =
    totalItems === 0
      ? 0
      : Math.max(0, Math.min(100, (completedItems / totalItems) * 100));

  const completedRuns = runs.filter((r) => r.status === "completed").length;

  const aggregateScore = (() => {
    const scored = runs
      .map((r) => getRunAvgScore(r))
      .filter((v): v is number => v !== null);
    if (scored.length === 0) return null;
    return scored.reduce((s, v) => s + v, 0) / scored.length;
  })();

  const totalCost = runs.reduce((s, r) => s + getRunCost(r), 0);

  const aggregateLatency = (() => {
    const lats = runs
      .map((r) => getRunLatency(r))
      .filter((v): v is number => v !== null);
    if (lats.length === 0) return null;
    return lats.reduce((s, v) => s + v, 0) / lats.length;
  })();

  const remainingTimeMs = (() => {
    if (experimentStatus !== "running") return null;
    const remaining = totalItems - completedItems;
    if (remaining <= 0) return 0;
    const lat = aggregateLatency ?? 1000;
    const parallelRuns = Math.max(
      1,
      runs.filter((r) => r.status === "running").length
    );
    return Math.round((remaining * lat) / parallelRuns);
  })();

  // Pull metadata from config_snapshot for display
  const cfg = experiment.config_snapshot ?? {};
  const promptName =
    (cfg.prompt_configs as Array<{ name: string }> | undefined)?.[0]?.name ??
    "—";
  const promptVersions =
    ((cfg.prompt_configs as Array<{ version?: number }> | undefined) ?? [])
      .map((p) => p.version)
      .filter((v): v is number => typeof v === "number");
  const datasetName = (cfg.dataset_name as string | undefined) ?? "—";

  const callControl = (action: ExperimentControlAction) => {
    control.mutate({ experimentId, action });
  };
  const callDelete = () => {
    deleteExp.mutate(
      { experimentId },
      { onSuccess: () => router.push("/experiments") }
    );
  };

  const failedRunCount = runs.filter((r) => r.status === "failed").length;

  return (
    <div className="px-6 py-6">
      <PageHeader
        title={experiment.name}
        description={
          <span className="flex flex-wrap items-center gap-3 text-xs">
            <StatusDot status={experimentStatus} />
            <span className="text-zinc-500">
              생성 {formatRelativeDate(experiment.created_at)} ·{" "}
              {experiment.owner}
            </span>
            <span className="text-zinc-500">
              프롬프트 {promptName}
              {promptVersions.length > 0 && (
                <> ({promptVersions.map((v) => `v${v}`).join(" + ")})</>
              )}
            </span>
            <span className="text-zinc-500">데이터셋 {datasetName}</span>
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            {experimentStatus === "running" && (
              <>
                <Button
                  variant="secondary"
                  onClick={() => callControl("pause")}
                  disabled={control.isPending}
                  aria-label="실험 일시정지"
                >
                  <Pause className="h-4 w-4" aria-hidden />
                  일시정지
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => callControl("cancel")}
                  disabled={control.isPending}
                  aria-label="실험 중단"
                >
                  <Square className="h-4 w-4" aria-hidden />
                  중단
                </Button>
              </>
            )}
            {experimentStatus === "paused" && (
              <>
                <Button
                  variant="primary"
                  onClick={() => callControl("resume")}
                  disabled={control.isPending}
                  aria-label="실험 재개"
                >
                  <Play className="h-4 w-4" aria-hidden />
                  재개
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => callControl("cancel")}
                  disabled={control.isPending}
                  aria-label="실험 중단"
                >
                  <Square className="h-4 w-4" aria-hidden />
                  중단
                </Button>
              </>
            )}
            {experimentStatus === "failed" && failedRunCount > 0 && (
              <Button
                variant="secondary"
                onClick={() => callControl("retry-failed")}
                disabled={control.isPending}
                aria-label="실패한 Run 재시도"
              >
                <RotateCw className="h-4 w-4" aria-hidden />
                실패 재시도
              </Button>
            )}
            {experimentStatus === "completed" && (
              <Link
                href={`/compare?experiment=${experiment.experiment_id}`}
                className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-400"
              >
                결과 비교
                <ArrowRight className="h-4 w-4" aria-hidden />
              </Link>
            )}
            <RequireRole role="admin">
              <Button
                variant="ghost"
                onClick={() => {
                  if (confirm("이 실험을 삭제하시겠습니까?")) {
                    callDelete();
                  }
                }}
                disabled={deleteExp.isPending}
                aria-label="실험 삭제 (관리자)"
              >
                <Trash2 className="h-4 w-4" aria-hidden />
                삭제
              </Button>
            </RequireRole>
          </div>
        }
      />

      {control.error && (
        <div className="mb-4 rounded-md border border-rose-900/40 bg-rose-950/20 px-4 py-2 text-sm text-rose-200">
          제어 요청 실패: {(control.error as Error).message}
        </div>
      )}
      {deleteExp.error && (
        <div className="mb-4 rounded-md border border-rose-900/40 bg-rose-950/20 px-4 py-2 text-sm text-rose-200">
          삭제 실패: {(deleteExp.error as Error).message}
        </div>
      )}

      <div className="sr-only" role="status" aria-live="polite">
        실험 상태: {experimentStatus} · 진행률 {overallPct.toFixed(1)}%
      </div>

      {/* KPI cards */}
      <section className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          icon={<TrendingUp className="h-4 w-4" />}
          label="진행률"
          value={`${overallPct.toFixed(1)}%`}
          hint={`${formatNumber(completedItems)} / ${formatNumber(totalItems)} 아이템`}
        />
        <KpiCard
          icon={<Gauge className="h-4 w-4" />}
          label="평균 스코어"
          value={aggregateScore === null ? "—" : aggregateScore.toFixed(2)}
          hint={`${completedRuns} / ${runs.length} Runs 완료`}
          accent={aggregateScore !== null && aggregateScore >= 0.85}
        />
        <KpiCard
          icon={<DollarSign className="h-4 w-4" />}
          label="총 비용"
          value={formatCurrency(totalCost, 2)}
          hint={`평균 ${formatCurrency(
            runs.length > 0 ? totalCost / runs.length : 0,
            3
          )} / Run`}
        />
        <KpiCard
          icon={<Clock className="h-4 w-4" />}
          label="평균 지연"
          value={
            aggregateLatency === null
              ? "—"
              : formatDuration(aggregateLatency)
          }
          hint={
            experimentStatus === "running" && remainingTimeMs !== null
              ? `남은 시간 ≈ ${formatDuration(remainingTimeMs)}`
              : "p50 latency"
          }
        />
      </section>

      {/* Overall progress bar */}
      <Card className="mb-6">
        <CardContent>
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-sm font-medium text-zinc-200">
              전체 진행률
            </span>
            <span className="font-mono text-xs tabular-nums text-zinc-400">
              {formatNumber(completedItems)} / {formatNumber(totalItems)} 아이템
              완료 ({overallPct.toFixed(1)}%)
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
            <div
              className={cn(
                "h-full transition-all duration-500",
                experimentStatus === "completed"
                  ? "bg-emerald-400"
                  : experimentStatus === "failed"
                  ? "bg-rose-400"
                  : "bg-indigo-500"
              )}
              style={{ width: `${overallPct}%` }}
              aria-hidden
            />
          </div>
        </CardContent>
      </Card>

      {experimentStatus === "running" && (
        <section className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="rounded-md border border-amber-900/40 bg-amber-950/10 px-4 py-3">
            <div className="text-[11px] uppercase tracking-wide text-amber-300">
              예상 남은 시간
            </div>
            <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-zinc-50">
              {remainingTimeMs === null
                ? "—"
                : formatDuration(Math.max(remainingTimeMs, 0))}
            </div>
          </div>
          <div className="rounded-md border border-zinc-800 bg-zinc-900 px-4 py-3">
            <div className="text-[11px] uppercase tracking-wide text-zinc-400">
              현재까지의 비용
            </div>
            <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-zinc-50">
              {formatCurrency(totalCost, 2)}
            </div>
          </div>
        </section>
      )}

      {/* Runs */}
      <Card className="mb-6">
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Runs ({runs.length})</CardTitle>
          <span className="text-xs text-zinc-500">
            {completedRuns} 완료 ·{" "}
            {runs.filter((r) => r.status === "running").length} 진행중
          </span>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <p className="text-sm text-zinc-500">Run 데이터가 없습니다.</p>
          ) : (
            <ul className="space-y-2">
              {runs.map((run) => (
                <li key={run.run_name}>
                  <RunProgressCard run={run} />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {experimentStatus === "completed" && (
        <div className="mt-6 flex items-center justify-between rounded-md border border-emerald-900/40 bg-emerald-950/10 px-4 py-3 text-sm">
          <div className="flex items-center gap-2 text-emerald-300">
            <CircleCheck className="h-4 w-4" aria-hidden />
            <span>실험이 완료되었습니다. 결과 비교 페이지에서 분석하세요.</span>
          </div>
          <Link
            href={`/compare?experiment=${experiment.experiment_id}`}
            className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-300 hover:text-indigo-200"
          >
            결과 비교 <ArrowRight className="h-4 w-4" aria-hidden />
          </Link>
        </div>
      )}
    </div>
  );
}

function KpiCard({
  icon,
  label,
  value,
  hint,
  accent,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
  accent?: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded-md border border-zinc-800 bg-zinc-900 px-4 py-3",
        accent && "border-emerald-900/40 bg-emerald-950/10"
      )}
    >
      <div className="flex items-center gap-2 text-xs text-zinc-400">
        <span className="text-zinc-500">{icon}</span>
        <span>{label}</span>
      </div>
      <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-zinc-50">
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[11px] text-zinc-500">{hint}</div>}
    </div>
  );
}
