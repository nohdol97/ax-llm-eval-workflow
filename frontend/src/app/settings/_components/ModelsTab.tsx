"use client";

import { Check, Info, X } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { useModelList } from "@/lib/hooks/useModels";
import { formatNumber } from "@/lib/utils";

interface ModelView {
  id: string;
  name: string;
  provider: string;
  vision: boolean;
  contextWindow: number;
  inputCostPerK: number;
  outputCostPerK: number;
}

const PROVIDER_LABELS: Record<string, string> = {
  azure: "Azure OpenAI",
  openai: "OpenAI",
  google: "Google",
  anthropic: "Anthropic",
  bedrock: "AWS Bedrock",
};

function normalizeModel(raw: Record<string, unknown>): ModelView {
  const obj = raw as {
    id?: string;
    model_name?: string;
    name?: string;
    provider?: string;
    vision?: boolean;
    supports_vision?: boolean;
    context_window?: number;
    contextWindow?: number;
    input_cost_per_k?: number;
    inputCostPerK?: number;
    output_cost_per_k?: number;
    outputCostPerK?: number;
  };
  return {
    id: obj.id ?? obj.model_name ?? obj.name ?? "",
    name: obj.name ?? obj.model_name ?? obj.id ?? "",
    provider: obj.provider ?? "",
    vision: obj.vision ?? obj.supports_vision ?? false,
    contextWindow: obj.context_window ?? obj.contextWindow ?? 0,
    inputCostPerK: obj.input_cost_per_k ?? obj.inputCostPerK ?? 0,
    outputCostPerK: obj.output_cost_per_k ?? obj.outputCostPerK ?? 0,
  };
}

export function ModelsTab() {
  const { data, isLoading, isError } = useModelList();
  const list: ModelView[] = ((data?.models ?? []) as unknown as Record<
    string,
    unknown
  >[]).map(normalizeModel);

  return (
    <Card>
      <div className="flex items-start justify-between gap-4 border-b border-zinc-800 px-4 py-3">
        <div>
          <h2 className="text-[15px] font-semibold text-zinc-50">모델 목록</h2>
          <p className="mt-1 text-xs text-zinc-400">
            Labs에서 사용 가능한 LLM 모델 카탈로그
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-md border border-zinc-800 bg-zinc-950/40 px-2.5 py-1 text-[11px] text-zinc-400">
          <Info className="h-3.5 w-3.5" aria-hidden />
          LiteLLM에서 조회 (읽기 전용)
        </span>
      </div>
      {isLoading ? (
        <div className="space-y-2 p-4">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="h-10 animate-pulse rounded bg-zinc-900/50" />
          ))}
        </div>
      ) : isError ? (
        <div className="p-6">
          <EmptyState
            title="모델 목록을 가져오지 못했습니다"
            description="LiteLLM 게이트웨이 연결을 확인해 주세요."
          />
        </div>
      ) : list.length === 0 ? (
        <div className="p-6">
          <EmptyState
            title="등록된 모델이 없습니다"
            description="LiteLLM 설정을 확인해 주세요."
          />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-800 bg-zinc-950/40 text-xs text-zinc-400">
              <tr>
                <th scope="col" className="px-4 py-2 text-left font-medium">
                  모델
                </th>
                <th scope="col" className="px-4 py-2 text-left font-medium">
                  프로바이더
                </th>
                <th scope="col" className="px-4 py-2 text-center font-medium">
                  Vision
                </th>
                <th scope="col" className="px-4 py-2 text-right font-medium">
                  Input ($/1K)
                </th>
                <th scope="col" className="px-4 py-2 text-right font-medium">
                  Output ($/1K)
                </th>
                <th scope="col" className="px-4 py-2 text-right font-medium">
                  컨텍스트 윈도우
                </th>
              </tr>
            </thead>
            <tbody>
              {list.map((m) => (
                <tr key={m.id} className="border-t border-zinc-800">
                  <td className="px-4 py-2.5">
                    <div className="flex flex-col">
                      <span className="text-sm font-medium text-zinc-100">
                        {m.name}
                      </span>
                      <span className="font-mono text-[11px] text-zinc-500">
                        {m.id}
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge tone="info">
                      {PROVIDER_LABELS[m.provider] ?? m.provider}
                    </Badge>
                  </td>
                  <td className="px-4 py-2.5 text-center">
                    {m.vision ? (
                      <Check
                        className="mx-auto h-4 w-4 text-emerald-400"
                        aria-label="지원"
                      />
                    ) : (
                      <X
                        className="mx-auto h-4 w-4 text-zinc-600"
                        aria-label="미지원"
                      />
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums text-zinc-200">
                    ${m.inputCostPerK.toFixed(5)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums text-zinc-200">
                    ${m.outputCostPerK.toFixed(5)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums text-zinc-300">
                    {formatNumber(m.contextWindow)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
