import type { ItemResult, Run } from "@/lib/mock/types";

export interface SelectedRun extends Run {
  color: string;
  shortLabel: string;
}

export interface RunStatsSummary {
  runId: string;
  shortLabel: string;
  modelName: string;
  promptVersion: number;
  color: string;
  avgScore: number;
  stdDev: number;
  min: number;
  max: number;
  validCount: number;
  totalCount: number;
}

export interface LatencyPercentiles {
  runId: string;
  shortLabel: string;
  color: string;
  p50: number;
  p90: number;
  p99: number;
  avg: number;
}

export interface CostBreakdown {
  runId: string;
  shortLabel: string;
  color: string;
  inputCost: number;
  outputCost: number;
  totalCost: number;
}

export interface TokenBreakdown {
  runId: string;
  shortLabel: string;
  color: string;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
}

export type CompareTab = "score" | "latency" | "cost" | "tokens";

export type SortMode = "diff_desc" | "diff_asc" | "index_asc";

export interface ItemDiffRow {
  itemId: string;
  itemIndex: number;
  input: string;
  expected: string;
  scoresByRun: Record<string, number | null>;
  outputs: Record<string, string>;
  diff: number;
  hasFailure: boolean;
}

export type { ItemResult };
