/**
 * Phase 8-B: Auto-Eval 도메인 React Query 훅.
 *
 * Backend 12 endpoints에 대한 클라이언트 어댑터.
 *
 * - {@link useAutoEvalPolicyList}: ``GET /auto-eval/policies``
 * - {@link useAutoEvalPolicy}: ``GET /auto-eval/policies/{id}``
 * - {@link useCreateAutoEvalPolicy}: ``POST /auto-eval/policies``
 * - {@link useUpdateAutoEvalPolicy}: ``PATCH /auto-eval/policies/{id}`` (If-Match)
 * - {@link useDeleteAutoEvalPolicy}: ``DELETE /auto-eval/policies/{id}`` (admin only)
 * - {@link usePausePolicy} / {@link useResumePolicy}: 상태 전환
 * - {@link useRunPolicyNow}: ``POST /auto-eval/policies/{id}/run-now``
 * - {@link useAutoEvalRunList}: ``GET /auto-eval/runs?policy_id=…``
 * - {@link useAutoEvalRun}: ``GET /auto-eval/runs/{id}``
 * - {@link useCostUsage}: ``GET /auto-eval/policies/{id}/cost-usage``
 *
 * mock 모드(``config.useMock=true``)에서는 ``mock/data.ts``의
 * ``autoEvalPolicies`` / ``autoEvalRuns`` / ``buildMockCostUsage``를 사용.
 *
 * 참조: docs/AGENT_EVAL.md §13
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
import {
  autoEvalPolicies as mockPolicies,
  autoEvalRuns as mockRuns,
  buildMockCostUsage,
} from "../mock/data";
import type {
  AutoEvalPolicy,
  AutoEvalPolicyCreate,
  AutoEvalPolicyListResponse,
  AutoEvalPolicyUpdate,
  AutoEvalRun,
  AutoEvalRunListResponse,
  AutoEvalRunStatus,
  CostUsage,
  PolicyStatus,
} from "../types/api";

export const autoEvalKeys = {
  all: ["auto-eval"] as const,
  policies: (projectId?: string, status?: string, page?: number) =>
    ["auto-eval", "policies", projectId ?? null, status ?? null, page ?? 1] as const,
  policy: (id: string) => ["auto-eval", "policy", id] as const,
  runs: (policyId: string, status?: string, page?: number) =>
    ["auto-eval", "runs", policyId, status ?? null, page ?? 1] as const,
  run: (id: string) => ["auto-eval", "run", id] as const,
  cost: (policyId: string, from: string, to: string) =>
    ["auto-eval", "cost", policyId, from, to] as const,
};

// ─────────────────────────────────────────────────────────────────────
// 정책 목록 / 단건 조회
// ─────────────────────────────────────────────────────────────────────

export interface AutoEvalPolicyListOptions {
  status?: PolicyStatus;
  page?: number;
  pageSize?: number;
}

export function useAutoEvalPolicyList(
  projectId: string,
  options: AutoEvalPolicyListOptions = {},
): UseQueryResult<AutoEvalPolicyListResponse> {
  const { status, page = 1, pageSize = 20 } = options;
  return useQuery<AutoEvalPolicyListResponse>({
    queryKey: autoEvalKeys.policies(projectId, status, page),
    queryFn: async () => {
      if (config.useMock) {
        const filtered = mockPolicies.filter((p) => {
          if (projectId && p.project_id !== projectId) return false;
          if (status && p.status !== status) return false;
          return true;
        });
        const start = (page - 1) * pageSize;
        const items = filtered.slice(start, start + pageSize);
        return {
          items,
          total: filtered.length,
          page,
          page_size: pageSize,
        };
      }
      return apiRequest<AutoEvalPolicyListResponse>("/auto-eval/policies", {
        query: {
          project_id: projectId,
          status,
          page,
          page_size: pageSize,
        },
      });
    },
    enabled: !!projectId,
  });
}

export function useAutoEvalPolicy(
  policyId: string | null | undefined,
): UseQueryResult<AutoEvalPolicy> {
  return useQuery<AutoEvalPolicy>({
    queryKey: autoEvalKeys.policy(policyId ?? ""),
    queryFn: async () => {
      if (!policyId) throw new Error("policyId required");
      if (config.useMock) {
        const found = mockPolicies.find((p) => p.id === policyId);
        if (!found) throw new Error(`mock policy not found: ${policyId}`);
        return found;
      }
      return apiRequest<AutoEvalPolicy>(
        `/auto-eval/policies/${encodeURIComponent(policyId)}`,
      );
    },
    enabled: !!policyId,
  });
}

// ─────────────────────────────────────────────────────────────────────
// 정책 생성 / 수정 / 삭제
// ─────────────────────────────────────────────────────────────────────

export interface CreateAutoEvalPolicyInput {
  payload: AutoEvalPolicyCreate;
  idempotencyKey?: string;
}

export function useCreateAutoEvalPolicy(): UseMutationResult<
  AutoEvalPolicy,
  Error,
  CreateAutoEvalPolicyInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ payload, idempotencyKey }) => {
      if (config.useMock) {
        const now = new Date().toISOString();
        const created: AutoEvalPolicy = {
          id: `policy_${Date.now()}`,
          name: payload.name,
          description: payload.description,
          project_id: payload.project_id,
          trace_filter: payload.trace_filter,
          expected_dataset_name: payload.expected_dataset_name,
          evaluators: payload.evaluators,
          schedule: payload.schedule,
          alert_thresholds: payload.alert_thresholds ?? [],
          notification_targets: payload.notification_targets ?? [],
          daily_cost_limit_usd: payload.daily_cost_limit_usd,
          status: payload.status ?? "active",
          owner: "user_1",
          created_at: now,
          updated_at: now,
        };
        return created;
      }
      return apiRequest<AutoEvalPolicy>("/auto-eval/policies", {
        method: "POST",
        body: payload,
        idempotencyKey,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: autoEvalKeys.all });
    },
  });
}

export interface UpdateAutoEvalPolicyInput {
  policyId: string;
  payload: AutoEvalPolicyUpdate;
  ifMatch?: string;
}

export function useUpdateAutoEvalPolicy(): UseMutationResult<
  AutoEvalPolicy,
  Error,
  UpdateAutoEvalPolicyInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ policyId, payload, ifMatch }) => {
      if (config.useMock) {
        const found = mockPolicies.find((p) => p.id === policyId);
        if (!found) throw new Error(`mock policy not found: ${policyId}`);
        const updated: AutoEvalPolicy = {
          ...found,
          ...payload,
          alert_thresholds:
            payload.alert_thresholds ?? found.alert_thresholds,
          notification_targets:
            payload.notification_targets ?? found.notification_targets,
          updated_at: new Date().toISOString(),
        };
        return updated;
      }
      return apiRequest<AutoEvalPolicy>(
        `/auto-eval/policies/${encodeURIComponent(policyId)}`,
        { method: "PATCH", body: payload, ifMatch },
      );
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: autoEvalKeys.policy(vars.policyId) });
      qc.invalidateQueries({ queryKey: autoEvalKeys.all });
    },
  });
}

export function useDeleteAutoEvalPolicy(): UseMutationResult<
  void,
  Error,
  { policyId: string; ifMatch?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ policyId, ifMatch }) => {
      if (config.useMock) return;
      await apiRequest(
        `/auto-eval/policies/${encodeURIComponent(policyId)}`,
        { method: "DELETE", ifMatch },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: autoEvalKeys.all });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────
// 상태 전환 / 즉시 실행
// ─────────────────────────────────────────────────────────────────────

function buildPausedMock(policyId: string): AutoEvalPolicy {
  const found = mockPolicies.find((p) => p.id === policyId);
  if (!found) throw new Error(`mock policy not found: ${policyId}`);
  return { ...found, status: "paused", updated_at: new Date().toISOString() };
}

function buildResumedMock(policyId: string): AutoEvalPolicy {
  const found = mockPolicies.find((p) => p.id === policyId);
  if (!found) throw new Error(`mock policy not found: ${policyId}`);
  return { ...found, status: "active", updated_at: new Date().toISOString() };
}

export function usePausePolicy(): UseMutationResult<
  AutoEvalPolicy,
  Error,
  string
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (policyId) => {
      if (config.useMock) return buildPausedMock(policyId);
      return apiRequest<AutoEvalPolicy>(
        `/auto-eval/policies/${encodeURIComponent(policyId)}/pause`,
        { method: "POST" },
      );
    },
    onSuccess: (_data, policyId) => {
      qc.invalidateQueries({ queryKey: autoEvalKeys.policy(policyId) });
      qc.invalidateQueries({ queryKey: autoEvalKeys.all });
    },
  });
}

export function useResumePolicy(): UseMutationResult<
  AutoEvalPolicy,
  Error,
  string
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (policyId) => {
      if (config.useMock) return buildResumedMock(policyId);
      return apiRequest<AutoEvalPolicy>(
        `/auto-eval/policies/${encodeURIComponent(policyId)}/resume`,
        { method: "POST" },
      );
    },
    onSuccess: (_data, policyId) => {
      qc.invalidateQueries({ queryKey: autoEvalKeys.policy(policyId) });
      qc.invalidateQueries({ queryKey: autoEvalKeys.all });
    },
  });
}

export function useRunPolicyNow(): UseMutationResult<
  AutoEvalRun,
  Error,
  string
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (policyId) => {
      if (config.useMock) {
        const now = new Date().toISOString();
        return {
          id: `run_manual_${Date.now()}`,
          policy_id: policyId,
          started_at: now,
          status: "running" as AutoEvalRunStatus,
          traces_evaluated: 0,
          traces_total: 0,
          cost_usd: 0,
          scores_by_evaluator: {},
          triggered_alerts: [],
          review_items_created: 0,
        } satisfies AutoEvalRun;
      }
      return apiRequest<AutoEvalRun>(
        `/auto-eval/policies/${encodeURIComponent(policyId)}/run-now`,
        { method: "POST" },
      );
    },
    onSuccess: (_data, policyId) => {
      qc.invalidateQueries({ queryKey: autoEvalKeys.policy(policyId) });
      qc.invalidateQueries({ queryKey: autoEvalKeys.all });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────
// Run 목록 / 단건 / Cost
// ─────────────────────────────────────────────────────────────────────

export interface AutoEvalRunListOptions {
  status?: AutoEvalRunStatus;
  page?: number;
  pageSize?: number;
}

export function useAutoEvalRunList(
  policyId: string,
  options: AutoEvalRunListOptions = {},
): UseQueryResult<AutoEvalRunListResponse> {
  const { status, page = 1, pageSize = 20 } = options;
  return useQuery<AutoEvalRunListResponse>({
    queryKey: autoEvalKeys.runs(policyId, status, page),
    queryFn: async () => {
      if (config.useMock) {
        const filtered = mockRuns
          .filter((r) => r.policy_id === policyId)
          .filter((r) => (status ? r.status === status : true))
          .sort((a, b) => b.started_at.localeCompare(a.started_at));
        const start = (page - 1) * pageSize;
        return {
          items: filtered.slice(start, start + pageSize),
          total: filtered.length,
          page,
          page_size: pageSize,
        };
      }
      return apiRequest<AutoEvalRunListResponse>("/auto-eval/runs", {
        query: {
          policy_id: policyId,
          status,
          page,
          page_size: pageSize,
        },
      });
    },
    enabled: !!policyId,
  });
}

export function useAutoEvalRun(
  runId: string | null | undefined,
): UseQueryResult<AutoEvalRun> {
  return useQuery<AutoEvalRun>({
    queryKey: autoEvalKeys.run(runId ?? ""),
    queryFn: async () => {
      if (!runId) throw new Error("runId required");
      if (config.useMock) {
        const found = mockRuns.find((r) => r.id === runId);
        if (!found) throw new Error(`mock run not found: ${runId}`);
        return found;
      }
      return apiRequest<AutoEvalRun>(
        `/auto-eval/runs/${encodeURIComponent(runId)}`,
      );
    },
    enabled: !!runId,
  });
}

export function useCostUsage(
  policyId: string,
  fromDate: string,
  toDate: string,
): UseQueryResult<CostUsage> {
  return useQuery<CostUsage>({
    queryKey: autoEvalKeys.cost(policyId, fromDate, toDate),
    queryFn: async () => {
      if (config.useMock) {
        return buildMockCostUsage(policyId, fromDate, toDate);
      }
      return apiRequest<CostUsage>(
        `/auto-eval/policies/${encodeURIComponent(policyId)}/cost-usage`,
        { query: { from_date: fromDate, to_date: toDate } },
      );
    },
    enabled: !!policyId && !!fromDate && !!toDate,
  });
}
