"use client";

import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { useDatasetList } from "@/lib/hooks/useDatasets";
import {
  useApprovedEvaluators,
  useBuiltInEvaluators,
} from "@/lib/hooks/useEvaluators";
import { useModelList } from "@/lib/hooks/useModels";
import { usePromptList } from "@/lib/hooks/usePrompts";
import type { DatasetSummary, ModelInfo } from "@/lib/types/api";
import type { WizardState } from "./wizardState";
import { formatCurrency, formatNumber } from "@/lib/utils";

const DEFAULT_PROJECT_ID = "production-api";
const AVG_INPUT_TOKENS_PER_ITEM = 200;
const AVG_OUTPUT_TOKENS_PER_ITEM = 120;

interface WizardStep4Props {
  state: WizardState;
}

export function WizardStep4({ state }: WizardStep4Props) {
  const projectId = DEFAULT_PROJECT_ID;
  const isTraceEval = state.mode === "trace_eval";

  const { data: promptListResp } = usePromptList(projectId);
  const { data: datasetListResp } = useDatasetList(projectId);
  const { data: modelListResp } = useModelList();
  const { data: builtInResp } = useBuiltInEvaluators();
  const { data: approvedResp } = useApprovedEvaluators(projectId);

  const prompts = promptListResp?.items ?? [];
  const datasets = useMemo<DatasetSummary[]>(() => {
    if (!datasetListResp || typeof datasetListResp !== "object") return [];
    const r = datasetListResp as Record<string, unknown>;
    if (Array.isArray(r.datasets)) return r.datasets as DatasetSummary[];
    if (Array.isArray(r.items)) return r.items as DatasetSummary[];
    return [];
  }, [datasetListResp]);
  const models = useMemo<ModelInfo[]>(
    () => modelListResp?.models ?? [],
    [modelListResp]
  );

  type EvaluatorOption = { id: string; name: string; type: string };
  const evaluators = useMemo<EvaluatorOption[]>(() => {
    const builtIn: EvaluatorOption[] = (builtInResp?.evaluators ?? []).map(
      (e) => ({ id: e.name, name: e.name, type: "builtin" })
    );
    const approved: EvaluatorOption[] = (
      approvedResp?.evaluators ?? []
    ).map((e) => ({
      id: e.submission_id,
      name: e.name,
      type: "custom",
    }));
    return [...builtIn, ...approved];
  }, [builtInResp, approvedResp]);

  const prompt = prompts.find((p) => p.name === state.promptId);
  const dataset = datasets.find((d) => d.name === state.datasetId);

  const totalRuns = state.models.length * state.promptVersions.length;
  const itemCount = dataset?.item_count ?? 0;
  const totalCalls = totalRuns * itemCount;

  const estimatedCost = useMemo(() => {
    if (!dataset) return 0;
    return state.models.reduce((acc, mc) => {
      const m = models.find((mm) => mm.id === mc.modelId);
      if (!m) return acc;
      const itemsForThisModel = state.promptVersions.length * itemCount;
      const inputCost =
        (AVG_INPUT_TOKENS_PER_ITEM / 1000) *
        m.input_cost_per_k *
        itemsForThisModel;
      const outputCost =
        (AVG_OUTPUT_TOKENS_PER_ITEM / 1000) *
        m.output_cost_per_k *
        itemsForThisModel;
      return acc + inputCost + outputCost;
    }, 0);
  }, [state.models, state.promptVersions.length, itemCount, dataset, models]);

  const selectedEvaluators = state.evaluators
    .map((e) => evaluators.find((ev) => ev.id === e.evaluatorId))
    .filter((e): e is NonNullable<typeof e> => !!e);

  const selectedModels = state.models
    .map((mc) => models.find((m) => m.id === mc.modelId))
    .filter((m): m is NonNullable<typeof m> => !!m);

  if (isTraceEval) {
    const tf = state.traceFilter;
    const sampleSize = tf?.sample_size;
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>실험 요약 (Trace Eval)</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <SummaryRow label="실험명" value={state.name || "—"} />
              <SummaryRow
                label="설명"
                value={state.description || "(없음)"}
              />
              <SummaryRow label="모드" value={<Badge tone="accent">trace_eval</Badge>} />
              <SummaryRow
                label="Agent 이름"
                value={tf?.name ?? "(전체)"}
              />
              <SummaryRow
                label="Tags"
                value={
                  tf?.tags && tf.tags.length > 0 ? (
                    <span className="flex flex-wrap gap-1">
                      {tf.tags.map((t) => (
                        <Badge key={t} tone="muted">{t}</Badge>
                      ))}
                    </span>
                  ) : (
                    "(없음)"
                  )
                }
              />
              <SummaryRow
                label="기간"
                value={
                  tf?.from_timestamp
                    ? `${new Date(tf.from_timestamp).toLocaleString("ko-KR")} ~`
                    : "전체 기간"
                }
              />
              <SummaryRow
                label="샘플 수"
                value={
                  sampleSize != null ? `${formatNumber(sampleSize)}건` : "전체"
                }
              />
              <SummaryRow
                label="골든셋"
                value={state.expectedDatasetName || "(사용 안 함)"}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>평가 함수 ({selectedEvaluators.length}개)</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5">
              {selectedEvaluators.map((ev) => {
                const cfg = state.evaluators.find(
                  (e) => e.evaluatorId === ev.id
                );
                return (
                  <li
                    key={ev.id}
                    className="flex items-center justify-between gap-2 text-sm"
                  >
                    <div className="flex items-center gap-2">
                      <Badge tone="muted">{ev.type}</Badge>
                      <span className="text-zinc-200">{ev.name}</span>
                    </div>
                    <span className="font-mono text-xs text-zinc-400 tabular-nums">
                      weight {cfg?.weight.toFixed(2) ?? "—"}
                    </span>
                  </li>
                );
              })}
              {selectedEvaluators.length === 0 && (
                <li className="text-xs text-zinc-500">
                  선택된 평가 함수 없음
                </li>
              )}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>실행 예측</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Stat
                label="평가 대상"
                value={
                  sampleSize != null ? formatNumber(sampleSize) : "전체"
                }
                hint="trace_filter 매칭 + 샘플링 적용"
              />
              <Stat
                label="LLM 호출"
                value="0"
                hint="trace_eval 모드는 호출 없음"
              />
              <Stat
                label="예상 비용"
                value={formatCurrency(0, 2)}
                hint="evaluator(LLM judge) 비용은 별도"
              />
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>실험 요약</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <SummaryRow label="실험명" value={state.name || "—"} />
            <SummaryRow label="설명" value={state.description || "(없음)"} />
            <SummaryRow
              label="프롬프트"
              value={
                <span>
                  {prompt?.name ?? "—"}
                  <span className="ml-1.5 font-mono text-zinc-500">
                    {state.promptVersions.map((v) => `v${v}`).join(", ")}
                  </span>
                </span>
              }
            />
            <SummaryRow
              label="데이터셋"
              value={
                <span>
                  {dataset?.name ?? "—"}
                  <span className="ml-1.5 text-zinc-500">
                    ({formatNumber(itemCount)} items)
                  </span>
                </span>
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>모델 ({state.models.length}개)</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-wrap gap-2">
            {selectedModels.map((m) => (
              <li key={m.id}>
                <Badge tone="accent">{m.name}</Badge>
              </li>
            ))}
            {selectedModels.length === 0 && (
              <li className="text-xs text-zinc-500">선택된 모델 없음</li>
            )}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>평가 함수 ({selectedEvaluators.length}개)</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1.5">
            {selectedEvaluators.map((ev) => {
              const cfg = state.evaluators.find(
                (e) => e.evaluatorId === ev.id
              );
              return (
                <li
                  key={ev.id}
                  className="flex items-center justify-between gap-2 text-sm"
                >
                  <div className="flex items-center gap-2">
                    <Badge tone="muted">{ev.type}</Badge>
                    <span className="text-zinc-200">{ev.name}</span>
                  </div>
                  <span className="font-mono text-xs text-zinc-400 tabular-nums">
                    weight {cfg?.weight.toFixed(2) ?? "—"}
                  </span>
                </li>
              );
            })}
            {selectedEvaluators.length === 0 && (
              <li className="text-xs text-zinc-500">
                선택된 평가 함수 없음
              </li>
            )}
          </ul>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>실행 예측</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Stat
              label="총 Run 수"
              value={formatNumber(totalRuns)}
              hint={`${state.models.length} 모델 × ${state.promptVersions.length} 버전`}
            />
            <Stat
              label="총 호출 수"
              value={formatNumber(totalCalls)}
              hint={`${totalRuns} Run × ${formatNumber(itemCount)} items`}
            />
            <Stat
              label="예상 비용"
              value={formatCurrency(estimatedCost, 2)}
              hint={`평균 in ${AVG_INPUT_TOKENS_PER_ITEM} / out ${AVG_OUTPUT_TOKENS_PER_ITEM} tok 가정`}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-zinc-500">
        {label}
      </div>
      <div className="mt-0.5 text-sm text-zinc-100">{value}</div>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-950/40 px-3 py-2.5">
      <div className="text-[11px] uppercase tracking-wide text-zinc-500">
        {label}
      </div>
      <div className="mt-1 font-mono text-2xl font-semibold tabular-nums text-zinc-50">
        {value}
      </div>
      {hint && <div className="mt-1 text-[11px] text-zinc-500">{hint}</div>}
    </div>
  );
}
