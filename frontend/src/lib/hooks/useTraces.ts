/**
 * Phase 8-A: Trace 도메인 React Query 훅.
 *
 * - {@link useTraceSearch}: ``POST /api/v1/traces/search`` 호출 (페이지네이션, 메타만)
 * - {@link useTraceDetail}: ``GET /api/v1/traces/{id}`` (모든 observations 포함)
 *
 * mock 모드(``config.useMock=true``)에서는 ``mock/data.ts``의 ``traces``를 반환.
 *
 * 참조: docs/AGENT_EVAL.md §7.1
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiRequest } from "../api";
import { config } from "../config";
import { traces as mockTraces } from "../mock/data";
import type {
  TraceFilter,
  TraceSearchResponse,
  TraceSummary,
  TraceTree,
} from "../types/api";

export const traceKeys = {
  all: ["traces"] as const,
  search: (filter: TraceFilter | null, page: number, pageSize: number) =>
    ["traces", "search", filter, page, pageSize] as const,
  detail: (traceId: string | null, projectId: string) =>
    ["traces", "detail", traceId, projectId] as const,
};

function summaryFromTree(t: TraceTree): TraceSummary {
  return {
    id: t.id,
    name: t.name,
    user_id: t.user_id ?? null,
    session_id: t.session_id ?? null,
    tags: t.tags ?? [],
    total_cost_usd: t.total_cost_usd ?? 0,
    total_latency_ms: t.total_latency_ms ?? null,
    timestamp: t.timestamp,
    observation_count: t.observations?.length ?? 0,
  };
}

function applyMockFilter(
  filter: TraceFilter,
  pool: TraceTree[],
): TraceTree[] {
  return pool.filter((t) => {
    if (filter.project_id && t.project_id !== filter.project_id) return false;
    if (filter.name && t.name !== filter.name) return false;
    if (filter.tags && filter.tags.length > 0) {
      const traceTags = new Set(t.tags ?? []);
      const allMatch = filter.tags.every((tag) => traceTags.has(tag));
      if (!allMatch) return false;
    }
    if (filter.user_ids && filter.user_ids.length > 0) {
      if (!t.user_id || !filter.user_ids.includes(t.user_id)) return false;
    }
    if (filter.session_ids && filter.session_ids.length > 0) {
      if (!t.session_id || !filter.session_ids.includes(t.session_id))
        return false;
    }
    if (filter.from_timestamp && t.timestamp < filter.from_timestamp)
      return false;
    if (filter.to_timestamp && t.timestamp > filter.to_timestamp) return false;
    return true;
  });
}

/**
 * 트레이스 검색 — TraceFilter 기반.
 *
 * ``filter``가 ``null``이면 enabled=false로 호출되지 않는다.
 */
export function useTraceSearch(
  filter: TraceFilter | null,
  page: number = 1,
  pageSize: number = 20,
): UseQueryResult<TraceSearchResponse> {
  return useQuery<TraceSearchResponse>({
    queryKey: traceKeys.search(filter, page, pageSize),
    queryFn: async () => {
      if (!filter) throw new Error("trace filter required");
      if (config.useMock) {
        const matched = applyMockFilter(filter, mockTraces);
        // sample_size 적용
        const sliced =
          filter.sample_size != null
            ? matched.slice(0, filter.sample_size)
            : matched;
        const start = (page - 1) * pageSize;
        const items = sliced.slice(start, start + pageSize).map(summaryFromTree);
        return {
          items,
          total: sliced.length,
          page,
          page_size: pageSize,
        };
      }
      return apiRequest<TraceSearchResponse>("/traces/search", {
        method: "POST",
        body: {
          filter,
          page,
          page_size: pageSize,
          include_observations: false,
        },
      });
    },
    enabled: !!filter,
  });
}

/**
 * 트레이스 단건 조회 — observations 포함.
 *
 * ``traceId``가 ``null/undefined``이면 호출되지 않는다.
 */
export function useTraceDetail(
  traceId: string | null | undefined,
  projectId: string,
): UseQueryResult<TraceTree> {
  return useQuery<TraceTree>({
    queryKey: traceKeys.detail(traceId ?? null, projectId),
    queryFn: async () => {
      if (!traceId) throw new Error("traceId required");
      if (config.useMock) {
        const found = mockTraces.find((t) => t.id === traceId);
        if (!found) throw new Error(`mock trace not found: ${traceId}`);
        return found;
      }
      return apiRequest<TraceTree>(
        `/traces/${encodeURIComponent(traceId)}?project_id=${encodeURIComponent(projectId)}`,
      );
    },
    enabled: !!traceId,
  });
}
