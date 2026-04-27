/**
 * 실험 도메인 React Query 훅.
 *
 * 참조: docs/API_DESIGN.md §3, §4, §11
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { apiRequest } from "../api";
import { config } from "../config";
import { experiments as mockExperiments } from "../mock/data";
import type {
  ExperimentControlResponse,
  ExperimentCreate,
  ExperimentDetail,
  ExperimentInitResponse,
  ExperimentStatus,
  ExperimentSummary,
  PaginatedResponse,
  SingleTestRequest,
  SingleTestResponse,
} from "../types/api";

export const experimentKeys = {
  all: ["experiments"] as const,
  list: (projectId: string, status?: string, page = 1) =>
    ["experiments", "list", projectId, status ?? "all", page] as const,
  detail: (id: string) => ["experiments", "detail", id] as const,
};

function mockToExperimentSummary(): ExperimentSummary[] {
  return mockExperiments.map((e) => ({
    experiment_id: e.id,
    name: e.name,
    status: e.status as ExperimentStatus,
    runs_total: e.runCount,
    runs_completed: e.completedRuns,
    total_cost: e.totalCostUsd,
    avg_score: e.avgScore ?? undefined,
    created_at: e.createdAt,
  }));
}

function mockToExperimentDetail(id: string): ExperimentDetail | null {
  const e = mockExperiments.find((x) => x.id === id);
  if (!e) return null;
  return {
    experiment_id: e.id,
    name: e.name,
    description: e.description,
    status: e.status as ExperimentStatus,
    project_id: "production-api",
    owner: e.owner,
    created_at: e.createdAt,
    started_at: e.startedAt,
    completed_at: e.completedAt,
    progress: {
      processed: e.completedRuns * (e.itemCount / Math.max(e.runCount, 1)),
      total: e.runCount * e.itemCount,
      percentage:
        e.runCount > 0 ? (e.completedRuns / e.runCount) * 100 : 0,
    },
    runs: e.modelIds.map((m, i) => ({
      run_name: `${e.promptName}_v${e.promptVersions[0]}_${m}`,
      model: m,
      prompt_version: e.promptVersions[0] ?? 1,
      status: e.status as ExperimentStatus,
      items_completed: e.itemCount,
      items_total: e.itemCount,
      avg_score: e.avgScore ?? undefined,
      total_cost: e.totalCostUsd / Math.max(e.modelIds.length, 1),
      avg_latency_ms: e.avgLatencyMs ?? undefined,
    })),
    config_snapshot: {
      prompt_configs: e.promptVersions.map((v) => ({
        name: e.promptName,
        version: v,
      })),
      dataset_name: e.datasetName,
      model_configs: e.modelIds.map((m) => ({ model: m })),
      evaluators: e.evaluatorIds.map((n) => ({ type: "builtin", name: n })),
    },
  };
}

// ─────────────────────────────────────────────────────────────────────
// Hooks
// ─────────────────────────────────────────────────────────────────────

export function useExperimentList(
  projectId: string,
  options: { status?: string; page?: number; pageSize?: number } = {},
): UseQueryResult<PaginatedResponse<ExperimentSummary>> {
  const { status, page = 1, pageSize = 20 } = options;
  return useQuery({
    queryKey: experimentKeys.list(projectId, status, page),
    queryFn: async () => {
      if (config.useMock) {
        const items = mockToExperimentSummary();
        return { items, total: items.length, page, page_size: pageSize };
      }
      return apiRequest<PaginatedResponse<ExperimentSummary>>(
        "/experiments",
        {
          query: {
            project_id: projectId,
            status,
            page,
            page_size: pageSize,
          },
        },
      );
    },
    enabled: !!projectId,
    refetchInterval: config.pollInterval.experimentList,
  });
}

export function useExperimentDetail(
  experimentId: string | null | undefined,
): UseQueryResult<ExperimentDetail> {
  return useQuery({
    queryKey: experimentKeys.detail(experimentId ?? ""),
    queryFn: async () => {
      if (!experimentId) throw new Error("experiment id required");
      if (config.useMock) {
        const detail = mockToExperimentDetail(experimentId);
        if (!detail) throw new Error(`Mock experiment not found: ${experimentId}`);
        return detail;
      }
      return apiRequest<ExperimentDetail>(
        `/experiments/${encodeURIComponent(experimentId)}`,
      );
    },
    enabled: !!experimentId,
  });
}

export interface CreateExperimentInput {
  payload: ExperimentCreate;
  idempotencyKey?: string;
}

export function useCreateExperiment(): UseMutationResult<
  ExperimentInitResponse,
  Error,
  CreateExperimentInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ payload, idempotencyKey }) => {
      if (config.useMock) {
        return {
          experiment_id: `mock-exp-${Date.now()}`,
          status: "running" as ExperimentStatus,
          total_runs: payload.prompt_configs.length * payload.model_configs.length,
          total_items: 100,
          runs: [],
        };
      }
      return apiRequest<ExperimentInitResponse>("/experiments", {
        method: "POST",
        body: payload,
        idempotencyKey,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: experimentKeys.all });
    },
  });
}

export type ExperimentControlAction =
  | "pause"
  | "resume"
  | "cancel"
  | "retry-failed";

export function useExperimentControl(): UseMutationResult<
  ExperimentControlResponse,
  Error,
  { experimentId: string; action: ExperimentControlAction; ifMatch?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ experimentId, action, ifMatch }) => {
      if (config.useMock) {
        return {
          experiment_id: experimentId,
          status:
            action === "pause"
              ? "paused"
              : action === "cancel"
                ? "cancelled"
                : "running",
          updated_at: new Date().toISOString(),
        } as ExperimentControlResponse;
      }
      return apiRequest<ExperimentControlResponse>(
        `/experiments/${encodeURIComponent(experimentId)}/${action}`,
        { method: "POST", ifMatch },
      );
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: experimentKeys.detail(vars.experimentId) });
      qc.invalidateQueries({ queryKey: experimentKeys.all });
    },
  });
}

export function useDeleteExperiment(): UseMutationResult<
  void,
  Error,
  { experimentId: string; ifMatch?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ experimentId, ifMatch }) => {
      if (config.useMock) return;
      await apiRequest(
        `/experiments/${encodeURIComponent(experimentId)}`,
        { method: "DELETE", ifMatch },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: experimentKeys.all });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────
// 단일 테스트 (non-stream). stream=true는 useSingleTestStream 훅 사용
// ─────────────────────────────────────────────────────────────────────

export function useSingleTestRun(): UseMutationResult<
  SingleTestResponse,
  Error,
  { payload: SingleTestRequest; idempotencyKey?: string }
> {
  return useMutation({
    mutationFn: async ({ payload, idempotencyKey }) => {
      if (config.useMock) {
        return {
          trace_id: `mock-trace-${Date.now()}`,
          output: "Mock output",
          usage: { input_tokens: 100, output_tokens: 25, total_tokens: 125 },
          latency_ms: 800,
          cost_usd: 0.0012,
          model: payload.model,
          scores: {},
        };
      }
      return apiRequest<SingleTestResponse>("/tests/single", {
        method: "POST",
        body: { ...payload, stream: false },
        idempotencyKey,
      });
    },
  });
}

export function useCancelSingleTest(): UseMutationResult<
  { trace_id: string; cancelled: boolean },
  Error,
  string
> {
  return useMutation({
    mutationFn: async (traceId) => {
      if (config.useMock) return { trace_id: traceId, cancelled: true };
      return apiRequest<{ trace_id: string; cancelled: boolean }>(
        `/tests/single/${encodeURIComponent(traceId)}/cancel`,
        { method: "POST" },
      );
    },
  });
}
