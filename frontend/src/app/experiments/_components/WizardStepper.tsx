"use client";

import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export interface WizardStepDef {
  id: number;
  label: string;
  description?: string;
}

interface WizardStepperProps {
  steps: WizardStepDef[];
  currentStep: number;
}

export function WizardStepper({ steps, currentStep }: WizardStepperProps) {
  return (
    <ol
      className="flex w-full items-stretch gap-2"
      aria-label="실험 생성 진행 단계"
    >
      {steps.map((step, idx) => {
        const isComplete = step.id < currentStep;
        const isActive = step.id === currentStep;
        const isFuture = step.id > currentStep;
        const isLast = idx === steps.length - 1;

        return (
          <li
            key={step.id}
            className="flex flex-1 items-center"
            aria-current={isActive ? "step" : undefined}
          >
            <div className="flex flex-1 items-center gap-3">
              <div
                className={cn(
                  "grid h-8 w-8 shrink-0 place-items-center rounded-full border text-xs font-semibold transition-colors",
                  isComplete &&
                    "border-emerald-500/50 bg-emerald-500/10 text-emerald-300",
                  isActive &&
                    "border-indigo-400 bg-indigo-500/15 text-indigo-200",
                  isFuture && "border-zinc-700 bg-zinc-900 text-zinc-500"
                )}
              >
                {isComplete ? (
                  <Check className="h-4 w-4" aria-hidden />
                ) : (
                  step.id
                )}
              </div>
              <div className="flex min-w-0 flex-col">
                <span
                  className={cn(
                    "text-sm font-medium",
                    isComplete && "text-emerald-300",
                    isActive && "text-zinc-100",
                    isFuture && "text-zinc-500"
                  )}
                >
                  {step.label}
                </span>
                {step.description && (
                  <span className="truncate text-[11px] text-zinc-500">
                    {step.description}
                  </span>
                )}
              </div>
            </div>
            {!isLast && (
              <div
                className={cn(
                  "mx-2 h-px flex-1",
                  isComplete ? "bg-emerald-400/60" : "bg-zinc-700"
                )}
                aria-hidden
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}
