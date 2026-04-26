"use client";

import { useState } from "react";
import { Check } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";

interface Params {
  temperature: number;
  topP: number;
  maxTokens: number;
  frequencyPenalty: number;
  presencePenalty: number;
  concurrency: number;
}

const DEFAULTS: Params = {
  temperature: 0.7,
  topP: 1,
  maxTokens: 2048,
  frequencyPenalty: 0,
  presencePenalty: 0,
  concurrency: 4,
};

export function ParamsTab() {
  const [params, setParams] = useState<Params>(DEFAULTS);
  const [saved, setSaved] = useState(false);

  const update = <K extends keyof Params>(k: K, v: Params[K]) =>
    setParams((p) => ({ ...p, [k]: v }));

  const onSave = () => {
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1800);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>기본 파라미터</CardTitle>
        <CardDescription>
          새 실험을 만들 때 적용되는 기본값입니다. 실험에서 개별 변경 가능합니다.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-5 sm:grid-cols-2">
          <Field
            id="temperature"
            label="temperature"
            help="0(보수적) ~ 2(창의적)"
          >
            <SliderRow
              value={params.temperature}
              min={0}
              max={2}
              step={0.05}
              onChange={(v) => update("temperature", v)}
            />
          </Field>
          <Field id="top_p" label="top_p" help="누적 확률 컷오프">
            <SliderRow
              value={params.topP}
              min={0}
              max={1}
              step={0.05}
              onChange={(v) => update("topP", v)}
            />
          </Field>
          <Field id="max_tokens" label="max_tokens">
            <Input
              id="max_tokens"
              type="number"
              min={1}
              max={32_000}
              value={params.maxTokens}
              onChange={(e) =>
                update("maxTokens", Number(e.target.value) || 0)
              }
            />
          </Field>
          <Field id="concurrency" label="concurrency" help="동시 실행 수">
            <Input
              id="concurrency"
              type="number"
              min={1}
              max={32}
              value={params.concurrency}
              onChange={(e) =>
                update("concurrency", Number(e.target.value) || 0)
              }
            />
          </Field>
          <Field id="frequency_penalty" label="frequency_penalty">
            <SliderRow
              value={params.frequencyPenalty}
              min={-2}
              max={2}
              step={0.1}
              onChange={(v) => update("frequencyPenalty", v)}
            />
          </Field>
          <Field id="presence_penalty" label="presence_penalty">
            <SliderRow
              value={params.presencePenalty}
              min={-2}
              max={2}
              step={0.1}
              onChange={(v) => update("presencePenalty", v)}
            />
          </Field>
        </div>

        <div className="mt-6 flex items-center justify-end gap-2">
          <Button
            variant="ghost"
            onClick={() => setParams(DEFAULTS)}
            disabled={saved}
          >
            기본값으로 초기화
          </Button>
          <Button variant="primary" onClick={onSave} disabled={saved}>
            {saved ? (
              <>
                <Check className="h-4 w-4" aria-hidden />
                저장됨
              </>
            ) : (
              "저장"
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Field({
  id,
  label,
  help,
  children,
}: {
  id: string;
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-xs font-medium text-zinc-300">
        {label}
      </label>
      {children}
      {help && <p className="text-[11px] text-zinc-500">{help}</p>}
    </div>
  );
}

function SliderRow({
  value,
  min,
  max,
  step,
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-zinc-800 accent-indigo-400"
      />
      <span className="w-14 text-right font-mono text-xs tabular-nums text-zinc-200">
        {value.toFixed(2)}
      </span>
    </div>
  );
}
