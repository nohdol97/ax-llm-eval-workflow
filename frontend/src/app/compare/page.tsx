"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Check, GitCompare, Link2, ScanSearch } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { StatusDot } from "@/components/ui/StatusDot";
import {
  experiments as allExperiments,
  generateItemResults,
  runsByExperiment,
} from "@/lib/mock/data";
import type { Experiment, ItemResult, Run } from "@/lib/mock/types";
import { cn } from "@/lib/utils";
import { ComparisonTabs } from "./_components/ComparisonTabs";
import { CostChart } from "./_components/CostChart";
import { ItemDiffTable } from "./_components/ItemDiffTable";
import { KpiCards } from "./_components/KpiCards";
import { LatencyChart } from "./_components/LatencyChart";
import { RunStats } from "./_components/RunStats";
import { ScoreChart } from "./_components/ScoreChart";
import { ScoreDistributionChart } from "./_components/ScoreDistributionChart";
import { TokenChart } from "./_components/TokenChart";
import { colorForIndex } from "./_components/colors";
import type {
  CompareTab,
  CostBreakdown,
  LatencyPercentiles,
  RunStatsSummary,
  SelectedRun,
  TokenBreakdown,
} from "./_components/types";

const COMPARE_STATUSES = new Set(["completed", "running"]);

function getCompareEligibleExperiments(): Experiment[] {
  return allExperiments.filter((e) => COMPARE_STATUSES.has(e.status));
}

function buildShortLabel(run: Run): string {
  return `${run.modelName} · v${run.promptVersion}`;
}

function percentile(sortedAsc: number[], p: number): number {
  if (sortedAsc.length === 0) return 0;
  if (sortedAsc.length === 1) return sortedAsc[0];
  const idx = (sortedAsc.length - 1) * p;
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sortedAsc[lo];
  const frac = idx - lo;
  return sortedAsc[lo] * (1 - frac) + sortedAsc[hi] * frac;
}

function ComparePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const eligibleExperiments = useMemo(
    () => getCompareEligibleExperiments(),
    []
  );

  const initialExperimentId = useMemo(() => {
    const fromParam = searchParams.get("experiment");
    if (fromParam && eligibleExperiments.some((e) => e.id === fromParam)) {
      return fromParam;
    }
    return eligibleExperiments[0]?.id ?? "";
  }, [searchParams, eligibleExperiments]);

  const [experimentId, setExperimentId] = useState<string>(initialExperimentId);
  const runsForExperiment = useMemo<Run[]>(
    () => runsByExperiment[experimentId] ?? [],
    [experimentId]
  );

  const [selectedRunIds, setSelectedRunIds] = useState<string[]>(() =>
    runsForExperiment.map((r) => r.id)
  );

  const [activeTab, setActiveTab] = useState<CompareTab>("score");
  const [linkCopied, setLinkCopied] = useState(false);

  // 실험 변경 시 모든 Run 자동 선택 + URL 동기화
  const handleExperimentChange = useCallback(
    (next: string) => {
      setExperimentId(next);
      const nextRuns = runsByExperiment[next] ?? [];
      setSelectedRunIds(nextRuns.map((r) => r.id));
      const params = new URLSearchParams(searchParams.toString());
      params.set("experiment", next);
      router.replace(`/compare?${params.toString()}`, { scroll: false });
    },
    [router, searchParams]
  );

  // 초기 마운트 시 URL에 experiment가 없으면 추가
  useEffect(() => {
    if (!searchParams.get("experiment") && experimentId) {
      const params = new URLSearchParams(searchParams.toString());
      params.set("experiment", experimentId);
      router.replace(`/compare?${params.toString()}`, { scroll: false });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleRun = (runId: string) => {
    setSelectedRunIds((prev) =>
      prev.includes(runId) ? prev.filter((id) => id !== runId) : [...prev, runId]
    );
  };

  const selectedExperiment = useMemo(
    () => eligibleExperiments.find((e) => e.id === experimentId) ?? null,
    [eligibleExperiments, experimentId]
  );

  // 선택된 Run에 색상 부여 (experiment의 원래 인덱스 기준으로 안정화)
  const selectedRuns = useMemo<SelectedRun[]>(() => {
    return runsForExperiment
      .filter((r) => selectedRunIds.includes(r.id))
      .map((r) => {
        const originalIdx = runsForExperiment.findIndex((x) => x.id === r.id);
        return {
          ...r,
          color: colorForIndex(originalIdx),
          shortLabel: buildShortLabel(r),
        };
      });
  }, [runsForExperiment, selectedRunIds]);

  // ItemResult는 비교 활성 시점에만 생성
  const itemResults = useMemo<ItemResult[]>(() => {
    if (selectedRuns.length < 2) return [];
    return generateItemResults(experimentId);
  }, [experimentId, selectedRuns.length]);

  // 통계 집계
  const scoreStats = useMemo<RunStatsSummary[]>(() => {
    return selectedRuns.map((r) => {
      const scores = itemResults
        .map((it) => it.scoresByRun[r.id])
        .filter((v): v is number => v !== null && v !== undefined);
      const validCount = scores.length;
      const totalCount = itemResults.length;
      if (validCount === 0) {
        return {
          runId: r.id,
          shortLabel: r.shortLabel,
          modelName: r.modelName,
          promptVersion: r.promptVersion,
          color: r.color,
          avgScore: r.avgScore ?? 0,
          stdDev: 0,
          min: 0,
          max: 0,
          validCount: 0,
          totalCount,
        };
      }
      const avg = scores.reduce((s, v) => s + v, 0) / validCount;
      const variance =
        scores.reduce((s, v) => s + (v - avg) ** 2, 0) / validCount;
      const stdDev = Math.sqrt(variance);
      return {
        runId: r.id,
        shortLabel: r.shortLabel,
        modelName: r.modelName,
        promptVersion: r.promptVersion,
        color: r.color,
        avgScore: avg,
        stdDev,
        min: Math.min(...scores),
        max: Math.max(...scores),
        validCount,
        totalCount,
      };
    });
  }, [selectedRuns, itemResults]);

  const latencyStats = useMemo<LatencyPercentiles[]>(() => {
    return selectedRuns.map((r) => {
      const lats = itemResults
        .map((it) => it.latenciesByRun[r.id])
        .filter((v): v is number => typeof v === "number")
        .sort((a, b) => a - b);
      if (lats.length === 0) {
        return {
          runId: r.id,
          shortLabel: r.shortLabel,
          color: r.color,
          p50: r.avgLatencyMs ?? 0,
          p90: r.avgLatencyMs ?? 0,
          p99: r.avgLatencyMs ?? 0,
          avg: r.avgLatencyMs ?? 0,
        };
      }
      const avg = lats.reduce((s, v) => s + v, 0) / lats.length;
      return {
        runId: r.id,
        shortLabel: r.shortLabel,
        color: r.color,
        p50: percentile(lats, 0.5),
        p90: percentile(lats, 0.9),
        p99: percentile(lats, 0.99),
        avg,
      };
    });
  }, [selectedRuns, itemResults]);

  const costStats = useMemo<CostBreakdown[]>(() => {
    return selectedRuns.map((r) => {
      // 입력/출력 비용 분할: 토큰 비율로 분배 (mock data엔 분리값이 없음)
      const totalTokens = r.totalInputTokens + r.totalOutputTokens;
      const inputRatio =
        totalTokens > 0 ? r.totalInputTokens / totalTokens : 0.5;
      const inputCost = r.totalCostUsd * inputRatio;
      const outputCost = r.totalCostUsd - inputCost;
      return {
        runId: r.id,
        shortLabel: r.shortLabel,
        color: r.color,
        inputCost,
        outputCost,
        totalCost: r.totalCostUsd,
      };
    });
  }, [selectedRuns]);

  const tokenStats = useMemo<TokenBreakdown[]>(() => {
    return selectedRuns.map((r) => ({
      runId: r.id,
      shortLabel: r.shortLabel,
      color: r.color,
      inputTokens: r.totalInputTokens,
      outputTokens: r.totalOutputTokens,
      totalTokens: r.totalInputTokens + r.totalOutputTokens,
    }));
  }, [selectedRuns]);

  const handleCopyLink = useCallback(async () => {
    if (typeof window === "undefined") return;
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLinkCopied(true);
      window.setTimeout(() => setLinkCopied(false), 1800);
    } catch {
      setLinkCopied(false);
    }
  }, []);

  const enoughRuns = selectedRuns.length >= 2;

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 px-6 py-6">
      <PageHeader
        title="결과 비교"
        description="동일 실험 내 Run을 선택해 스코어·지연·비용·토큰을 한눈에 비교합니다."
        actions={
          <Button variant="outline" onClick={handleCopyLink} aria-live="polite">
            {linkCopied ? (
              <>
                <Check className="h-4 w-4" aria-hidden />
                복사됨!
              </>
            ) : (
              <>
                <Link2 className="h-4 w-4" aria-hidden />
                비교 링크 복사
              </>
            )}
          </Button>
        }
      />

      {/* 비교 대상 선택 */}
      <Card>
        <CardHeader className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <GitCompare className="h-4 w-4 text-indigo-300" aria-hidden />
            <CardTitle>비교 대상 선택</CardTitle>
          </div>
          <div className="flex items-center gap-2">
            <label
              htmlFor="experiment-select"
              className="text-xs text-zinc-400"
            >
              실험
            </label>
            <div className="w-[280px]">
              <Select
                id="experiment-select"
                value={experimentId}
                onChange={(e) => handleExperimentChange(e.target.value)}
              >
                {eligibleExperiments.map((exp) => (
                  <option key={exp.id} value={exp.id}>
                    {exp.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {selectedExperiment && (
            <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-zinc-400">
              <StatusDot status={selectedExperiment.status} />
              <Badge tone="muted">{selectedExperiment.datasetName}</Badge>
              <Badge tone="muted">
                {selectedExperiment.itemCount} items · {selectedExperiment.runCount}{" "}
                runs
              </Badge>
              {selectedExperiment.completedAt && (
                <span className="text-zinc-500">
                  완료 {new Date(selectedExperiment.completedAt).toLocaleString("ko-KR")}
                </span>
              )}
            </div>
          )}

          <div className="text-xs text-zinc-400">비교할 Run 선택 (최소 2개)</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {runsForExperiment.length === 0 && (
              <span className="text-sm text-zinc-500">
                해당 실험에 Run 데이터가 없습니다.
              </span>
            )}
            {runsForExperiment.map((r, idx) => {
              const checked = selectedRunIds.includes(r.id);
              const color = colorForIndex(idx);
              return (
                <label
                  key={r.id}
                  className={cn(
                    "group flex cursor-pointer items-center gap-2 rounded-md border px-3 py-1.5 text-xs transition-colors",
                    checked
                      ? "border-zinc-700 bg-zinc-800 text-zinc-100"
                      : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:bg-zinc-800/60"
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleRun(r.id)}
                    className="h-3.5 w-3.5 cursor-pointer rounded border-zinc-700 bg-zinc-800 text-indigo-400 focus:ring-1 focus:ring-indigo-400"
                  />
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: color }}
                    aria-hidden
                  />
                  <span className="font-medium text-zinc-100">{r.modelName}</span>
                  <span className="text-zinc-500">v{r.promptVersion}</span>
                </label>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {!enoughRuns && (
        <EmptyState
          icon={<ScanSearch className="h-7 w-7" aria-hidden />}
          title="비교할 Run을 2개 이상 선택해 주세요"
          description="동일 실험 내 두 개 이상의 Run을 선택해야 KPI·차트·아이템 비교가 활성화됩니다."
        />
      )}

      {enoughRuns && (
        <>
          <KpiCards runs={selectedRuns} />

          <Card>
            <CardHeader>
              <ComparisonTabs active={activeTab} onChange={setActiveTab} />
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
                <div
                  role="tabpanel"
                  id={`tabpanel-${activeTab}`}
                  aria-labelledby={`tab-${activeTab}`}
                  className="lg:col-span-3"
                >
                  <AnimatePresence mode="wait">
                    <motion.div
                      key={activeTab}
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -6 }}
                      transition={{ duration: 0.18 }}
                      className="rounded-md border border-zinc-800 bg-zinc-900/40 p-4"
                    >
                      {activeTab === "score" && (
                        <div className="flex flex-col gap-4">
                          <ScoreChart runs={selectedRuns} />
                          <div className="border-t border-zinc-800 pt-4">
                            <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-400">
                              스코어 분포
                            </h4>
                            <ScoreDistributionChart
                              runs={selectedRuns}
                              itemResults={itemResults}
                            />
                          </div>
                        </div>
                      )}
                      {activeTab === "latency" && (
                        <LatencyChart data={latencyStats} />
                      )}
                      {activeTab === "cost" && <CostChart data={costStats} />}
                      {activeTab === "tokens" && (
                        <TokenChart data={tokenStats} />
                      )}
                    </motion.div>
                  </AnimatePresence>
                </div>
                <div className="lg:col-span-2">
                  <RunStats
                    tab={activeTab}
                    scoreStats={scoreStats}
                    latencyStats={latencyStats}
                    costStats={costStats}
                    tokenStats={tokenStats}
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>아이템별 비교</CardTitle>
            </CardHeader>
            <CardContent>
              <ItemDiffTable runs={selectedRuns} itemResults={itemResults} />
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

export default function ComparePage() {
  return (
    <Suspense
      fallback={
        <div className="px-6 py-10 text-sm text-zinc-500">로딩 중…</div>
      }
    >
      <ComparePageInner />
    </Suspense>
  );
}
