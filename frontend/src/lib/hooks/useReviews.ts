/**
 * Phase 8-C: Review Queue 도메인 React Query 훅.
 *
 * Backend ``/api/v1/reviews/*`` 엔드포인트에 대한 클라이언트 어댑터.
 *
 * - {@link useReviewItemList}: ``GET /reviews/items``
 * - {@link useReviewItem}: ``GET /reviews/items/{id}``
 * - {@link useCreateReviewItem}: ``POST /reviews/items``
 * - {@link useClaimReviewItem} / {@link useReleaseReviewItem}: 상태 전환
 * - {@link useResolveReviewItem}: ``POST /reviews/items/{id}/resolve``
 * - {@link useDeleteReviewItem}: ``DELETE /reviews/items/{id}`` (admin)
 * - {@link useReviewSummary}: ``GET /reviews/stats/summary``
 * - {@link useReviewerStats}: ``GET /reviews/stats/reviewer/{user_id}``
 * - {@link useDisagreementStats}: ``GET /reviews/stats/disagreement``
 * - {@link useReportTrace}: ``POST /reviews/report``
 *
 * mock 모드(``config.useMock=true``)에서는 in-memory 큐를 사용한다.
 *
 * 참조: docs/AGENT_EVAL.md §15~§18
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
import type {
  EvaluatorDisagreementResponse,
  ReviewDecision,
  ReviewItem,
  ReviewItemCreate,
  ReviewItemListResponse,
  ReviewItemResolve,
  ReviewItemType,
  ReviewQueueSummary,
  ReviewReport,
  ReviewSeverity,
  ReviewStatus,
  ReviewerStats,
} from "../types/api";

export const reviewKeys = {
  all: ["reviews"] as const,
  list: (filters: ReviewListFilters) =>
    [
      "reviews",
      "list",
      filters.projectId ?? null,
      filters.status ?? null,
      filters.type ?? null,
      filters.severity ?? null,
      filters.assignedTo ?? null,
      filters.page ?? 1,
    ] as const,
  item: (id: string) => ["reviews", "item", id] as const,
  summary: (projectId?: string) =>
    ["reviews", "summary", projectId ?? null] as const,
  reviewerStats: (userId: string) =>
    ["reviews", "reviewer-stats", userId] as const,
  disagreement: () => ["reviews", "disagreement"] as const,
};

// ─────────────────────────────────────────────────────────────────────
// Mock store (config.useMock=true 시 사용)
// ─────────────────────────────────────────────────────────────────────
const mockStore: { items: ReviewItem[] } = { items: [] };

function nowIso(): string {
  return new Date().toISOString();
}

function newId(): string {
  return `review_${Math.random().toString(36).slice(2, 14)}`;
}

// ─────────────────────────────────────────────────────────────────────
// 목록 / 단건
// ─────────────────────────────────────────────────────────────────────
export interface ReviewListFilters {
  projectId?: string;
  status?: ReviewStatus;
  type?: ReviewItemType;
  severity?: ReviewSeverity;
  assignedTo?: string;
  page?: number;
  pageSize?: number;
}

export function useReviewItemList(
  filters: ReviewListFilters = {},
): UseQueryResult<ReviewItemListResponse> {
  const { page = 1, pageSize = 20 } = filters;
  return useQuery<ReviewItemListResponse>({
    queryKey: reviewKeys.list(filters),
    queryFn: async () => {
      if (config.useMock) {
        const f = mockStore.items.filter((it) => {
          if (filters.projectId && it.project_id !== filters.projectId)
            return false;
          if (filters.status && it.status !== filters.status) return false;
          if (filters.type && it.type !== filters.type) return false;
          if (filters.severity && it.severity !== filters.severity)
            return false;
          if (filters.assignedTo && it.assigned_to !== filters.assignedTo)
            return false;
          return true;
        });
        const start = (page - 1) * pageSize;
        return {
          items: f.slice(start, start + pageSize),
          total: f.length,
          page,
          page_size: pageSize,
        };
      }
      return apiRequest<ReviewItemListResponse>("/reviews/items", {
        query: {
          project_id: filters.projectId,
          status: filters.status,
          type: filters.type,
          severity: filters.severity,
          assigned_to: filters.assignedTo,
          page,
          page_size: pageSize,
        },
      });
    },
  });
}

export function useReviewItem(
  itemId: string | null | undefined,
): UseQueryResult<ReviewItem> {
  return useQuery<ReviewItem>({
    queryKey: reviewKeys.item(itemId ?? ""),
    queryFn: async () => {
      if (!itemId) throw new Error("itemId required");
      if (config.useMock) {
        const found = mockStore.items.find((it) => it.id === itemId);
        if (!found) throw new Error(`review not found: ${itemId}`);
        return found;
      }
      return apiRequest<ReviewItem>(
        `/reviews/items/${encodeURIComponent(itemId)}`,
      );
    },
    enabled: !!itemId,
  });
}

// ─────────────────────────────────────────────────────────────────────
// 생성 (수동 추가)
// ─────────────────────────────────────────────────────────────────────
export function useCreateReviewItem(): UseMutationResult<
  ReviewItem,
  Error,
  ReviewItemCreate
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      if (config.useMock) {
        const item: ReviewItem = {
          id: newId(),
          type: "manual_addition",
          severity: payload.severity ?? "medium",
          subject_type: payload.subject_type ?? "trace",
          subject_id: payload.subject_id,
          project_id: payload.project_id,
          reason: payload.reason ?? "manual_addition",
          reason_detail: payload.reason_detail ?? {},
          automatic_scores: payload.automatic_scores ?? {},
          status: "open",
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        mockStore.items.unshift(item);
        return item;
      }
      return apiRequest<ReviewItem>("/reviews/items", {
        method: "POST",
        body: payload,
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: reviewKeys.all }),
  });
}

// ─────────────────────────────────────────────────────────────────────
// claim / release
// ─────────────────────────────────────────────────────────────────────
export function useClaimReviewItem(): UseMutationResult<
  ReviewItem,
  Error,
  { itemId: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ itemId }) => {
      if (config.useMock) {
        const item = mockStore.items.find((it) => it.id === itemId);
        if (!item) throw new Error(`not found: ${itemId}`);
        item.status = "in_review";
        item.assigned_to = "current_user";
        item.assigned_at = nowIso();
        item.updated_at = nowIso();
        return item;
      }
      return apiRequest<ReviewItem>(
        `/reviews/items/${encodeURIComponent(itemId)}/claim`,
        { method: "PATCH" },
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: reviewKeys.all }),
  });
}

export function useReleaseReviewItem(): UseMutationResult<
  ReviewItem,
  Error,
  { itemId: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ itemId }) => {
      if (config.useMock) {
        const item = mockStore.items.find((it) => it.id === itemId);
        if (!item) throw new Error(`not found: ${itemId}`);
        item.status = "open";
        item.assigned_to = null;
        item.assigned_at = null;
        item.updated_at = nowIso();
        return item;
      }
      return apiRequest<ReviewItem>(
        `/reviews/items/${encodeURIComponent(itemId)}/release`,
        { method: "PATCH" },
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: reviewKeys.all }),
  });
}

// ─────────────────────────────────────────────────────────────────────
// resolve
// ─────────────────────────────────────────────────────────────────────
export interface ResolveInput {
  itemId: string;
  payload: ReviewItemResolve;
  ifMatch?: string;
}

export function useResolveReviewItem(): UseMutationResult<
  ReviewItem,
  Error,
  ResolveInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ itemId, payload, ifMatch }) => {
      if (config.useMock) {
        const item = mockStore.items.find((it) => it.id === itemId);
        if (!item) throw new Error(`not found: ${itemId}`);
        const targetStatus: ReviewItem["status"] =
          payload.decision === "dismiss" ? "dismissed" : "resolved";
        item.status = targetStatus;
        item.decision = payload.decision;
        item.reviewer_score = payload.reviewer_score ?? null;
        item.reviewer_comment = payload.reviewer_comment ?? null;
        item.expected_output = payload.expected_output;
        item.resolved_at = nowIso();
        item.resolved_by = "current_user";
        item.updated_at = nowIso();
        return item;
      }
      return apiRequest<ReviewItem>(
        `/reviews/items/${encodeURIComponent(itemId)}/resolve`,
        { method: "POST", body: payload, ifMatch },
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: reviewKeys.all }),
  });
}

// ─────────────────────────────────────────────────────────────────────
// admin 삭제
// ─────────────────────────────────────────────────────────────────────
export function useDeleteReviewItem(): UseMutationResult<
  void,
  Error,
  { itemId: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ itemId }) => {
      if (config.useMock) {
        const idx = mockStore.items.findIndex((it) => it.id === itemId);
        if (idx >= 0) mockStore.items.splice(idx, 1);
        return;
      }
      await apiRequest<void>(
        `/reviews/items/${encodeURIComponent(itemId)}`,
        { method: "DELETE" },
      );
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: reviewKeys.all }),
  });
}

// ─────────────────────────────────────────────────────────────────────
// 통계
// ─────────────────────────────────────────────────────────────────────
export function useReviewSummary(
  projectId?: string,
): UseQueryResult<ReviewQueueSummary> {
  return useQuery<ReviewQueueSummary>({
    queryKey: reviewKeys.summary(projectId),
    queryFn: async () => {
      if (config.useMock) {
        const items = projectId
          ? mockStore.items.filter((it) => it.project_id === projectId)
          : mockStore.items;
        const today = new Date().toISOString().slice(0, 10);
        return {
          open: items.filter((it) => it.status === "open").length,
          in_review: items.filter((it) => it.status === "in_review").length,
          resolved_today: items.filter(
            (it) =>
              it.status === "resolved" &&
              (it.resolved_at ?? "").startsWith(today),
          ).length,
          dismissed_today: items.filter(
            (it) =>
              it.status === "dismissed" &&
              (it.resolved_at ?? "").startsWith(today),
          ).length,
          avg_resolution_time_min: null,
        };
      }
      return apiRequest<ReviewQueueSummary>("/reviews/stats/summary", {
        query: { project_id: projectId },
      });
    },
  });
}

export function useReviewerStats(
  userId: string | null | undefined,
): UseQueryResult<ReviewerStats> {
  return useQuery<ReviewerStats>({
    queryKey: reviewKeys.reviewerStats(userId ?? ""),
    queryFn: async () => {
      if (!userId) throw new Error("userId required");
      if (config.useMock) {
        const decisions: Record<string, number> = {};
        let resolved = 0;
        for (const it of mockStore.items) {
          if (it.resolved_by === userId && it.decision) {
            decisions[it.decision] = (decisions[it.decision] ?? 0) + 1;
            resolved += 1;
          }
        }
        return {
          user_id: userId,
          open_count: 0,
          in_review_count: mockStore.items.filter(
            (it) => it.assigned_to === userId,
          ).length,
          resolved_today: resolved,
          avg_resolution_time_min: null,
          decisions_breakdown: decisions,
        };
      }
      return apiRequest<ReviewerStats>(
        `/reviews/stats/reviewer/${encodeURIComponent(userId)}`,
      );
    },
    enabled: !!userId,
  });
}

export function useDisagreementStats(): UseQueryResult<EvaluatorDisagreementResponse> {
  return useQuery<EvaluatorDisagreementResponse>({
    queryKey: reviewKeys.disagreement(),
    queryFn: async () => {
      if (config.useMock) return { items: [] };
      return apiRequest<EvaluatorDisagreementResponse>(
        "/reviews/stats/disagreement",
      );
    },
  });
}

// ─────────────────────────────────────────────────────────────────────
// 사용자 신고
// ─────────────────────────────────────────────────────────────────────
export function useReportTrace(): UseMutationResult<
  ReviewItem,
  Error,
  ReviewReport
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      if (config.useMock) {
        const item: ReviewItem = {
          id: newId(),
          type: "user_report",
          severity: payload.severity ?? "medium",
          subject_type: payload.subject_type ?? "trace",
          subject_id: payload.trace_id,
          project_id: payload.project_id,
          reason: "user_report",
          reason_detail: { reason_text: payload.reason },
          automatic_scores: {},
          status: "open",
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        mockStore.items.unshift(item);
        return item;
      }
      return apiRequest<ReviewItem>("/reviews/report", {
        method: "POST",
        body: payload,
      });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: reviewKeys.all }),
  });
}

// ─────────────────────────────────────────────────────────────────────
// 헬퍼 — UI에서 결정 라벨/색상
// ─────────────────────────────────────────────────────────────────────
export function decisionLabel(decision: ReviewDecision): string {
  switch (decision) {
    case "approve":
      return "Approve (자동 점수 확정)";
    case "override":
      return "Override (수동 점수)";
    case "dismiss":
      return "Dismiss (false positive)";
    case "add_to_dataset":
      return "Add to Dataset (골든셋 추가)";
  }
}

export function severityColor(severity: ReviewSeverity): string {
  switch (severity) {
    case "high":
      return "text-red-600";
    case "medium":
      return "text-yellow-600";
    case "low":
      return "text-green-600";
  }
}
