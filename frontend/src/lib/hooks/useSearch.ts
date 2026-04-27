/**
 * 검색 / 헬스 도메인 React Query 훅.
 *
 * 참조: docs/API_DESIGN.md §1.5, §10
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { apiRequest } from "../api";
import { config } from "../config";
import {
  datasets as mockDatasets,
  experiments as mockExperiments,
  prompts as mockPrompts,
} from "../mock/data";
import type { HealthResponse, SearchResponse } from "../types/api";

export const searchKeys = {
  all: ["search"] as const,
  query: (projectId: string, q: string, type?: string) =>
    ["search", projectId, q, type ?? "all"] as const,
};

export const healthKeys = {
  status: () => ["health"] as const,
};

function mockSearch(q: string, type?: string): SearchResponse {
  const lower = q.toLowerCase();
  const promptResults = mockPrompts
    .filter((p) => p.name.toLowerCase().includes(lower))
    .map((p) => ({
      type: "prompt" as const,
      id: p.id,
      name: p.name,
      score: 1.0,
      match_context: p.description,
    }));
  const datasetResults = mockDatasets
    .filter((d) => d.name.toLowerCase().includes(lower))
    .map((d) => ({
      type: "dataset" as const,
      id: d.id,
      name: d.name,
      score: 0.9,
    }));
  const experimentResults = mockExperiments
    .filter((e) => e.name.toLowerCase().includes(lower))
    .map((e) => ({
      type: "experiment" as const,
      id: e.id,
      name: e.name,
      score: 0.8,
    }));
  const results: SearchResponse["results"] = {};
  if (!type || type === "prompt") results.prompts = promptResults;
  if (!type || type === "dataset") results.datasets = datasetResults;
  if (!type || type === "experiment")
    results.experiments = experimentResults;
  return {
    query: q,
    results,
    total:
      (results.prompts?.length ?? 0) +
      (results.datasets?.length ?? 0) +
      (results.experiments?.length ?? 0),
  };
}

export function useGlobalSearch(
  projectId: string,
  q: string,
  type?: "prompt" | "dataset" | "experiment",
): UseQueryResult<SearchResponse> {
  return useQuery({
    queryKey: searchKeys.query(projectId, q, type),
    queryFn: async () => {
      if (config.useMock) {
        return mockSearch(q, type);
      }
      return apiRequest<SearchResponse>("/search", {
        query: { project_id: projectId, q, type },
      });
    },
    enabled: !!projectId && q.trim().length > 0,
    staleTime: 10_000,
  });
}

/** @alias useGlobalSearch — 페이지 에이전트 호환 */
export const useSearch = useGlobalSearch;

export function useHealth(): UseQueryResult<HealthResponse> {
  return useQuery({
    queryKey: healthKeys.status(),
    queryFn: async () => {
      if (config.useMock) {
        return {
          status: "ok",
          version: "1.0.0-mock",
          environment: "mock",
          services: {
            langfuse: { status: "ok", checked_at: new Date().toISOString() },
            litellm: { status: "ok", checked_at: new Date().toISOString() },
            clickhouse: { status: "ok", checked_at: new Date().toISOString() },
            redis: { status: "ok", checked_at: new Date().toISOString() },
          },
        } as HealthResponse;
      }
      return apiRequest<HealthResponse>("/health", { skipAuth: true });
    },
    refetchInterval: 60_000,
  });
}
