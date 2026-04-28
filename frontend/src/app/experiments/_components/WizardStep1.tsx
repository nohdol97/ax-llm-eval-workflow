"use client";

import { useMemo } from "react";
import { Input, Textarea } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { useDatasetList } from "@/lib/hooks/useDatasets";
import { usePromptList, usePromptVersions } from "@/lib/hooks/usePrompts";
import type { DatasetSummary, TraceFilter } from "@/lib/types/api";
import { defaultTraceFilter, type WizardMode, type WizardState } from "./wizardState";
import { TraceFilterForm } from "./TraceFilterForm";
import { cn } from "@/lib/utils";

const DEFAULT_PROJECT_ID = "production-api";

interface WizardStep1Props {
  state: WizardState;
  onChange: (patch: Partial<WizardState>) => void;
}

export function WizardStep1({ state, onChange }: WizardStep1Props) {
  const projectId = DEFAULT_PROJECT_ID;

  const handleModeChange = (mode: WizardMode) => {
    if (mode === state.mode) return;
    if (mode === "trace_eval" && state.traceFilter === null) {
      onChange({ mode, traceFilter: defaultTraceFilter(projectId) });
    } else {
      onChange({ mode });
    }
  };

  const handleTraceFilterChange = (filter: TraceFilter) => {
    onChange({ traceFilter: filter });
  };

  const { data: promptListResp } = usePromptList(projectId);
  const { data: datasetListResp } = useDatasetList(projectId);

  const prompts = useMemo(
    () => promptListResp?.items ?? [],
    [promptListResp]
  );
  const datasets = useMemo<DatasetSummary[]>(() => {
    if (!datasetListResp) return [];
    if ("datasets" in datasetListResp) return datasetListResp.datasets;
    if ("items" in datasetListResp) return datasetListResp.items;
    return [];
  }, [datasetListResp]);

  const selectedPrompt = useMemo(
    () => prompts.find((p) => p.name === state.promptId),
    [prompts, state.promptId]
  );
  const selectedDataset = useMemo(
    () => datasets.find((d) => d.name === state.datasetId),
    [datasets, state.datasetId]
  );

  const { data: versionsResp } = usePromptVersions(
    projectId,
    selectedPrompt?.name ?? null
  );
  const versions = versionsResp?.versions ?? [];

  const handlePromptChange = (promptId: string) => {
    onChange({ promptId, promptVersions: [] });
  };

  const toggleVersion = (version: number) => {
    const next = state.promptVersions.includes(version)
      ? state.promptVersions.filter((v) => v !== version)
      : [...state.promptVersions, version].sort((a, b) => a - b);
    onChange({ promptVersions: next });
  };

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
        <h3 className="mb-3 text-sm font-medium text-zinc-300">실험 모드</h3>
        <div
          role="radiogroup"
          aria-label="실험 모드 선택"
          className="grid grid-cols-1 gap-3 sm:grid-cols-2"
        >
          <button
            type="button"
            role="radio"
            aria-checked={state.mode === "live"}
            onClick={() => handleModeChange("live")}
            className={cn(
              "rounded-md border p-3 text-left text-sm transition-colors",
              state.mode === "live"
                ? "border-indigo-500 bg-indigo-500/10 text-zinc-50"
                : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700"
            )}
          >
            <div className="font-medium">신규 실행 (Live)</div>
            <div className="mt-1 text-xs text-zinc-400">
              데이터셋 아이템마다 LLM 호출 + 평가
            </div>
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={state.mode === "trace_eval"}
            onClick={() => handleModeChange("trace_eval")}
            className={cn(
              "rounded-md border p-3 text-left text-sm transition-colors",
              state.mode === "trace_eval"
                ? "border-indigo-500 bg-indigo-500/10 text-zinc-50"
                : "border-zinc-800 bg-zinc-900 text-zinc-300 hover:border-zinc-700"
            )}
          >
            <div className="font-medium">기존 Trace 평가 (Trace Eval)</div>
            <div className="mt-1 text-xs text-zinc-400">
              Langfuse trace를 가져와 평가 (LLM 호출 없음)
            </div>
          </button>
        </div>
      </div>

      <div className="space-y-1.5">
        <label
          htmlFor="experiment-name"
          className="block text-sm font-medium text-zinc-200"
        >
          실험명
          <span className="ml-1 text-rose-400">*</span>
        </label>
        <Input
          id="experiment-name"
          placeholder="예: 감성분석 v3 vs v4 회귀 검증"
          value={state.name}
          onChange={(e) => onChange({ name: e.target.value })}
          maxLength={120}
        />
        <p className="text-[11px] text-zinc-500">
          공백 포함 최대 120자. 결과 비교 페이지에서 식별자로 사용됩니다.
        </p>
      </div>

      <div className="space-y-1.5">
        <label
          htmlFor="experiment-description"
          className="block text-sm font-medium text-zinc-200"
        >
          설명 (선택)
        </label>
        <Textarea
          id="experiment-description"
          placeholder="이 실험의 목적, 가설, 비교하려는 변화 등을 입력하세요."
          value={state.description}
          onChange={(e) => onChange({ description: e.target.value })}
          rows={3}
        />
      </div>

      {state.mode === "trace_eval" && (
        <>
          <TraceFilterForm
            value={state.traceFilter}
            onChange={handleTraceFilterChange}
            projectId={projectId}
          />
          <div className="space-y-1.5">
            <label
              htmlFor="expected-dataset"
              className="block text-sm font-medium text-zinc-200"
            >
              골든셋 데이터셋 (선택)
            </label>
            <Select
              id="expected-dataset"
              value={state.expectedDatasetName}
              onChange={(e) =>
                onChange({ expectedDatasetName: e.target.value })
              }
            >
              <option value="">사용 안 함</option>
              {datasets.map((d) => (
                <option key={d.name} value={d.name}>
                  {d.name}
                </option>
              ))}
            </Select>
            <p className="text-[11px] text-zinc-500">
              지정 시 trace.input과 데이터셋 아이템을 매칭하여 expected_output을
              evaluator에 전달합니다.
            </p>
          </div>
        </>
      )}

      {state.mode === "live" && (
        <>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <label
            htmlFor="prompt-select"
            className="block text-sm font-medium text-zinc-200"
          >
            프롬프트
            <span className="ml-1 text-rose-400">*</span>
          </label>
          <Select
            id="prompt-select"
            value={state.promptId}
            onChange={(e) => handlePromptChange(e.target.value)}
          >
            <option value="">프롬프트를 선택하세요</option>
            {prompts.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name} (v{p.latest_version})
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor="dataset-select"
            className="block text-sm font-medium text-zinc-200"
          >
            데이터셋
            <span className="ml-1 text-rose-400">*</span>
          </label>
          <Select
            id="dataset-select"
            value={state.datasetId}
            onChange={(e) => onChange({ datasetId: e.target.value })}
          >
            <option value="">데이터셋을 선택하세요</option>
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>
                {d.name} ({d.item_count} items)
              </option>
            ))}
          </Select>
          {selectedDataset && (
            <p className="text-[11px] text-zinc-500">
              {selectedDataset.item_count} items ·{" "}
              {new Date(selectedDataset.created_at).toLocaleDateString(
                "ko-KR"
              )}{" "}
              생성
            </p>
          )}
        </div>
      </div>

      {selectedPrompt && (
        <div className="space-y-2">
          <span className="block text-sm font-medium text-zinc-200">
            프롬프트 버전 (다중 선택)
            <span className="ml-1 text-rose-400">*</span>
          </span>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {versions.map((v) => {
              const checked = state.promptVersions.includes(v.version);
              return (
                <label
                  key={v.version}
                  className={cn(
                    "flex cursor-pointer items-start gap-3 rounded-md border bg-zinc-900 px-3 py-2.5 transition-colors",
                    checked
                      ? "border-indigo-500 bg-indigo-950/30"
                      : "border-zinc-800 hover:border-zinc-700"
                  )}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleVersion(v.version)}
                    className="mt-1 h-4 w-4 rounded border-zinc-600 bg-zinc-800 accent-indigo-500"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-sm text-zinc-100">
                        v{v.version}
                      </span>
                      {v.version === selectedPrompt.latest_version && (
                        <Badge tone="accent">latest</Badge>
                      )}
                    </div>
                    <div className="mt-1 text-[10px] text-zinc-600">
                      {v.created_by ?? "—"} ·{" "}
                      {new Date(v.created_at).toLocaleDateString("ko-KR")}
                    </div>
                  </div>
                </label>
              );
            })}
            {versions.length === 0 && (
              <p className="text-[11px] text-zinc-500">
                버전 정보를 불러오는 중…
              </p>
            )}
          </div>
        </div>
      )}
        </>
      )}
    </div>
  );
}
