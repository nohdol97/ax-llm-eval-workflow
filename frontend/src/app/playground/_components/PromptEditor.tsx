"use client";

import { useEffect, useMemo, useRef } from "react";
import { cn } from "@/lib/utils";

const VARIABLE_REGEX = /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g;

export function extractVariables(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  let match: RegExpExecArray | null;
  // create a fresh regex to avoid lastIndex pitfalls with global regex literals
  const re = new RegExp(VARIABLE_REGEX.source, "g");
  while ((match = re.exec(text)) !== null) {
    const name = match[1];
    if (!seen.has(name)) {
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}

interface PromptEditorProps {
  value: string;
  onChange: (next: string) => void;
  onVariablesChange: (vars: string[]) => void;
  onVariableClick?: (name: string) => void;
  /** Run callback when user presses Cmd/Ctrl + Enter inside the editor */
  onRun?: () => void;
  detectedVariables: string[];
}

export function PromptEditor({
  value,
  onChange,
  onVariablesChange,
  onVariableClick,
  onRun,
  detectedVariables,
}: PromptEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const lastReportedRef = useRef<string>("");

  const vars = useMemo(() => extractVariables(value), [value]);

  useEffect(() => {
    const key = vars.join("|");
    if (key !== lastReportedRef.current) {
      lastReportedRef.current = key;
      onVariablesChange(vars);
    }
  }, [vars, onVariablesChange]);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <label
          htmlFor="prompt-editor-textarea"
          className="text-xs font-medium text-zinc-300"
        >
          프롬프트 본문
        </label>
        <span className="text-[11px] text-zinc-500">
          {value.length.toLocaleString("ko-KR")}자 · {vars.length} 변수
        </span>
      </div>
      <textarea
        id="prompt-editor-textarea"
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            onRun?.();
          }
        }}
        spellCheck={false}
        className={cn(
          "min-h-[220px] w-full resize-y rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2",
          "font-mono text-[13px] leading-relaxed text-zinc-100 placeholder:text-zinc-500",
          "focus-visible:border-indigo-400 focus-visible:outline-none"
        )}
        placeholder="프롬프트 본문을 입력하세요. {{변수명}} 형식으로 변수를 정의할 수 있습니다."
        aria-describedby="prompt-editor-vars"
      />
      {detectedVariables.length > 0 && (
        <div
          id="prompt-editor-vars"
          className="flex flex-wrap items-center gap-1.5"
        >
          <span className="text-[11px] text-zinc-500">감지된 변수:</span>
          {detectedVariables.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => onVariableClick?.(name)}
              className={cn(
                "rounded border border-indigo-500/30 bg-indigo-500/15 px-1.5 py-0.5",
                "font-mono text-xs text-indigo-300 transition-colors",
                "hover:bg-indigo-500/25 focus-visible:outline-none"
              )}
              aria-label={`변수 ${name} 입력란으로 이동`}
            >
              {name}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
