"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
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
import { formatNumber } from "@/lib/utils";
import type { TokenBreakdown } from "./types";

interface TokenChartProps {
  data: TokenBreakdown[];
}

interface ChartRow {
  name: string;
  inputTokens: number;
  outputTokens: number;
}

export function TokenChart({ data }: TokenChartProps) {
  const rows: ChartRow[] = data.map((d) => ({
    name: d.shortLabel,
    inputTokens: d.inputTokens,
    outputTokens: d.outputTokens,
  }));

  return (
    <div
      role="img"
      aria-label="Run별 토큰 사용량 (입력/출력) 스택 막대 차트"
    >
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid stroke={CHART_GRID_STROKE} vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fill: CHART_AXIS_TICK_FILL, fontSize: 12 }}
            stroke={CHART_GRID_STROKE}
          />
          <YAxis
            tick={{ fill: CHART_AXIS_TICK_FILL, fontSize: 12 }}
            stroke={CHART_GRID_STROKE}
            tickFormatter={(v: number) =>
              v >= 1000 ? `${(v / 1000).toFixed(0)}K` : `${v}`
            }
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            itemStyle={CHART_TOOLTIP_ITEM_STYLE}
            cursor={{ fill: "rgba(63, 63, 70, 0.3)" }}
            formatter={(value: number, name: string) => [
              formatNumber(value),
              name === "inputTokens" ? "입력 토큰" : "출력 토큰",
            ]}
          />
          <Legend
            wrapperStyle={{ fontSize: "12px", color: "#a1a1aa" }}
            iconType="circle"
            formatter={(value: string) =>
              value === "inputTokens" ? "입력 토큰" : "출력 토큰"
            }
          />
          <Bar
            dataKey="inputTokens"
            stackId="tokens"
            fill="#a5b4fc"
            radius={[0, 0, 0, 0]}
          />
          <Bar
            dataKey="outputTokens"
            stackId="tokens"
            fill="#34d399"
            radius={[2, 2, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
