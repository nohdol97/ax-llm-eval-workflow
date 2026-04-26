"use client";

import { Crown } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import {
  cn,
  formatCurrency,
  formatDuration,
  formatNumber,
} from "@/lib/utils";
import type {
  CompareTab,
  CostBreakdown,
  LatencyPercentiles,
  RunStatsSummary,
  TokenBreakdown,
} from "./types";

interface RunStatsProps {
  tab: CompareTab;
  scoreStats: RunStatsSummary[];
  latencyStats: LatencyPercentiles[];
  costStats: CostBreakdown[];
  tokenStats: TokenBreakdown[];
}

export function RunStats({
  tab,
  scoreStats,
  latencyStats,
  costStats,
  tokenStats,
}: RunStatsProps) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-zinc-200">Run별 통계</h3>
        {tab === "score" && <Badge tone="accent">평균 ± 표준편차</Badge>}
        {tab === "latency" && <Badge tone="info">백분위수 (ms)</Badge>}
        {tab === "cost" && <Badge tone="success">총 비용 (USD)</Badge>}
        {tab === "tokens" && <Badge tone="neutral">총 토큰</Badge>}
      </div>

      {tab === "score" && <ScoreStatsList stats={scoreStats} />}
      {tab === "latency" && <LatencyStatsList stats={latencyStats} />}
      {tab === "cost" && <CostStatsList stats={costStats} />}
      {tab === "tokens" && <TokenStatsList stats={tokenStats} />}
    </div>
  );
}

function StatRow({
  color,
  label,
  promptVersion,
  modelName,
  isBest,
  children,
}: {
  color: string;
  label: string;
  promptVersion?: number;
  modelName?: string;
  isBest?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "rounded-md border p-3 transition-colors",
        isBest
          ? "border-emerald-900/60 bg-emerald-950/20"
          : "border-zinc-800 bg-zinc-900/40"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: color }}
            aria-hidden
          />
          <span className="truncate text-sm font-medium text-zinc-100">
            {label}
          </span>
          {isBest && (
            <Crown className="h-3.5 w-3.5 shrink-0 text-amber-300" aria-label="최고" />
          )}
        </div>
        {promptVersion !== undefined && (
          <Badge tone="muted">v{promptVersion}</Badge>
        )}
      </div>
      {modelName && (
        <div className="mt-1 truncate text-[11px] text-zinc-500">{modelName}</div>
      )}
      <div className="mt-2 text-xs text-zinc-300">{children}</div>
    </div>
  );
}

function ScoreStatsList({ stats }: { stats: RunStatsSummary[] }) {
  const best = stats.reduce<RunStatsSummary | null>((acc, s) => {
    if (!acc || s.avgScore > acc.avgScore) return s;
    return acc;
  }, null);
  return (
    <div className="flex flex-col gap-2">
      {stats.map((s) => (
        <StatRow
          key={s.runId}
          color={s.color}
          label={s.shortLabel}
          modelName={s.modelName}
          promptVersion={s.promptVersion}
          isBest={best?.runId === s.runId}
        >
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-base font-semibold text-zinc-50 tabular-nums">
              {s.avgScore.toFixed(2)}
            </span>
            <span className="text-zinc-400">±</span>
            <span className="font-mono text-zinc-300 tabular-nums">
              {s.stdDev.toFixed(2)}
            </span>
          </div>
          <div className="mt-1 flex gap-3 text-[11px] text-zinc-500">
            <span>min {s.min.toFixed(2)}</span>
            <span>max {s.max.toFixed(2)}</span>
            <span>
              n={s.validCount}/{s.totalCount}
            </span>
          </div>
        </StatRow>
      ))}
    </div>
  );
}

function LatencyStatsList({ stats }: { stats: LatencyPercentiles[] }) {
  const best = stats.reduce<LatencyPercentiles | null>((acc, s) => {
    if (!acc || s.p50 < acc.p50) return s;
    return acc;
  }, null);
  return (
    <div className="flex flex-col gap-2">
      {stats.map((s) => (
        <StatRow
          key={s.runId}
          color={s.color}
          label={s.shortLabel}
          isBest={best?.runId === s.runId}
        >
          <div className="grid grid-cols-3 gap-2 text-[11px]">
            <div>
              <div className="text-zinc-500">P50</div>
              <div className="font-mono text-sm text-emerald-300 tabular-nums">
                {formatDuration(s.p50)}
              </div>
            </div>
            <div>
              <div className="text-zinc-500">P90</div>
              <div className="font-mono text-sm text-amber-300 tabular-nums">
                {formatDuration(s.p90)}
              </div>
            </div>
            <div>
              <div className="text-zinc-500">P99</div>
              <div className="font-mono text-sm text-rose-300 tabular-nums">
                {formatDuration(s.p99)}
              </div>
            </div>
          </div>
        </StatRow>
      ))}
    </div>
  );
}

function CostStatsList({ stats }: { stats: CostBreakdown[] }) {
  const best = stats.reduce<CostBreakdown | null>((acc, s) => {
    if (!acc || s.totalCost < acc.totalCost) return s;
    return acc;
  }, null);
  return (
    <div className="flex flex-col gap-2">
      {stats.map((s) => (
        <StatRow
          key={s.runId}
          color={s.color}
          label={s.shortLabel}
          isBest={best?.runId === s.runId}
        >
          <div className="font-mono text-base font-semibold text-zinc-50 tabular-nums">
            {formatCurrency(s.totalCost, 2)}
          </div>
          <div className="mt-1 flex gap-3 text-[11px] text-zinc-500">
            <span>입력 {formatCurrency(s.inputCost, 3)}</span>
            <span>출력 {formatCurrency(s.outputCost, 3)}</span>
          </div>
        </StatRow>
      ))}
    </div>
  );
}

function TokenStatsList({ stats }: { stats: TokenBreakdown[] }) {
  const best = stats.reduce<TokenBreakdown | null>((acc, s) => {
    if (!acc || s.totalTokens < acc.totalTokens) return s;
    return acc;
  }, null);
  return (
    <div className="flex flex-col gap-2">
      {stats.map((s) => (
        <StatRow
          key={s.runId}
          color={s.color}
          label={s.shortLabel}
          isBest={best?.runId === s.runId}
        >
          <div className="font-mono text-base font-semibold text-zinc-50 tabular-nums">
            {formatNumber(s.totalTokens)}
          </div>
          <div className="mt-1 flex gap-3 text-[11px] text-zinc-500">
            <span>입력 {formatNumber(s.inputTokens)}</span>
            <span>출력 {formatNumber(s.outputTokens)}</span>
          </div>
        </StatRow>
      ))}
    </div>
  );
}
