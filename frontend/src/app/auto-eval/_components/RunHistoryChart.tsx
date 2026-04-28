"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { AutoEvalRun } from "@/lib/types/api";

interface RunHistoryChartProps {
  runs: AutoEvalRun[];
}

interface ChartPoint {
  date: string;
  avg_score: number | null;
  pass_rate: number | null;
  cost: number;
}

const CHART_HEIGHT = 280;

const TOOLTIP_STYLE: React.CSSProperties = {
  background: "#27272a",
  border: "1px solid #3f3f46",
  borderRadius: "6px",
  fontSize: "12px",
  color: "#e4e4e7",
};

export function RunHistoryChart({ runs }: RunHistoryChartProps) {
  const data: ChartPoint[] = runs
    .filter((r) => r.status === "completed")
    .slice()
    .sort((a, b) => a.started_at.localeCompare(b.started_at))
    .map((r) => ({
      date: r.started_at.slice(0, 10),
      avg_score: r.avg_score ?? null,
      pass_rate: r.pass_rate ?? null,
      cost: Number(r.cost_usd.toFixed(4)),
    }));

  if (data.length === 0) {
    return (
      <div className="grid h-[200px] place-items-center rounded-md border border-dashed border-zinc-800 bg-zinc-950/40 text-xs text-zinc-500">
        완료된 실행 기록이 없습니다.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart
        data={data}
        margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
      >
        <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
        <XAxis
          dataKey="date"
          tick={{ fill: "#71717a", fontSize: 12 }}
          stroke="#3f3f46"
        />
        <YAxis
          domain={[0, 1]}
          tick={{ fill: "#71717a", fontSize: 12 }}
          stroke="#3f3f46"
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelStyle={{ color: "#a1a1aa" }}
        />
        <Legend
          wrapperStyle={{ fontSize: "12px", color: "#a1a1aa" }}
          iconType="circle"
        />
        <Line
          type="monotone"
          dataKey="avg_score"
          name="평균 스코어"
          stroke="#818cf8"
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="pass_rate"
          name="통과율"
          stroke="#34d399"
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

interface CostChartProps {
  runs: AutoEvalRun[];
  dailyLimitUsd?: number;
}

export function CostChart({ runs, dailyLimitUsd }: CostChartProps) {
  // 일자별 비용 집계
  const buckets = new Map<string, number>();
  for (const r of runs) {
    const day = r.started_at.slice(0, 10);
    buckets.set(day, (buckets.get(day) ?? 0) + r.cost_usd);
  }
  const data = Array.from(buckets.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([date, cost]) => ({
      date,
      cost: Number(cost.toFixed(4)),
      limit: dailyLimitUsd ?? null,
    }));

  if (data.length === 0) {
    return (
      <div className="grid h-[200px] place-items-center rounded-md border border-dashed border-zinc-800 bg-zinc-950/40 text-xs text-zinc-500">
        비용 기록이 없습니다.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart
        data={data}
        margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
      >
        <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
        <XAxis
          dataKey="date"
          tick={{ fill: "#71717a", fontSize: 12 }}
          stroke="#3f3f46"
        />
        <YAxis
          tick={{ fill: "#71717a", fontSize: 12 }}
          stroke="#3f3f46"
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelStyle={{ color: "#a1a1aa" }}
          formatter={(value: number) => `$${value.toFixed(4)}`}
        />
        <Legend
          wrapperStyle={{ fontSize: "12px", color: "#a1a1aa" }}
          iconType="circle"
        />
        <Line
          type="monotone"
          dataKey="cost"
          name="일일 비용 (USD)"
          stroke="#fbbf24"
          strokeWidth={2}
          dot={false}
        />
        {dailyLimitUsd != null && (
          <Line
            type="monotone"
            dataKey="limit"
            name="일일 한도"
            stroke="#f87171"
            strokeWidth={1}
            strokeDasharray="6 3"
            dot={false}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}

interface EvaluatorBreakdownChartProps {
  runs: AutoEvalRun[];
}

const EVALUATOR_COLORS = [
  "#818cf8",
  "#34d399",
  "#fbbf24",
  "#f87171",
  "#22d3ee",
  "#c084fc",
];

export function EvaluatorBreakdownChart({
  runs,
}: EvaluatorBreakdownChartProps) {
  const completed = runs.filter((r) => r.status === "completed");
  if (completed.length === 0) {
    return (
      <div className="grid h-[200px] place-items-center rounded-md border border-dashed border-zinc-800 bg-zinc-950/40 text-xs text-zinc-500">
        evaluator 점수 기록이 없습니다.
      </div>
    );
  }

  const evaluatorNames = Array.from(
    new Set(completed.flatMap((r) => Object.keys(r.scores_by_evaluator))),
  );

  if (evaluatorNames.length === 0) {
    return (
      <div className="grid h-[200px] place-items-center rounded-md border border-dashed border-zinc-800 bg-zinc-950/40 text-xs text-zinc-500">
        evaluator 점수 기록이 없습니다.
      </div>
    );
  }

  type Row = { date: string } & Record<string, number | null | string>;
  const data: Row[] = completed
    .slice()
    .sort((a, b) => a.started_at.localeCompare(b.started_at))
    .map((r) => {
      const row: Row = { date: r.started_at.slice(0, 10) };
      for (const name of evaluatorNames) {
        row[name] = r.scores_by_evaluator[name] ?? null;
      }
      return row;
    });

  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart
        data={data}
        margin={{ top: 8, right: 16, left: 0, bottom: 0 }}
      >
        <CartesianGrid stroke="#27272a" strokeDasharray="3 3" />
        <XAxis
          dataKey="date"
          tick={{ fill: "#71717a", fontSize: 12 }}
          stroke="#3f3f46"
        />
        <YAxis
          domain={[0, 1]}
          tick={{ fill: "#71717a", fontSize: 12 }}
          stroke="#3f3f46"
        />
        <Tooltip
          contentStyle={TOOLTIP_STYLE}
          labelStyle={{ color: "#a1a1aa" }}
        />
        <Legend
          wrapperStyle={{ fontSize: "12px", color: "#a1a1aa" }}
          iconType="circle"
        />
        {evaluatorNames.map((name, idx) => (
          <Line
            key={name}
            type="monotone"
            dataKey={name}
            name={name}
            stroke={EVALUATOR_COLORS[idx % EVALUATOR_COLORS.length]}
            strokeWidth={2}
            dot={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
