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
import type { LatencyPercentiles } from "./types";

interface LatencyChartProps {
  data: LatencyPercentiles[];
  distribution?: {
    bins: Array<{ lo: number; hi: number; count: number }>;
  };
}

interface ChartRow {
  name: string;
  P50: number;
  P90: number;
  P99: number;
}

export function LatencyChart({ data, distribution: _distribution }: LatencyChartProps) {
  // distribution histogram support is reserved for future enhancement; currently
  // the percentile bar chart is the primary visualization.
  void _distribution;
  const rows: ChartRow[] = data.map((d) => ({
    name: d.shortLabel,
    P50: Math.round(d.p50),
    P90: Math.round(d.p90),
    P99: Math.round(d.p99),
  }));

  return (
    <div
      role="img"
      aria-label={`Run별 지연시간 P50/P90/P99 비교 차트 (단위: ms)`}
    >
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid stroke={CHART_GRID_STROKE} vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fill: CHART_AXIS_TICK_FILL, fontSize: 12 }}
            stroke={CHART_GRID_STROKE}
          />
          <YAxis
            tick={{ fill: CHART_AXIS_TICK_FILL, fontSize: 12 }}
            stroke={CHART_GRID_STROKE}
            tickFormatter={(v: number) => `${v}ms`}
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            itemStyle={CHART_TOOLTIP_ITEM_STYLE}
            cursor={{ fill: "rgba(63, 63, 70, 0.3)" }}
            formatter={(value: number, name: string) => [`${value}ms`, name]}
          />
          <Legend
            wrapperStyle={{ fontSize: "12px", color: "#a1a1aa" }}
            iconType="circle"
          />
          <Bar dataKey="P50" fill="#34d399" radius={[2, 2, 0, 0]} />
          <Bar dataKey="P90" fill="#fbbf24" radius={[2, 2, 0, 0]} />
          <Bar dataKey="P99" fill="#fb7185" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
