"use client";

import { useMemo } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { useModelList } from "@/lib/hooks/useModels";
import type { ModelInfo as Model } from "@/lib/types/api";
import type { ModelConfig, WizardState } from "./wizardState";
import { cn, formatCurrency } from "@/lib/utils";

const PROVIDER_LABELS: Record<string, string> = {
  azure: "Azure OpenAI",
  openai: "OpenAI",
  google: "Google",
  anthropic: "Anthropic",
  bedrock: "AWS Bedrock",
};

interface WizardStep2Props {
  state: WizardState;
  onChange: (patch: Partial<WizardState>) => void;
}

export function WizardStep2({ state, onChange }: WizardStep2Props) {
  const { data: modelListResp } = useModelList();
  const models = useMemo<Model[]>(
    () => modelListResp?.models ?? [],
    [modelListResp]
  );
  const providerLabels = PROVIDER_LABELS;
  const totalRuns = state.models.length * state.promptVersions.length;

  const toggleModel = (modelId: string) => {
    const exists = state.models.find((m) => m.modelId === modelId);
    if (exists) {
      onChange({
        models: state.models.filter((m) => m.modelId !== modelId),
      });
    } else {
      const next: ModelConfig = {
        modelId,
        temperature: 0.2,
        maxTokens: 1024,
        expanded: false,
      };
      onChange({ models: [...state.models, next] });
    }
  };

  const updateModel = (modelId: string, patch: Partial<ModelConfig>) => {
    onChange({
      models: state.models.map((m) =>
        m.modelId === modelId ? { ...m, ...patch } : m
      ),
    });
  };

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4 rounded-md border border-zinc-800 bg-zinc-900/50 px-4 py-3">
        <div>
          <p className="text-sm font-medium text-zinc-200">모델 선택</p>
          <p className="mt-0.5 text-xs text-zinc-500">
            선택한 각 모델은 프롬프트 버전마다 1개의 Run으로 실행됩니다.
          </p>
        </div>
        <div className="text-right">
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">
            총 Run 수
          </div>
          <div className="font-mono text-2xl font-semibold tabular-nums text-zinc-50">
            {totalRuns}
          </div>
          <div className="text-[11px] text-zinc-500">
            {state.models.length} 모델 × {state.promptVersions.length} 버전
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-2">
        {models.map((model) => {
          const cfg = state.models.find((m) => m.modelId === model.id);
          const checked = !!cfg;

          return (
            <div
              key={model.id}
              className={cn(
                "rounded-md border bg-zinc-900 transition-colors",
                checked
                  ? "border-indigo-500/50 bg-indigo-950/20"
                  : "border-zinc-800"
              )}
            >
              <label className="flex cursor-pointer items-center gap-3 px-3 py-2.5">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleModel(model.id)}
                  className="h-4 w-4 rounded border-zinc-600 bg-zinc-800 accent-indigo-500"
                />
                <div className="flex min-w-0 flex-1 items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-zinc-100">
                        {model.name}
                      </span>
                      <Badge tone="muted">
                        {providerLabels[model.provider] ?? model.provider}
                      </Badge>
                      {model.vision && <Badge tone="info">vision</Badge>}
                    </div>
                    <div className="mt-0.5 text-[11px] text-zinc-500">
                      ctx {Math.round(model.context_window / 1000)}K · in{" "}
                      {formatCurrency(model.input_cost_per_k, 4)}/1K · out{" "}
                      {formatCurrency(model.output_cost_per_k, 4)}/1K
                    </div>
                  </div>
                  {checked && cfg && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.preventDefault();
                        updateModel(model.id, { expanded: !cfg.expanded });
                      }}
                      className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
                      aria-expanded={cfg.expanded}
                      aria-label="모델 파라미터 펼치기"
                    >
                      {cfg.expanded ? (
                        <ChevronDown className="h-4 w-4" aria-hidden />
                      ) : (
                        <ChevronRight className="h-4 w-4" aria-hidden />
                      )}
                      <span>설정</span>
                    </button>
                  )}
                </div>
              </label>

              {checked && cfg?.expanded && (
                <div className="grid grid-cols-1 gap-3 border-t border-zinc-800 bg-zinc-950/40 px-3 py-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <label
                      htmlFor={`temp-${model.id}`}
                      className="block text-xs text-zinc-400"
                    >
                      temperature
                    </label>
                    <Input
                      id={`temp-${model.id}`}
                      type="number"
                      min={0}
                      max={2}
                      step={0.1}
                      value={cfg.temperature}
                      onChange={(e) =>
                        updateModel(model.id, {
                          temperature: Number(e.target.value),
                        })
                      }
                    />
                  </div>
                  <div className="space-y-1">
                    <label
                      htmlFor={`maxtok-${model.id}`}
                      className="block text-xs text-zinc-400"
                    >
                      max_tokens
                    </label>
                    <Input
                      id={`maxtok-${model.id}`}
                      type="number"
                      min={1}
                      max={model.context_window}
                      step={64}
                      value={cfg.maxTokens}
                      onChange={(e) =>
                        updateModel(model.id, {
                          maxTokens: Number(e.target.value),
                        })
                      }
                    />
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
