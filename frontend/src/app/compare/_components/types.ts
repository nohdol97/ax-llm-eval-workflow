/**
 * Compare 페이지 전용 뷰 모델 타입.
 *
 * Phase 7-A의 RunSummary / CompareItemEntry 등 backend mirror 타입은
 * camelCase가 아니라 snake_case이므로, 여기서는 페이지에 친화적인 어댑터
 * 타입(SelectedRun, ItemResult 등)을 정의한다.
 */

export interface SelectedRun {
  /** Run identifier — backend의 run_name 또는 run_id를 그대로 사용 */
  id: string;
  modelName: string;
  promptVersion: number;
  status: string;
  itemsCompleted: number;
  itemsTotal: number;
  avgScore: number | null;
  avgLatencyMs: number | null;
  totalCostUsd: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  scoresByEvaluator?: Record<string, number>;
  color: string;
  shortLabel: string;
}

export interface ItemResult {
  itemId: string;
  itemIndex: number;
  input: string;
  expected: string;
  outputs: Record<string, string>;
  scoresByRun: Record<string, number | null>;
  latenciesByRun: Record<string, number>;
  costsByRun: Record<string, number>;
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
  /** Phase 5: split between model invocation cost and evaluator cost */
  modelCost?: number;
  evalCost?: number;
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

