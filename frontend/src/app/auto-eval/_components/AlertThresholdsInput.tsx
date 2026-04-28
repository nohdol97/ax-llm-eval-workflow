"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import type { AlertThreshold } from "@/lib/types/api";

interface AlertThresholdsInputProps {
  value: AlertThreshold[];
  onChange: (next: AlertThreshold[]) => void;
}

const METRIC_OPTIONS: Array<{ id: AlertThreshold["metric"]; label: string }> = [
  { id: "avg_score", label: "평균 스코어" },
  { id: "pass_rate", label: "통과율" },
  { id: "evaluator_score", label: "특정 evaluator 점수" },
];

const OPERATOR_OPTIONS: Array<{
  id: AlertThreshold["operator"];
  label: string;
}> = [
  { id: "lt", label: "<" },
  { id: "lte", label: "≤" },
  { id: "gt", label: ">" },
  { id: "gte", label: "≥" },
];

function defaultThreshold(): AlertThreshold {
  return { metric: "pass_rate", operator: "lt", value: 0.85 };
}

export function AlertThresholdsInput({
  value,
  onChange,
}: AlertThresholdsInputProps) {
  const update = (idx: number, patch: Partial<AlertThreshold>) => {
    const next = value.map((t, i) => (i === idx ? { ...t, ...patch } : t));
    onChange(next);
  };

  const remove = (idx: number) => {
    onChange(value.filter((_, i) => i !== idx));
  };

  const add = () => {
    onChange([...value, defaultThreshold()]);
  };

  return (
    <div className="space-y-3 rounded-md border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h4 className="text-sm font-medium text-zinc-200">알림 조건</h4>
          <p className="mt-0.5 text-[11px] text-zinc-500">
            지정한 임계값을 벗어나면 알림이 발송됩니다.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={add} type="button">
          <Plus className="h-3.5 w-3.5" aria-hidden />
          조건 추가
        </Button>
      </div>

      {value.length === 0 ? (
        <p className="rounded-md border border-dashed border-zinc-800 px-3 py-4 text-center text-xs text-zinc-500">
          알림 조건이 없습니다. 추가 버튼을 눌러 조건을 정의하세요.
        </p>
      ) : (
        <div className="space-y-2">
          {value.map((t, idx) => (
            <div
              key={idx}
              className="grid grid-cols-1 gap-2 rounded-md border border-zinc-800 bg-zinc-950/40 p-3 sm:grid-cols-[180px_minmax(140px,1fr)_80px_120px_auto]"
            >
              <Select
                aria-label={`임계값 ${idx + 1} metric`}
                value={t.metric}
                onChange={(e) =>
                  update(idx, {
                    metric: e.target.value as AlertThreshold["metric"],
                  })
                }
              >
                {METRIC_OPTIONS.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </Select>
              {t.metric === "evaluator_score" ? (
                <Input
                  aria-label={`임계값 ${idx + 1} evaluator name`}
                  placeholder="evaluator 이름"
                  value={t.evaluator_name ?? ""}
                  onChange={(e) =>
                    update(idx, { evaluator_name: e.target.value })
                  }
                />
              ) : (
                <span className="hidden sm:block" />
              )}
              <Select
                aria-label={`임계값 ${idx + 1} operator`}
                value={t.operator}
                onChange={(e) =>
                  update(idx, {
                    operator: e.target.value as AlertThreshold["operator"],
                  })
                }
              >
                {OPERATOR_OPTIONS.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </Select>
              <Input
                type="number"
                step={0.01}
                aria-label={`임계값 ${idx + 1} 값`}
                value={t.value}
                onChange={(e) => update(idx, { value: Number(e.target.value) })}
              />
              <Button
                variant="ghost"
                size="iconSm"
                onClick={() => remove(idx)}
                aria-label={`임계값 ${idx + 1} 제거`}
                type="button"
              >
                <Trash2 className="h-4 w-4 text-rose-300" />
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
