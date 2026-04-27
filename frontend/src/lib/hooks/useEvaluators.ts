/**
 * 평가 함수 도메인 React Query 훅.
 *
 * 참조: docs/API_DESIGN.md §8, §14
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
import { evaluators as mockEvaluators } from "../mock/data";
import type {
  ApprovedEvaluatorListResponse,
  BuiltInEvaluatorListResponse,
  ScoreConfigListResponse,
  Submission,
  SubmissionCreate,
  SubmissionListResponse,
  SubmissionRejectRequest,
  SubmissionStatus,
  ValidateRequest,
  ValidateResponse,
} from "../types/api";

export const evaluatorKeys = {
  all: ["evaluators"] as const,
  builtIn: () => ["evaluators", "built-in"] as const,
  approved: (projectId: string) =>
    ["evaluators", "approved", projectId] as const,
  submissions: (projectId: string, status?: SubmissionStatus) =>
    ["evaluators", "submissions", projectId, status ?? "all"] as const,
  scoreConfigs: (projectId: string) =>
    ["evaluators", "score-configs", projectId] as const,
};

export function useBuiltInEvaluators(): UseQueryResult<BuiltInEvaluatorListResponse> {
  return useQuery({
    queryKey: evaluatorKeys.builtIn(),
    queryFn: async () => {
      if (config.useMock) {
        return {
          evaluators: mockEvaluators
            .filter((e) => e.type === "builtin")
            .map((e) => ({
              name: e.id,
              description: e.description,
              return_type:
                e.range === "binary"
                  ? "binary"
                  : e.range === "0-1"
                    ? "float"
                    : "float",
            })),
        } as BuiltInEvaluatorListResponse;
      }
      return apiRequest<BuiltInEvaluatorListResponse>(
        "/evaluators/built-in",
      );
    },
    staleTime: 5 * 60_000,
  });
}

export function useApprovedEvaluators(
  projectId: string,
): UseQueryResult<ApprovedEvaluatorListResponse> {
  return useQuery({
    queryKey: evaluatorKeys.approved(projectId),
    queryFn: async () => {
      if (config.useMock) {
        return {
          evaluators: mockEvaluators
            .filter((e) => e.status === "approved" && e.type === "custom")
            .map((e) => ({
              submission_id: e.id,
              name: e.name,
              description: e.description,
              version: 1,
              approved_at: e.approvedAt ?? new Date().toISOString(),
              approver: e.approvedBy ?? "admin",
            })),
        };
      }
      return apiRequest<ApprovedEvaluatorListResponse>(
        "/evaluators/approved",
        { query: { project_id: projectId } },
      );
    },
    enabled: !!projectId,
  });
}

export function useEvaluatorSubmissions(
  projectId: string,
  status?: SubmissionStatus,
): UseQueryResult<SubmissionListResponse> {
  return useQuery({
    queryKey: evaluatorKeys.submissions(projectId, status),
    queryFn: async () => {
      if (config.useMock) {
        return {
          submissions: mockEvaluators
            .filter((e) => e.type === "custom")
            .filter((e) => !status || e.status === status)
            .map<Submission>((e) => ({
              submission_id: e.id,
              name: e.name,
              description: e.description,
              status: e.status as SubmissionStatus,
              submitted_by: e.submittedBy,
              submitted_at: e.submittedAt,
              approved_by: e.approvedBy,
              approved_at: e.approvedAt,
            })),
        };
      }
      return apiRequest<SubmissionListResponse>(
        "/evaluators/submissions",
        { query: { project_id: projectId, status } },
      );
    },
    enabled: !!projectId,
  });
}

export function useSubmitEvaluator(): UseMutationResult<
  Submission,
  Error,
  { payload: SubmissionCreate; idempotencyKey?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ payload, idempotencyKey }) => {
      if (config.useMock) {
        return {
          submission_id: `mock-sub-${Date.now()}`,
          name: payload.name,
          description: payload.description,
          code: payload.code,
          status: "pending" as SubmissionStatus,
          submitted_at: new Date().toISOString(),
        };
      }
      return apiRequest<Submission>("/evaluators/submissions", {
        method: "POST",
        body: payload,
        idempotencyKey,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: evaluatorKeys.all });
    },
  });
}

export function useApproveEvaluator(): UseMutationResult<
  Submission,
  Error,
  { submissionId: string; ifMatch?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ submissionId, ifMatch }) => {
      if (config.useMock) {
        return {
          submission_id: submissionId,
          name: "mock",
          description: "",
          status: "approved" as SubmissionStatus,
        };
      }
      return apiRequest<Submission>(
        `/evaluators/submissions/${encodeURIComponent(submissionId)}/approve`,
        { method: "POST", ifMatch },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: evaluatorKeys.all });
    },
  });
}

export function useRejectEvaluator(): UseMutationResult<
  Submission,
  Error,
  { submissionId: string; payload: SubmissionRejectRequest; ifMatch?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ submissionId, payload, ifMatch }) => {
      if (config.useMock) {
        return {
          submission_id: submissionId,
          name: "mock",
          description: "",
          status: "rejected" as SubmissionStatus,
          rejection_reason: payload.reason,
        };
      }
      return apiRequest<Submission>(
        `/evaluators/submissions/${encodeURIComponent(submissionId)}/reject`,
        { method: "POST", body: payload, ifMatch },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: evaluatorKeys.all });
    },
  });
}

export function useDeprecateEvaluator(): UseMutationResult<
  Submission,
  Error,
  { submissionId: string; reason: string; ifMatch?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ submissionId, reason, ifMatch }) => {
      if (config.useMock) {
        return {
          submission_id: submissionId,
          name: "mock",
          description: "",
          status: "deprecated" as SubmissionStatus,
          reason,
        };
      }
      return apiRequest<Submission>(
        `/evaluators/submissions/${encodeURIComponent(submissionId)}/deprecate`,
        { method: "POST", body: { reason }, ifMatch },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: evaluatorKeys.all });
    },
  });
}

export function useValidateEvaluator(): UseMutationResult<
  ValidateResponse,
  Error,
  ValidateRequest
> {
  return useMutation({
    mutationFn: async (payload) => {
      if (config.useMock) {
        return {
          valid: true,
          test_results: payload.test_cases.map((_tc, i) => ({
            input_index: i,
            result: 1.0,
            error: null,
          })),
        };
      }
      return apiRequest<ValidateResponse>("/evaluators/validate", {
        method: "POST",
        body: payload,
      });
    },
  });
}

export function useScoreConfigs(
  projectId: string,
): UseQueryResult<ScoreConfigListResponse> {
  return useQuery({
    queryKey: evaluatorKeys.scoreConfigs(projectId),
    queryFn: async () => {
      if (config.useMock) {
        return {
          score_configs: [
            {
              name: "exact_match",
              data_type: "NUMERIC",
              min_value: 0,
              max_value: 1,
              source: "built_in",
              registered: true,
            },
          ],
        } as ScoreConfigListResponse;
      }
      return apiRequest<ScoreConfigListResponse>(
        "/evaluators/score-configs",
        { query: { project_id: projectId } },
      );
    },
    enabled: !!projectId,
  });
}
