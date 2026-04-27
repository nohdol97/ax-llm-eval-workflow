"use client";

import { useMemo, useState } from "react";
import { Input, Textarea } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import {
  useApprovedEvaluators,
  useBuiltInEvaluators,
} from "@/lib/hooks/useEvaluators";
import { useModelList } from "@/lib/hooks/useModels";
import type { ModelInfo } from "@/lib/types/api";
import type { EvaluatorConfig, WizardState } from "./wizardState";
import { cn } from "@/lib/utils";

const DEFAULT_PROJECT_ID = "production-api";

type EvalCategory = "builtin" | "judge" | "custom";

interface UnifiedEvaluator {
  id: string;
  name: string;
  description: string;
  range: string;
  category: EvalCategory;
  status?: string;
}

interface WizardStep3Props {
  state: WizardState;
  onChange: (patch: Partial<WizardState>) => void;
}

const TABS: Array<{ id: EvalCategory; label: string }> = [
  { id: "builtin", label: "내장" },
  { id: "judge", label: "Judge (LLM)" },
  { id: "custom", label: "Custom" },
];

export function WizardStep3({ state, onChange }: WizardStep3Props) {
  const projectId = DEFAULT_PROJECT_ID;
  const [tab, setTab] = useState<EvalCategory>("builtin");

  const { data: builtInResp } = useBuiltInEvaluators();
  const { data: approvedResp } = useApprovedEvaluators(projectId);

  const evaluators = useMemo<UnifiedEvaluator[]>(() => {
    const builtIn: UnifiedEvaluator[] = (builtInResp?.evaluators ?? []).map(
      (e) => ({
        id: e.name,
        name: e.name,
        description: e.description,
        range:
          e.return_type === "binary"
            ? "binary"
            : e.return_type === "integer"
              ? "0-N"
              : "0-1",
        category: e.name.includes("judge") ? "judge" : "builtin",
        status: "approved",
      })
    );
    const approved: UnifiedEvaluator[] = (
      approvedResp?.evaluators ?? []
    ).map((e) => ({
      id: e.submission_id,
      name: e.name,
      description: e.description,
      range: "0-1",
      category: "custom",
      status: "approved",
    }));
    return [...builtIn, ...approved];
  }, [builtInResp, approvedResp]);

  const { data: modelListResp } = useModelList();
  const models = useMemo<ModelInfo[]>(
    () => modelListResp?.models ?? [],
    [modelListResp]
  );

  const filtered = useMemo(
    () => evaluators.filter((e) => e.category === tab),
    [tab, evaluators]
  );

  const hasJudge = state.evaluators.some((e) => {
    const def = evaluators.find((ev) => ev.id === e.evaluatorId);
    return def?.category === "judge";
  });

  const totalWeight = state.evaluators.reduce((sum, e) => sum + e.weight, 0);

  const toggleEvaluator = (evaluatorId: string) => {
    const exists = state.evaluators.find((e) => e.evaluatorId === evaluatorId);
    let next: EvaluatorConfig[];
    if (exists) {
      next = state.evaluators.filter((e) => e.evaluatorId !== evaluatorId);
    } else {
      const equalWeight = 1 / (state.evaluators.length + 1);
      next = [
        ...state.evaluators.map((e) =>
          state.normalizeWeights ? { ...e, weight: equalWeight } : e
        ),
        { evaluatorId, weight: equalWeight },
      ];
    }
    onChange({ evaluators: next });
  };

  const updateWeight = (evaluatorId: string, weight: number) => {
    const next = state.evaluators.map((e) =>
      e.evaluatorId === evaluatorId ? { ...e, weight } : e
    );
    onChange({ evaluators: next });
  };

  const normalize = () => {
    if (state.evaluators.length === 0) return;
    const sum = state.evaluators.reduce((s, e) => s + e.weight, 0) || 1;
    const next = state.evaluators.map((e) => ({
      ...e,
      weight: Number((e.weight / sum).toFixed(3)),
    }));
    onChange({ evaluators: next });
  };

  return (
    <div className="space-y-5">
      <div
        role="tablist"
        aria-label="평가 함수 카테고리"
        className="inline-flex items-center gap-1 rounded-md border border-zinc-800 bg-zinc-900 p-1"
      >
        {TABS.map((t) => {
          const count = evaluators.filter((e) => e.category === t.id).length;
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              role="tab"
              type="button"
              aria-selected={active}
              onClick={() => setTab(t.id)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-xs font-medium transition-colors",
                active
                  ? "bg-indigo-500/15 text-indigo-200"
                  : "text-zinc-400 hover:text-zinc-200"
              )}
            >
              {t.label}
              <span className="font-mono text-[10px] text-zinc-500">
                {count}
              </span>
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-2">
        {filtered.map((ev) => {
          const cfg = state.evaluators.find((e) => e.evaluatorId === ev.id);
          const checked = !!cfg;
          return (
            <div
              key={ev.id}
              className={cn(
                "rounded-md border bg-zinc-900 transition-colors",
                checked
                  ? "border-indigo-500/50 bg-indigo-950/20"
                  : "border-zinc-800"
              )}
            >
              <label className="flex cursor-pointer items-start gap-3 px-3 py-2.5">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleEvaluator(ev.id)}
                  className="mt-1 h-4 w-4 rounded border-zinc-600 bg-zinc-800 accent-indigo-500"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-zinc-100">{ev.name}</span>
                    <Badge tone="muted">{ev.range}</Badge>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-zinc-500">
                    {ev.description}
                  </p>
                </div>
                {checked && cfg && (
                  <div className="ml-2 flex shrink-0 items-center gap-2">
                    <span className="text-[11px] text-zinc-500">가중치</span>
                    <Input
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={cfg.weight.toFixed(3)}
                      onClick={(e) => e.preventDefault()}
                      onChange={(e) =>
                        updateWeight(ev.id, Number(e.target.value))
                      }
                      className="h-7 w-20 text-right"
                    />
                  </div>
                )}
              </label>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <p className="rounded-md border border-dashed border-zinc-800 bg-zinc-950/50 px-4 py-6 text-center text-xs text-zinc-500">
            이 카테고리에 사용 가능한 평가 함수가 없습니다.
          </p>
        )}
      </div>

      <div className="flex flex-col gap-3 rounded-md border border-zinc-800 bg-zinc-900/50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 text-xs text-zinc-400">
          <label className="inline-flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={state.normalizeWeights}
              onChange={(e) =>
                onChange({ normalizeWeights: e.target.checked })
              }
              className="h-4 w-4 rounded border-zinc-600 bg-zinc-800 accent-indigo-500"
            />
            <span>가중치 자동 정규화 (합계 = 1.0)</span>
          </label>
          <button
            type="button"
            onClick={normalize}
            className="rounded-md border border-zinc-700 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800"
            disabled={state.evaluators.length === 0}
          >
            지금 정규화
          </button>
        </div>
        <div className="text-xs text-zinc-400">
          현재 합계{" "}
          <span
            className={cn(
              "font-mono tabular-nums",
              Math.abs(totalWeight - 1) < 0.01
                ? "text-emerald-300"
                : "text-amber-300"
            )}
          >
            {totalWeight.toFixed(3)}
          </span>
        </div>
      </div>

      {hasJudge && (
        <div className="space-y-3 rounded-md border border-indigo-900/40 bg-indigo-950/10 px-4 py-3">
          <div>
            <h4 className="text-sm font-semibold text-zinc-100">
              LLM-as-Judge 설정
            </h4>
            <p className="mt-0.5 text-[11px] text-zinc-500">
              Judge 평가 함수에 사용할 모델과 평가 프롬프트를 입력하세요.
            </p>
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="judge-model"
              className="block text-xs font-medium text-zinc-300"
            >
              Judge 모델
            </label>
            <Select
              id="judge-model"
              value={state.judge.judgeModelId}
              onChange={(e) =>
                onChange({
                  judge: { ...state.judge, judgeModelId: e.target.value },
                })
              }
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="judge-prompt"
              className="block text-xs font-medium text-zinc-300"
            >
              평가 프롬프트
            </label>
            <Textarea
              id="judge-prompt"
              rows={4}
              value={state.judge.judgePrompt}
              onChange={(e) =>
                onChange({
                  judge: { ...state.judge, judgePrompt: e.target.value },
                })
              }
            />
          </div>
        </div>
      )}
    </div>
  );
}
