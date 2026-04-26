"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  CHART_AXIS_TICK_FILL,
  CHART_GRID_STROKE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
  CHART_TOOLTIP_STYLE,
} from "./colors";
import type { SelectedRun } from "./types";

interface ScoreChartProps {
  runs: SelectedRun[];
}

export function ScoreChart({ runs }: ScoreChartProps) {
  const data = runs.map((r) => ({
    name: r.shortLabel,
    score: Number((r.avgScore ?? 0).toFixed(3)),
    color: r.color,
    modelName: r.modelName,
  }));

  return (
    <div
      role="img"
      aria-label={`Run별 평균 스코어 비교 차트. ${data
        .map((d) => `${d.name}: ${d.score}`)
        .join(", ")}`}
    >
      <ResponsiveContainer width="100%" height={300}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
        >
          <CartesianGrid stroke={CHART_GRID_STROKE} horizontal={false} />
          <XAxis
            type="number"
            domain={[0, 1]}
            tick={{ fill: CHART_AXIS_TICK_FILL, fontSize: 12 }}
            stroke={CHART_GRID_STROKE}
          />
          <YAxis
            type="category"
            dataKey="name"
            tick={{ fill: CHART_AXIS_TICK_FILL, fontSize: 12 }}
            stroke={CHART_GRID_STROKE}
            width={120}
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            itemStyle={CHART_TOOLTIP_ITEM_STYLE}
            cursor={{ fill: "rgba(63, 63, 70, 0.3)" }}
            formatter={(value: number) => [value.toFixed(3), "평균 스코어"]}
          />
          <Bar dataKey="score" radius={[0, 4, 4, 0]}>
            {data.map((entry) => (
              <Cell key={entry.name} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
