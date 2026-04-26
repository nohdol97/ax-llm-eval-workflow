"use client";

import Link from "next/link";
import { notFound, useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  CircleCheck,
  Clock,
  DollarSign,
  Gauge,
  Pause,
  Play,
  Square,
  Target,
  TrendingUp,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { PageHeader } from "@/components/ui/PageHeader";
import { ScoreBadge } from "@/components/ui/ScoreBadge";
import { StatusDot } from "@/components/ui/StatusDot";
import { evaluators, experiments, runsByExperiment } from "@/lib/mock/data";
import type { ExperimentStatus, Run } from "@/lib/mock/types";
import {
  cn,
  formatCurrency,
  formatDuration,
  formatNumber,
  formatRelativeDate,
} from "@/lib/utils";
import { RunProgressCard } from "../_components/RunProgressCard";

export default function ExperimentDetailPage() {
  const params = useParams<{ id: string }>();
  const experimentId = params.id;

  const experiment = useMemo(
    () => experiments.find((e) => e.id === experimentId),
    [experimentId]
  );

  const initialRuns = useMemo<Run[]>(
    () => runsByExperiment[experimentId] ?? [],
    [experimentId]
  );

  const [runs, setRuns] = useState<Run[]>(initialRuns);
  const [experimentStatus, setExperimentStatus] = useState<ExperimentStatus>(
    experiment?.status ?? "completed"
  );

  // Sync local state when params change
  useEffect(() => {
    setRuns(initialRuns);
    setExperimentStatus(experiment?.status ?? "completed");
  }, [initialRuns, experiment?.status]);

  // Tick simulation: when experiment is "running", incrementally advance any
  // running run by +1 item per second until it completes.
  useEffect(() => {
    if (experimentStatus !== "running") return;
    const interval = window.setInterval(() => {
      setRuns((prev) => {
        let anyRunning = false;
        const next = prev.map((r) => {
          if (r.status !== "running") return r;
          if (r.itemsCompleted >= r.itemsTotal) {
            return { ...r, status: "completed" as ExperimentStatus };
          }
          anyRunning = true;
          const inc = Math.min(1, r.itemsTotal - r.itemsCompleted);
          const itemsCompleted = r.itemsCompleted + inc;
          const isDone = itemsCompleted >= r.itemsTotal;
          return {
            ...r,
            itemsCompleted,
            status: isDone
              ? ("completed" as ExperimentStatus)
              : ("running" as ExperimentStatus),
          };
        });
        if (!anyRunning) {
          // Auto-complete experiment when no more running runs
          setExperimentStatus("completed");
        }
        return next;
      });
    }, 1000);
    return () => window.clearInterval(interval);
  }, [experimentStatus]);

  if (!experiment) {
    notFound();
  }

  const totalItems = runs.reduce((sum, r) => sum + r.itemsTotal, 0);
  const completedItems = runs.reduce((sum, r) => sum + r.itemsCompleted, 0);
  const overallPct =
    totalItems === 0
      ? 0
      : Math.max(0, Math.min(100, (completedItems / totalItems) * 100));

  const completedRuns = runs.filter((r) => r.status === "completed").length;

  const aggregateScore = useMemo(() => {
    const scored = runs.filter((r) => r.avgScore !== null);
    if (scored.length === 0) return null;
    const sum = scored.reduce((s, r) => s + (r.avgScore ?? 0), 0);
    return sum / scored.length;
  }, [runs]);

  const totalCost = runs.reduce((s, r) => s + r.totalCostUsd, 0);

  const aggregateLatency = useMemo(() => {
    const withLat = runs.filter((r) => r.avgLatencyMs !== null);
    if (withLat.length === 0) return null;
    const sum = withLat.reduce((s, r) => s + (r.avgLatencyMs ?? 0), 0);
    return sum / withLat.length;
  }, [runs]);

  // Estimated remaining time: items left × avg latency per run, divided by parallel runs
  const remainingTimeMs = useMemo(() => {
    if (experimentStatus !== "running") return null;
    const remaining = totalItems - completedItems;
    if (remaining <= 0) return 0;
    const lat = aggregateLatency ?? 1000;
    const parallelRuns = Math.max(
      1,
      runs.filter((r) => r.status === "running").length
    );
    return Math.round((remaining * lat) / parallelRuns);
  }, [
    experimentStatus,
    totalItems,
    completedItems,
    aggregateLatency,
    runs,
  ]);

  const usedEvaluators = useMemo(
    () =>
      experiment.evaluatorIds
        .map((id) => evaluators.find((e) => e.id === id))
        .filter((e): e is NonNullable<typeof e> => !!e),
    [experiment.evaluatorIds]
  );

  const handlePause = () => setExperimentStatus("paused");
  const handleResume = () => setExperimentStatus("running");
  const handleCancel = () => setExperimentStatus("cancelled");

  return (
    <div className="px-6 py-6">
      <PageHeader
        title={experiment.name}
        description={
          <span className="flex flex-wrap items-center gap-3 text-xs">
            <StatusDot status={experimentStatus} />
            <span className="text-zinc-500">
              생성 {formatRelativeDate(experiment.createdAt)} ·{" "}
              {experiment.owner}
            </span>
            <span className="text-zinc-500">
              프롬프트 {experiment.promptName} (
              {experiment.promptVersions.map((v) => `v${v}`).join(" + ")})
            </span>
            <span className="text-zinc-500">
              데이터셋 {experiment.datasetName} ({experiment.itemCount} items)
            </span>
          </span>
        }
        actions={
          <div className="flex items-center gap-2">
            {experimentStatus === "running" && (
              <>
                <Button
                  variant="secondary"
                  onClick={handlePause}
                  aria-label="실험 일시정지"
                >
                  <Pause className="h-4 w-4" aria-hidden />
                  일시정지
                </Button>
                <Button
                  variant="destructive"
                  onClick={handleCancel}
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
                  onClick={handleResume}
                  aria-label="실험 재개"
                >
                  <Play className="h-4 w-4" aria-hidden />
                  재개
                </Button>
                <Button
                  variant="destructive"
                  onClick={handleCancel}
                  aria-label="실험 중단"
                >
                  <Square className="h-4 w-4" aria-hidden />
                  중단
                </Button>
              </>
            )}
            {experimentStatus === "completed" && (
              <Link
                href={`/compare?experiment=${experiment.id}`}
                className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-400"
              >
                결과 비교
                <ArrowRight className="h-4 w-4" aria-hidden />
              </Link>
            )}
          </div>
        }
      />

      {/* a11y live region for status changes */}
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

      {/* Live cost/eta when running */}
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
            {completedRuns} 완료 · {runs.filter((r) => r.status === "running").length}{" "}
            진행중
          </span>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <p className="text-sm text-zinc-500">Run 데이터가 없습니다.</p>
          ) : (
            <ul className="space-y-2">
              {runs.map((run) => (
                <li key={run.id}>
                  <RunProgressCard run={run} />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Evaluator score table */}
      <Card>
        <CardHeader>
          <CardTitle>평가 함수별 점수</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {usedEvaluators.length === 0 ? (
            <p className="text-sm text-zinc-500">평가 함수 정보가 없습니다.</p>
          ) : (
            <table className="w-full min-w-[640px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-zinc-800">
                  <th
                    scope="col"
                    className="py-2 pr-4 text-left text-xs font-medium uppercase tracking-wide text-zinc-500"
                  >
                    평가 함수
                  </th>
                  {runs.map((r) => (
                    <th
                      key={r.id}
                      scope="col"
                      className="py-2 pr-4 text-right text-xs font-medium uppercase tracking-wide text-zinc-500"
                    >
                      <div className="flex flex-col items-end gap-0.5">
                        <span className="text-zinc-300">
                          v{r.promptVersion} · {r.modelName}
                        </span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {usedEvaluators.map((ev) => (
                  <tr
                    key={ev.id}
                    className="border-b border-zinc-900 last:border-b-0"
                  >
                    <td className="py-2 pr-4 align-middle">
                      <div className="flex items-center gap-2">
                        <Target
                          className="h-3.5 w-3.5 text-zinc-500"
                          aria-hidden
                        />
                        <span className="text-zinc-200">{ev.name}</span>
                        <span className="font-mono text-[10px] text-zinc-500">
                          {ev.range}
                        </span>
                      </div>
                    </td>
                    {runs.map((r) => {
                      const score = r.scoresByEvaluator[ev.id];
                      return (
                        <td
                          key={r.id}
                          className="py-2 pr-4 text-right align-middle"
                        >
                          <div className="inline-flex justify-end">
                            <ScoreBadge
                              value={score === undefined ? null : score}
                              size="sm"
                            />
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
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
            href={`/compare?experiment=${experiment.id}`}
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
