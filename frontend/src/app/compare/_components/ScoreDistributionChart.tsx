"use client";

import { useMemo } from "react";
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
import type { ItemResult, SelectedRun } from "./types";

interface ScoreDistributionChartProps {
  runs: SelectedRun[];
  itemResults: ItemResult[];
}

const BUCKETS = 10;

interface BucketRow {
  bucket: string;
  [runId: string]: number | string;
}

export function ScoreDistributionChart({
  runs,
  itemResults,
}: ScoreDistributionChartProps) {
  const data = useMemo<BucketRow[]>(() => {
    const buckets: BucketRow[] = Array.from({ length: BUCKETS }).map((_, i) => {
      const lo = (i / BUCKETS).toFixed(1);
      const hi = ((i + 1) / BUCKETS).toFixed(1);
      const row: BucketRow = { bucket: `${lo}-${hi}` };
      runs.forEach((r) => {
        row[r.id] = 0;
      });
      return row;
    });

    itemResults.forEach((item) => {
      runs.forEach((r) => {
        const score = item.scoresByRun[r.id];
        if (score === null || score === undefined) return;
        let idx = Math.floor(score * BUCKETS);
        if (idx >= BUCKETS) idx = BUCKETS - 1;
        if (idx < 0) idx = 0;
        const current = buckets[idx][r.id];
        if (typeof current === "number") {
          buckets[idx][r.id] = current + 1;
        }
      });
    });

    return buckets;
  }, [runs, itemResults]);

  return (
    <div
      role="img"
      aria-label="스코어 분포 히스토그램. 0.0~1.0 구간을 10개 버킷으로 나눈 Run별 카운트"
    >
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid stroke={CHART_GRID_STROKE} vertical={false} />
          <XAxis
            dataKey="bucket"
            tick={{ fill: CHART_AXIS_TICK_FILL, fontSize: 11 }}
            stroke={CHART_GRID_STROKE}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fill: CHART_AXIS_TICK_FILL, fontSize: 12 }}
            stroke={CHART_GRID_STROKE}
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            itemStyle={CHART_TOOLTIP_ITEM_STYLE}
            cursor={{ fill: "rgba(63, 63, 70, 0.3)" }}
          />
          <Legend
            wrapperStyle={{ fontSize: "12px", color: "#a1a1aa" }}
            iconType="circle"
          />
          {runs.map((r) => (
            <Bar
              key={r.id}
              dataKey={r.id}
              name={r.shortLabel}
              fill={r.color}
              radius={[2, 2, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
