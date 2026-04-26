"use client";

import { forwardRef, useImperativeHandle, useRef } from "react";
import { Textarea } from "@/components/ui/Input";
import { cn } from "@/lib/utils";

export interface VariableFormHandle {
  focusVariable: (name: string) => void;
}

interface VariableFormProps {
  variables: string[];
  values: Record<string, string>;
  onChange: (name: string, value: string) => void;
  errors: Set<string>;
}

export const VariableForm = forwardRef<VariableFormHandle, VariableFormProps>(
  function VariableForm({ variables, values, onChange, errors }, ref) {
    const inputRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});

    useImperativeHandle(ref, () => ({
      focusVariable(name: string) {
        const el = inputRefs.current[name];
        if (el) {
          el.focus();
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      },
    }));

    if (variables.length === 0) {
      return (
        <p className="text-xs text-zinc-500">
          프롬프트에 <code className="font-mono">{"{{변수명}}"}</code> 패턴이
          없습니다.
        </p>
      );
    }

    return (
      <div className="flex flex-col gap-3">
        {variables.map((name) => {
          const id = `var-input-${name}`;
          const hasError = errors.has(name);
          return (
            <div key={name} className="flex flex-col gap-1">
              <label
                htmlFor={id}
                className="flex items-center gap-2 text-xs font-medium text-zinc-300"
              >
                <span className="font-mono text-indigo-300">{name}</span>
                {hasError && (
                  <span
                    role="alert"
                    className="text-[11px] font-normal text-rose-300"
                  >
                    값을 입력하세요
                  </span>
                )}
              </label>
              <Textarea
                id={id}
                ref={(el) => {
                  inputRefs.current[name] = el;
                }}
                rows={2}
                value={values[name] ?? ""}
                onChange={(e) => onChange(name, e.target.value)}
                className={cn(
                  "min-h-[44px] font-mono text-[13px]",
                  hasError && "border-rose-500/60 focus-visible:border-rose-400"
                )}
                placeholder={`${name} 값을 입력하세요`}
                aria-invalid={hasError || undefined}
              />
            </div>
          );
        })}
      </div>
    );
  }
);
