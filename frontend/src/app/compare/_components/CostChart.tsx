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
import type { CostBreakdown } from "./types";

interface CostChartProps {
  data: CostBreakdown[];
  distribution?: {
    items: Array<{
      run_name: string;
      model_cost?: number;
      eval_cost?: number;
    }>;
  };
}

interface ChartRow {
  name: string;
  inputCost: number;
  outputCost: number;
  modelCost?: number;
  evalCost?: number;
}

export function CostChart({ data, distribution }: CostChartProps) {
  const hasModelEvalSplit = data.some(
    (d) => d.modelCost !== undefined || d.evalCost !== undefined
  );

  const rows: ChartRow[] = data.map((d) => {
    const dist = distribution?.items?.find((x) => x.run_name === d.runId);
    return {
      name: d.shortLabel,
      inputCost: Number(d.inputCost.toFixed(4)),
      outputCost: Number(d.outputCost.toFixed(4)),
      modelCost:
        d.modelCost ??
        dist?.model_cost ??
        undefined,
      evalCost:
        d.evalCost ??
        dist?.eval_cost ??
        undefined,
    };
  });

  void hasModelEvalSplit;

  return (
    <div
      role="img"
      aria-label={`Run별 비용 (입력/출력) 스택 막대 차트`}
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
            tickFormatter={(v: number) => `$${v.toFixed(2)}`}
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            itemStyle={CHART_TOOLTIP_ITEM_STYLE}
            cursor={{ fill: "rgba(63, 63, 70, 0.3)" }}
            formatter={(value: number, name: string) => [
              `$${value.toFixed(4)}`,
              name === "inputCost" ? "입력 비용" : "출력 비용",
            ]}
          />
          <Legend
            wrapperStyle={{ fontSize: "12px", color: "#a1a1aa" }}
            iconType="circle"
            formatter={(value: string) =>
              value === "inputCost" ? "입력 비용" : "출력 비용"
            }
          />
          <Bar
            dataKey="inputCost"
            stackId="cost"
            fill="#818cf8"
            radius={[0, 0, 0, 0]}
          />
          <Bar
            dataKey="outputCost"
            stackId="cost"
            fill="#38bdf8"
            radius={[2, 2, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
