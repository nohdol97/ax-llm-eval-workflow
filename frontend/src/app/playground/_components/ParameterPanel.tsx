"use client";

import { Input } from "@/components/ui/Input";
import { cn } from "@/lib/utils";

export interface ModelParameters {
  temperature: number;
  topP: number;
  maxTokens: number;
  frequencyPenalty: number;
  presencePenalty: number;
}

export const DEFAULT_PARAMETERS: ModelParameters = {
  temperature: 0.7,
  topP: 1.0,
  maxTokens: 1024,
  frequencyPenalty: 0,
  presencePenalty: 0,
};

interface ParameterPanelProps {
  value: ModelParameters;
  onChange: (next: ModelParameters) => void;
}

interface SliderRowProps {
  label: string;
  hint?: string;
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
  fixed?: number;
}

function SliderRow({
  label,
  hint,
  min,
  max,
  step,
  value,
  onChange,
  fixed = 2,
}: SliderRowProps) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <label className="text-xs font-medium text-zinc-300">
          {label}
          {hint && (
            <span className="ml-2 font-normal text-[11px] text-zinc-500">
              {hint}
            </span>
          )}
        </label>
        <span className="font-mono text-[11px] tabular-nums text-zinc-300">
          {fixed === 0 ? value.toFixed(0) : value.toFixed(fixed)}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className={cn(
            "h-1 flex-1 cursor-pointer appearance-none rounded-full bg-zinc-800",
            "accent-indigo-400 focus-visible:outline-none"
          )}
          aria-label={label}
        />
      </div>
    </div>
  );
}

export function ParameterPanel({ value, onChange }: ParameterPanelProps) {
  const update = <K extends keyof ModelParameters>(
    key: K,
    v: ModelParameters[K]
  ) => onChange({ ...value, [key]: v });

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <SliderRow
        label="temperature"
        hint="0=결정적, 2=창의적"
        min={0}
        max={2}
        step={0.05}
        value={value.temperature}
        onChange={(v) => update("temperature", v)}
      />
      <SliderRow
        label="top_p"
        hint="누적 확률 컷오프"
        min={0}
        max={1}
        step={0.05}
        value={value.topP}
        onChange={(v) => update("topP", v)}
      />
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <label htmlFor="param-max-tokens" className="text-xs font-medium text-zinc-300">
            max_tokens
          </label>
        </div>
        <Input
          id="param-max-tokens"
          type="number"
          min={1}
          max={32_000}
          step={64}
          value={value.maxTokens}
          onChange={(e) => {
            const next = Number(e.target.value);
            if (Number.isFinite(next) && next > 0) {
              update("maxTokens", Math.min(32_000, Math.round(next)));
            }
          }}
          className="font-mono"
        />
      </div>
      <div /> {/* spacer to keep 2x2 grid aligned */}
      <SliderRow
        label="frequency_penalty"
        hint="-2 ~ 2"
        min={-2}
        max={2}
        step={0.1}
        value={value.frequencyPenalty}
        onChange={(v) => update("frequencyPenalty", v)}
      />
      <SliderRow
        label="presence_penalty"
        hint="-2 ~ 2"
        min={-2}
        max={2}
        step={0.1}
        value={value.presencePenalty}
        onChange={(v) => update("presencePenalty", v)}
      />
    </div>
  );
}
