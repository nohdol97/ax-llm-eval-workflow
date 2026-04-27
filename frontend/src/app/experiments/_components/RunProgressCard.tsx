"use client";

import type { RunSummary } from "@/lib/types/api";
import { StatusDot } from "@/components/ui/StatusDot";
import { ScoreBadge } from "@/components/ui/ScoreBadge";
import { cn, formatCurrency, formatDuration } from "@/lib/utils";

interface RunProgressCardProps {
  run: RunSummary;
}

export function RunProgressCard({ run }: RunProgressCardProps) {
  const itemsCompleted = run.items_completed ?? 0;
  const itemsTotal = run.items_total ?? 0;
  const avgScore = run.avg_score ?? run.summary?.avg_score ?? null;
  const avgLatency =
    run.avg_latency_ms ??
    run.summary?.avg_latency_ms ??
    run.summary?.avg_latency ??
    null;
  const totalCost = run.total_cost ?? run.summary?.total_cost ?? 0;

  const pct =
    itemsTotal === 0
      ? 0
      : Math.max(0, Math.min(100, (itemsCompleted / itemsTotal) * 100));

  return (
    <div
      className={cn(
        "flex items-center gap-4 rounded-md border border-zinc-800 bg-zinc-900/60 px-4 py-3 transition-colors",
        run.status === "running" && "border-amber-900/60"
      )}
    >
      <div className="flex min-w-[200px] shrink-0 flex-col gap-1">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium text-zinc-100">
            {run.run_name.slice(-12)}
          </span>
          <StatusDot status={run.status} showLabel={false} />
        </div>
        <div className="text-xs text-zinc-400">
          v{run.prompt_version} · {run.model}
        </div>
      </div>

      <div className="flex flex-1 items-center gap-3">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-800">
          <div
            className={cn(
              "h-full transition-all duration-500",
              run.status === "completed" && "bg-emerald-400",
              run.status === "running" && "bg-indigo-500",
              run.status === "failed" && "bg-rose-400",
              run.status === "paused" && "bg-sky-400",
              run.status === "cancelled" && "bg-zinc-600"
            )}
            style={{ width: `${pct}%` }}
            aria-hidden
          />
        </div>
        <div className="w-[88px] text-right font-mono text-xs tabular-nums text-zinc-300">
          {itemsCompleted}/{itemsTotal}
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-4 text-xs">
        <div className="flex flex-col items-end gap-0.5">
          <span className="text-zinc-500">스코어</span>
          <ScoreBadge value={avgScore} size="sm" />
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <span className="text-zinc-500">지연</span>
          <span className="font-mono tabular-nums text-zinc-200">
            {avgLatency !== null ? formatDuration(avgLatency) : "—"}
          </span>
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <span className="text-zinc-500">비용</span>
          <span className="font-mono tabular-nums text-zinc-200">
            {formatCurrency(totalCost, 3)}
          </span>
        </div>
      </div>
    </div>
  );
}
