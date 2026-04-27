"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  Check,
  Database,
  GitCompare,
  Link2,
  Loader2,
  ScanSearch,
} from "lucide-react";
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
  useCompareItems,
  useCompareRuns,
  useCostDistribution,
  useLatencyDistribution,
  useScoreDistribution,
} from "@/lib/hooks/useAnalysis";
import { useDeriveDataset } from "@/lib/hooks/useDatasets";
import {
  useExperimentDetail,
  useExperimentList,
} from "@/lib/hooks/useExperiments";
import type {
  CompareItemEntry,
  CompareItemsResponse,
  RunSummary,
} from "@/lib/types/api";
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
  ItemResult,
  LatencyPercentiles,
  RunStatsSummary,
  SelectedRun,
  TokenBreakdown,
} from "./_components/types";

const DEFAULT_PROJECT_ID = "production-api";

function buildShortLabel(r: { modelName: string; promptVersion: number }) {
  return `${r.modelName} · v${r.promptVersion}`;
}

function runSummaryToView(
  r: RunSummary,
  idx: number
): {
  id: string;
  modelName: string;
  promptVersion: number;
  status: string;
  itemsCompleted: number;
  itemsTotal: number;
  avgScore: number | null;
  avgLatencyMs: number | null;
  totalCostUsd: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  color: string;
  shortLabel: string;
} {
  const avgScore = r.avg_score ?? r.summary?.avg_score ?? null;
  const avgLatency =
    r.avg_latency_ms ??
    r.summary?.avg_latency_ms ??
    r.summary?.avg_latency ??
    null;
  const totalCost = r.total_cost ?? r.summary?.total_cost ?? 0;
  return {
    id: r.run_name,
    modelName: r.model,
    promptVersion: r.prompt_version,
    status: r.status,
    itemsCompleted: r.items_completed ?? 0,
    itemsTotal: r.items_total ?? 0,
    avgScore,
    avgLatencyMs: avgLatency,
    totalCostUsd: totalCost,
    totalInputTokens: 0,
    totalOutputTokens: 0,
    color: colorForIndex(idx),
    shortLabel: buildShortLabel({
      modelName: r.model,
      promptVersion: r.prompt_version,
    }),
  };
}

function compareItemsToItemResults(
  resp: CompareItemsResponse | undefined,
  runIds: string[]
): ItemResult[] {
  if (!resp) return [];
  return resp.items.map((it: CompareItemEntry, i: number) => {
    const outputs: Record<string, string> = {};
    const scoresByRun: Record<string, number | null> = {};
    const latenciesByRun: Record<string, number> = {};
    const costsByRun: Record<string, number> = {};
    runIds.forEach((id) => {
      const r = it.results?.[id];
      outputs[id] = r?.output ?? "";
      scoresByRun[id] = r?.score ?? null;
      latenciesByRun[id] = r?.latency_ms ?? 0;
      costsByRun[id] = r?.cost_usd ?? 0;
    });
    const expectedRaw = it.expected_output;
    const expected =
      typeof expectedRaw === "string"
        ? expectedRaw
        : JSON.stringify(expectedRaw ?? "");
    const inputStr =
      typeof it.input === "string"
        ? it.input
        : JSON.stringify(it.input ?? "");
    return {
      itemId: it.dataset_item_id,
      itemIndex: i + 1,
      input: inputStr,
      expected,
      outputs,
      scoresByRun,
      latenciesByRun,
      costsByRun,
    };
  });
}

function ComparePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const projectId = DEFAULT_PROJECT_ID;

  // Eligible experiments: completed + running
  const { data: completedList } = useExperimentList(projectId, {
    status: "completed",
    page: 1,
    pageSize: 50,
  });
  const { data: runningList } = useExperimentList(projectId, {
    status: "running",
    page: 1,
    pageSize: 50,
  });

  const eligibleExperiments = useMemo(() => {
    const a = completedList?.items ?? [];
    const b = runningList?.items ?? [];
    return [...a, ...b];
  }, [completedList, runningList]);

  const initialExperimentId = useMemo(() => {
    const fromParam = searchParams.get("experiment");
    if (
      fromParam &&
      eligibleExperiments.some((e) => e.experiment_id === fromParam)
    ) {
      return fromParam;
    }
    return eligibleExperiments[0]?.experiment_id ?? "";
  }, [searchParams, eligibleExperiments]);

  const [experimentId, setExperimentId] = useState<string>(initialExperimentId);

  useEffect(() => {
    if (!experimentId && initialExperimentId) {
      setExperimentId(initialExperimentId);
    }
  }, [initialExperimentId, experimentId]);

  const { data: experimentDetail } = useExperimentDetail(experimentId);
  const runsForExperiment = useMemo<RunSummary[]>(
    () => experimentDetail?.runs ?? [],
    [experimentDetail]
  );

  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);

  useEffect(() => {
    setSelectedRunIds(runsForExperiment.map((r) => r.run_name));
  }, [runsForExperiment]);

  const [activeTab, setActiveTab] = useState<CompareTab>("score");
  const [linkCopied, setLinkCopied] = useState(false);

  const handleExperimentChange = useCallback(
    (next: string) => {
      setExperimentId(next);
      const params = new URLSearchParams(searchParams.toString());
      params.set("experiment", next);
      router.replace(`/compare?${params.toString()}`, { scroll: false });
    },
    [router, searchParams]
  );

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
    () =>
      eligibleExperiments.find((e) => e.experiment_id === experimentId) ?? null,
    [eligibleExperiments, experimentId]
  );

  const selectedRuns = useMemo<SelectedRun[]>(() => {
    return runsForExperiment
      .map((r, originalIdx) => ({ r, originalIdx }))
      .filter(({ r }) => selectedRunIds.includes(r.run_name))
      .map(({ r, originalIdx }) => runSummaryToView(r, originalIdx));
  }, [runsForExperiment, selectedRunIds]);

  const enoughRuns = selectedRuns.length >= 2;
  const selectedRunNames = useMemo(
    () => selectedRuns.map((r) => r.id),
    [selectedRuns]
  );

  // Compare KPIs (POST /analysis/compare)
  const { data: compareKpis } = useCompareRuns(
    enoughRuns
      ? {
          project_id: projectId,
          run_names: selectedRunNames,
        }
      : null
  );

  // Compare items (mutation — fetched on demand)
  const compareItemsMutation = useCompareItems();
  const [scoreMin, setScoreMin] = useState<number | null>(null);
  const [compareItemsResp, setCompareItemsResp] = useState<
    CompareItemsResponse | undefined
  >();

  useEffect(() => {
    if (!enoughRuns) {
      setCompareItemsResp(undefined);
      return;
    }
    compareItemsMutation
      .mutateAsync({
        project_id: projectId,
        run_names: selectedRunNames,
        filter: scoreMin !== null ? { score_min: scoreMin } : undefined,
      })
      .then(setCompareItemsResp)
      .catch(() => setCompareItemsResp(undefined));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enoughRuns, selectedRunNames.join(","), scoreMin]);

  const itemResults = useMemo<ItemResult[]>(
    () => compareItemsToItemResults(compareItemsResp, selectedRunNames),
    [compareItemsResp, selectedRunNames]
  );

  // Distribution endpoints (positional args)
  const { data: scoreDistResp } = useScoreDistribution(
    projectId,
    enoughRuns && activeTab === "score" ? selectedRunNames : [],
    "overall",
    10
  );
  const { data: latencyDistResp } = useLatencyDistribution(
    projectId,
    enoughRuns && activeTab === "latency" ? selectedRunNames : [],
    10
  );
  const { data: costDistResp } = useCostDistribution(
    projectId,
    enoughRuns && activeTab === "cost" ? selectedRunNames : [],
    10
  );

  // Stats from comparison response (CompareEntry[])
  const scoreStats = useMemo<RunStatsSummary[]>(() => {
    if (compareKpis?.comparison) {
      return compareKpis.comparison.map((entry) => {
        const run = selectedRuns.find((r) => r.id === entry.run_name);
        const firstScoreKey = Object.keys(entry.scores ?? {})[0];
        const firstScore = firstScoreKey
          ? entry.scores[firstScoreKey]
          : undefined;
        const summary =
          firstScore && typeof firstScore === "object"
            ? firstScore
            : null;
        return {
          runId: entry.run_name,
          shortLabel: run?.shortLabel ?? entry.run_name,
          modelName: run?.modelName ?? entry.model,
          promptVersion: run?.promptVersion ?? entry.prompt_version,
          color: run?.color ?? "#888",
          avgScore: summary?.avg ?? (typeof firstScore === "number" ? firstScore : 0),
          stdDev: summary?.stddev ?? 0,
          min: summary?.min ?? 0,
          max: summary?.max ?? 0,
          validCount: entry.metrics.sample_count ?? 0,
          totalCount: entry.metrics.sample_count ?? 0,
        };
      });
    }
    return selectedRuns.map((r) => ({
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
      totalCount: 0,
    }));
  }, [compareKpis, selectedRuns]);

  const latencyStats = useMemo<LatencyPercentiles[]>(() => {
    if (compareKpis?.comparison) {
      return compareKpis.comparison.map((entry) => {
        const run = selectedRuns.find((r) => r.id === entry.run_name);
        return {
          runId: entry.run_name,
          shortLabel: run?.shortLabel ?? entry.run_name,
          color: run?.color ?? "#888",
          p50: entry.metrics.p50_latency_ms ?? 0,
          p90: entry.metrics.p90_latency_ms ?? 0,
          p99: entry.metrics.p99_latency_ms ?? 0,
          avg: entry.metrics.avg_latency_ms ?? 0,
        };
      });
    }
    return selectedRuns.map((r) => ({
      runId: r.id,
      shortLabel: r.shortLabel,
      color: r.color,
      p50: r.avgLatencyMs ?? 0,
      p90: r.avgLatencyMs ?? 0,
      p99: r.avgLatencyMs ?? 0,
      avg: r.avgLatencyMs ?? 0,
    }));
  }, [compareKpis, selectedRuns]);

  const costStats = useMemo<CostBreakdown[]>(() => {
    if (compareKpis?.comparison) {
      return compareKpis.comparison.map((entry) => {
        const run = selectedRuns.find((r) => r.id === entry.run_name);
        const total = entry.metrics.total_cost_usd ?? 0;
        const modelCost = entry.metrics.model_cost_usd;
        const evalCost = entry.metrics.eval_cost_usd;
        const inputTokens = entry.metrics.avg_input_tokens ?? 0;
        const outputTokens = entry.metrics.avg_output_tokens ?? 0;
        const totalTokens = inputTokens + outputTokens;
        const inputRatio = totalTokens > 0 ? inputTokens / totalTokens : 0.5;
        return {
          runId: entry.run_name,
          shortLabel: run?.shortLabel ?? entry.run_name,
          color: run?.color ?? "#888",
          inputCost: total * inputRatio,
          outputCost: total * (1 - inputRatio),
          totalCost: total,
          modelCost,
          evalCost,
        };
      });
    }
    return selectedRuns.map((r) => {
      const total = r.totalCostUsd;
      return {
        runId: r.id,
        shortLabel: r.shortLabel,
        color: r.color,
        inputCost: total * 0.5,
        outputCost: total * 0.5,
        totalCost: total,
      };
    });
  }, [compareKpis, selectedRuns]);

  const tokenStats = useMemo<TokenBreakdown[]>(() => {
    if (compareKpis?.comparison) {
      return compareKpis.comparison.map((entry) => {
        const run = selectedRuns.find((r) => r.id === entry.run_name);
        const inputTokens = entry.metrics.avg_input_tokens ?? 0;
        const outputTokens = entry.metrics.avg_output_tokens ?? 0;
        return {
          runId: entry.run_name,
          shortLabel: run?.shortLabel ?? entry.run_name,
          color: run?.color ?? "#888",
          inputTokens,
          outputTokens,
          totalTokens: inputTokens + outputTokens,
        };
      });
    }
    return selectedRuns.map((r) => ({
      runId: r.id,
      shortLabel: r.shortLabel,
      color: r.color,
      inputTokens: r.totalInputTokens,
      outputTokens: r.totalOutputTokens,
      totalTokens: r.totalInputTokens + r.totalOutputTokens,
    }));
  }, [compareKpis, selectedRuns]);

  // Distribution adapters → chart-shaped objects
  const scoreDistribution = useMemo(() => {
    if (!scoreDistResp) return undefined;
    return {
      bins: scoreDistResp.distribution.map((b) => ({
        lo: b.bin_start,
        hi: b.bin_end,
        // counts are not split per-run in ScoreDistributionResponse — collapse to overall
        counts: { overall: b.count },
      })),
    };
  }, [scoreDistResp]);

  const latencyDistribution = useMemo(() => {
    if (!latencyDistResp) return undefined;
    const firstRun = Object.keys(latencyDistResp.runs)[0];
    if (!firstRun) return undefined;
    const dist = latencyDistResp.runs[firstRun].distribution;
    return {
      bins: dist.map((b) => ({ lo: b.bin_start, hi: b.bin_end, count: b.count })),
    };
  }, [latencyDistResp]);

  const costDistribution = useMemo(() => {
    if (!costDistResp) return undefined;
    return {
      items: Object.entries(costDistResp.runs).map(([runName, data]) => ({
        run_name: runName,
        // Statistics may carry mean/stddev; we leave model/eval cost undefined
        // since CompareEntry already provides per-run model/eval split.
        model_cost: undefined,
        eval_cost: undefined,
        ...data.statistics,
      })),
    };
  }, [costDistResp]);

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

  const handleAddToBasket = useCallback(() => {
    if (typeof window === "undefined") return;
    const key = "compare-basket";
    try {
      const raw = window.localStorage.getItem(key);
      const list = raw ? (JSON.parse(raw) as string[]) : [];
      if (!list.includes(experimentId)) list.push(experimentId);
      window.localStorage.setItem(key, JSON.stringify(list));
    } catch {
      /* noop */
    }
  }, [experimentId]);

  const datasetFromItems = useDeriveDataset();
  const handleDeriveFailedDataset = useCallback(async () => {
    const failedItemIds = itemResults
      .filter((it) =>
        Object.values(it.scoresByRun).some(
          (v) => v !== null && v !== undefined && v < 0.5
        )
      )
      .map((it) => it.itemId);
    if (failedItemIds.length === 0) {
      alert("실패 아이템이 없습니다.");
      return;
    }
    try {
      await datasetFromItems.mutateAsync({
        project_id: projectId,
        source_run_names: selectedRunNames,
        item_ids: failedItemIds,
        new_dataset_name: `${selectedExperiment?.name ?? "experiment"}-failures`,
      });
      router.push(`/datasets`);
    } catch (err) {
      alert(err instanceof Error ? err.message : "데이터셋 생성 실패");
    }
  }, [
    itemResults,
    datasetFromItems,
    projectId,
    selectedRunNames,
    selectedExperiment,
    router,
  ]);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6 px-6 py-6">
      <PageHeader
        title="결과 비교"
        description="동일 실험 내 Run을 선택해 스코어·지연·비용·토큰을 한눈에 비교합니다."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={handleAddToBasket}>
              비교 장바구니 담기
            </Button>
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
          </div>
        }
      />

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
                  <option key={exp.experiment_id} value={exp.experiment_id}>
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
              <Badge tone="muted">
                {selectedExperiment.runs_completed ?? 0} /{" "}
                {selectedExperiment.total_runs ?? selectedExperiment.runs_total ?? 0}{" "}
                runs
              </Badge>
              <span className="text-zinc-500">
                생성{" "}
                {new Date(selectedExperiment.created_at).toLocaleString("ko-KR")}
              </span>
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
              const checked = selectedRunIds.includes(r.run_name);
              const color = colorForIndex(idx);
              return (
                <label
                  key={r.run_name}
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
                    onChange={() => toggleRun(r.run_name)}
                    className="h-3.5 w-3.5 cursor-pointer rounded border-zinc-700 bg-zinc-800 text-indigo-400 focus:ring-1 focus:ring-indigo-400"
                  />
                  <span
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: color }}
                    aria-hidden
                  />
                  <span className="font-medium text-zinc-100">{r.model}</span>
                  <span className="text-zinc-500">v{r.prompt_version}</span>
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
                              distribution={scoreDistribution}
                            />
                          </div>
                        </div>
                      )}
                      {activeTab === "latency" && (
                        <LatencyChart
                          data={latencyStats}
                          distribution={latencyDistribution}
                        />
                      )}
                      {activeTab === "cost" && (
                        <CostChart
                          data={costStats}
                          distribution={costDistribution}
                        />
                      )}
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
            <CardHeader className="flex flex-wrap items-center justify-between gap-2">
              <CardTitle>아이템별 비교</CardTitle>
              <div className="flex items-center gap-2">
                <label className="text-xs text-zinc-400">
                  최소 스코어
                  <input
                    type="number"
                    min={0}
                    max={1}
                    step={0.05}
                    value={scoreMin ?? ""}
                    onChange={(e) =>
                      setScoreMin(
                        e.target.value === "" ? null : Number(e.target.value)
                      )
                    }
                    className="ml-2 w-20 rounded border border-zinc-700 bg-zinc-900 px-2 py-0.5 text-xs"
                  />
                </label>
                <Button
                  variant="outline"
                  onClick={handleDeriveFailedDataset}
                  disabled={datasetFromItems.isPending}
                  aria-label="실패 아이템으로 새 데이터셋 만들기"
                >
                  {datasetFromItems.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <Database className="h-4 w-4" aria-hidden />
                  )}
                  실패 → 새 데이터셋
                </Button>
              </div>
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
