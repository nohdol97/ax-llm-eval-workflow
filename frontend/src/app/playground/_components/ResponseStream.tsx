"use client";

import { Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  cn,
  formatCurrency,
  formatDuration,
  formatNumber,
} from "@/lib/utils";
import type { MockResponseMeta } from "./mockResponse";

export type StreamStatus = "idle" | "streaming" | "completed" | "stopped";

interface ResponseStreamProps {
  status: StreamStatus;
  text: string;
  meta: MockResponseMeta | null;
  modelName: string;
  promptName?: string;
}

export function ResponseStream({
  status,
  text,
  meta,
  modelName,
  promptName,
}: ResponseStreamProps) {
  if (status === "idle" && text.length === 0) {
    return (
      <EmptyState
        icon={<Sparkles className="h-8 w-8" />}
        title="실행하면 여기에 결과가 표시됩니다"
        description={
          <span>
            <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
              ⌘
            </kbd>
            <span className="mx-1">+</span>
            <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 font-mono text-[11px] text-zinc-300">
              Enter
            </kbd>
            로도 실행할 수 있습니다.
          </span>
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">{modelName}</Badge>
        {promptName && <Badge tone="muted">{promptName}</Badge>}
        {status === "streaming" && (
          <Badge tone="warning">
            <span className="inline-block h-1.5 w-1.5 animate-pulse-dot rounded-full bg-amber-400" />
            스트리밍 중
          </Badge>
        )}
        {status === "completed" && <Badge tone="success">완료</Badge>}
        {status === "stopped" && <Badge tone="error">중단됨 · partial</Badge>}
      </div>

      <div
        aria-live="polite"
        aria-atomic="false"
        className={cn(
          "min-h-[200px] whitespace-pre-wrap rounded-md border border-zinc-800",
          "bg-zinc-950/70 p-4 font-mono text-[13px] leading-relaxed text-zinc-100",
          "selection:bg-indigo-500/30"
        )}
      >
        {text}
        {status === "streaming" && (
          <span className="cursor-blink" aria-hidden />
        )}
      </div>

      {meta && (status === "completed" || status === "stopped") && (
        <dl className="grid grid-cols-2 gap-2 rounded-md border border-zinc-800 bg-zinc-900/40 p-3 text-xs sm:grid-cols-4">
          <MetaItem label="응답시간" value={formatDuration(meta.latencyMs)} />
          <MetaItem
            label="입력 토큰"
            value={formatNumber(meta.inputTokens)}
          />
          <MetaItem
            label="출력 토큰"
            value={formatNumber(meta.outputTokens)}
          />
          <MetaItem
            label="비용"
            value={formatCurrency(meta.costUsd, 4)}
            highlight
          />
        </dl>
      )}
    </div>
  );
}

function MetaItem({
  label,
  value,
  highlight = false,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[11px] uppercase tracking-wide text-zinc-500">
        {label}
      </dt>
      <dd
        className={cn(
          "font-mono text-sm tabular-nums",
          highlight ? "text-indigo-300" : "text-zinc-200"
        )}
      >
        {value}
      </dd>
    </div>
  );
}
