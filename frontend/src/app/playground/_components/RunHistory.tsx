"use client";

import { Clock, RotateCcw } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { ScoreBadge } from "@/components/ui/ScoreBadge";
import {
  cn,
  formatCurrency,
  formatDuration,
  formatRelativeDate,
} from "@/lib/utils";
import type { RunHistoryEntry } from "./mockResponse";

interface RunHistoryProps {
  entries: RunHistoryEntry[];
  onReplay: (entry: RunHistoryEntry) => void;
}

export function RunHistory({ entries, onReplay }: RunHistoryProps) {
  if (entries.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-zinc-800 bg-zinc-950/40 px-3 py-4 text-center text-xs text-zinc-500">
        아직 실행 기록이 없습니다.
      </div>
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {entries.map((entry) => (
        <li key={entry.id}>
          <button
            type="button"
            onClick={() => onReplay(entry)}
            className={cn(
              "group flex w-full items-start gap-3 rounded-md border border-zinc-800",
              "bg-zinc-900 px-3 py-2 text-left transition-colors hover:border-zinc-700 hover:bg-zinc-800/60",
              "focus-visible:outline-none"
            )}
            aria-label={`${entry.modelName}로 실행한 결과 다시 보기`}
          >
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge tone="accent">{entry.modelName}</Badge>
                <Badge tone="muted">
                  {entry.promptName} v{entry.promptVersion}
                </Badge>
                {entry.partial && <Badge tone="error">partial</Badge>}
              </div>
              <p className="truncate text-xs text-zinc-400">
                {entry.response.split("\n")[0]?.slice(0, 80) || "(빈 응답)"}
              </p>
              <div className="flex flex-wrap items-center gap-3 text-[11px] text-zinc-500">
                <span className="inline-flex items-center gap-1">
                  <Clock className="h-3 w-3" aria-hidden />
                  {formatRelativeDate(entry.createdAt)}
                </span>
                <span className="font-mono tabular-nums">
                  {formatDuration(entry.meta.latencyMs)}
                </span>
                <span className="font-mono tabular-nums">
                  {entry.meta.inputTokens + entry.meta.outputTokens} tok
                </span>
                <span className="font-mono tabular-nums">
                  {formatCurrency(entry.meta.costUsd, 4)}
                </span>
              </div>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-1">
              <ScoreBadge value={entry.score} size="sm" />
              <RotateCcw
                className="h-3.5 w-3.5 text-zinc-600 transition-colors group-hover:text-zinc-400"
                aria-hidden
              />
            </div>
          </button>
        </li>
      ))}
    </ul>
  );
}
