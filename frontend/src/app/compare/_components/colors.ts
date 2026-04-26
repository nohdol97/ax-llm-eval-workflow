// Run별 색상 시리즈 (UI_UX_DESIGN §9.3 — indigo/sky/amber/emerald/rose 순환)
export const RUN_COLORS = [
  "#818cf8", // indigo-400
  "#38bdf8", // sky-400
  "#fbbf24", // amber-400
  "#34d399", // emerald-400
  "#fb7185", // rose-400
] as const;

export function colorForIndex(index: number): string {
  return RUN_COLORS[index % RUN_COLORS.length];
}

// 차트 공통 토큰
export const CHART_GRID_STROKE = "#27272a"; // zinc-800
export const CHART_AXIS_TICK_FILL = "#71717a"; // zinc-500
export const CHART_TOOLTIP_STYLE: React.CSSProperties = {
  background: "#27272a",
  border: "1px solid #3f3f46",
  borderRadius: "6px",
  fontSize: "12px",
  color: "#e4e4e7",
};
export const CHART_TOOLTIP_LABEL_STYLE: React.CSSProperties = {
  color: "#a1a1aa",
  fontSize: "11px",
};
export const CHART_TOOLTIP_ITEM_STYLE: React.CSSProperties = {
  color: "#e4e4e7",
};
