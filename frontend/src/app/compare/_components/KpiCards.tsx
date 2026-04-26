"use client";

import { motion } from "framer-motion";
import {
  ArrowDown,
  ArrowUp,
  DollarSign,
  Trophy,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useMemo } from "react";
import { Card } from "@/components/ui/Card";
import { cn, formatCurrency, formatDuration } from "@/lib/utils";
import type { SelectedRun } from "./types";

interface KpiCardsProps {
  runs: SelectedRun[];
}

interface KpiInfo {
  key: "score" | "latency" | "cost";
  label: string;
  icon: LucideIcon;
  iconClass: string;
  valueDisplay: string;
  winnerLabel: string;
  winnerColor: string;
  diffText: string;
  diffPositive: boolean;
}

function buildScoreKpi(runs: SelectedRun[]): KpiInfo | null {
  const sorted = [...runs]
    .filter((r) => r.avgScore !== null)
    .sort((a, b) => (b.avgScore ?? 0) - (a.avgScore ?? 0));
  if (sorted.length === 0) return null;
  const winner = sorted[0];
  const second = sorted[1];
  const winnerScore = winner.avgScore ?? 0;
  const secondScore = second?.avgScore ?? 0;
  const diffPct =
    second && secondScore > 0
      ? ((winnerScore - secondScore) / secondScore) * 100
      : 0;
  return {
    key: "score",
    label: "Best Score",
    icon: Trophy,
    iconClass: "text-amber-300",
    valueDisplay: winnerScore.toFixed(2),
    winnerLabel: winner.shortLabel,
    winnerColor: winner.color,
    diffText: second
      ? `${diffPct >= 0 ? "+" : ""}${diffPct.toFixed(1)}% vs 2위`
      : "단일 Run",
    diffPositive: diffPct >= 0,
  };
}

function buildLatencyKpi(runs: SelectedRun[]): KpiInfo | null {
  const sorted = [...runs]
    .filter((r) => r.avgLatencyMs !== null)
    .sort((a, b) => (a.avgLatencyMs ?? 0) - (b.avgLatencyMs ?? 0));
  if (sorted.length === 0) return null;
  const winner = sorted[0];
  const second = sorted[1];
  const winnerLat = winner.avgLatencyMs ?? 0;
  const secondLat = second?.avgLatencyMs ?? 0;
  const ratio = second && winnerLat > 0 ? secondLat / winnerLat : 1;
  return {
    key: "latency",
    label: "Fastest",
    icon: Zap,
    iconClass: "text-sky-300",
    valueDisplay: formatDuration(winnerLat),
    winnerLabel: winner.shortLabel,
    winnerColor: winner.color,
    diffText: second
      ? `${ratio.toFixed(2)}x faster vs 2위`
      : "단일 Run",
    diffPositive: ratio >= 1,
  };
}

function buildCostKpi(runs: SelectedRun[]): KpiInfo | null {
  const sorted = [...runs].sort((a, b) => a.totalCostUsd - b.totalCostUsd);
  if (sorted.length === 0) return null;
  const winner = sorted[0];
  const second = sorted[1];
  const diffPct =
    second && second.totalCostUsd > 0
      ? ((winner.totalCostUsd - second.totalCostUsd) / second.totalCostUsd) * 100
      : 0;
  return {
    key: "cost",
    label: "Cheapest",
    icon: DollarSign,
    iconClass: "text-emerald-300",
    valueDisplay: formatCurrency(winner.totalCostUsd, 2),
    winnerLabel: winner.shortLabel,
    winnerColor: winner.color,
    diffText: second
      ? `${diffPct.toFixed(1)}% vs 2위`
      : "단일 Run",
    diffPositive: diffPct <= 0, // 비용은 낮을수록 좋음
  };
}

export function KpiCards({ runs }: KpiCardsProps) {
  const kpis = useMemo(() => {
    return [buildScoreKpi(runs), buildLatencyKpi(runs), buildCostKpi(runs)].filter(
      (k): k is KpiInfo => k !== null
    );
  }, [runs]);

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      {kpis.map((kpi, idx) => {
        const Icon = kpi.icon;
        const ArrowIcon = kpi.diffPositive ? ArrowUp : ArrowDown;
        return (
          <motion.div
            key={kpi.key}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, delay: idx * 0.05 }}
          >
            <Card
              className={cn(
                "p-4 transition-all duration-150",
                "hover:border-zinc-700 hover:shadow-[0_4px_12px_rgba(0,0,0,0.4)]"
              )}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium uppercase tracking-wide text-zinc-400">
                  {kpi.label}
                </span>
                <Icon className={cn("h-4 w-4", kpi.iconClass)} aria-hidden />
              </div>
              <div className="mt-3 text-3xl font-semibold tabular-nums text-zinc-50">
                {kpi.valueDisplay}
              </div>
              <div className="mt-2 flex items-center gap-2">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: kpi.winnerColor }}
                  aria-hidden
                />
                <span className="truncate text-sm font-medium text-zinc-200">
                  {kpi.winnerLabel}
                </span>
              </div>
              <div
                className={cn(
                  "mt-1 inline-flex items-center gap-1 text-xs",
                  kpi.diffPositive ? "text-emerald-300" : "text-rose-300"
                )}
              >
                <ArrowIcon className="h-3 w-3" aria-hidden />
                <span className="tabular-nums">{kpi.diffText}</span>
              </div>
            </Card>
          </motion.div>
        );
      })}
    </div>
  );
}
