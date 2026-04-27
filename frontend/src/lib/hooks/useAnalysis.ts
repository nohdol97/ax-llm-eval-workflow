/**
 * 비교/분석 도메인 React Query 훅.
 *
 * 참조: docs/API_DESIGN.md §5
 */
import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { apiRequest } from "../api";
import { config } from "../config";
import type {
  CompareItemsRequest,
  CompareItemsResponse,
  CompareRequest,
  CompareResponse,
  MultiRunDistributionResponse,
  ScoreDistributionResponse,
} from "../types/api";

export const analysisKeys = {
  all: ["analysis"] as const,
  compare: (projectId: string, runs: string[]) =>
    ["analysis", "compare", projectId, [...runs].sort().join(",")] as const,
  scoreDist: (projectId: string, runs: string[], scoreName: string, bins: number) =>
    [
      "analysis",
      "score-dist",
      projectId,
      [...runs].sort().join(","),
      scoreName,
      bins,
    ] as const,
  latencyDist: (projectId: string, runs: string[], bins: number) =>
    [
      "analysis",
      "latency-dist",
      projectId,
      [...runs].sort().join(","),
      bins,
    ] as const,
  costDist: (projectId: string, runs: string[], bins: number) =>
    [
      "analysis",
      "cost-dist",
      projectId,
      [...runs].sort().join(","),
      bins,
    ] as const,
};

function mockCompareResponse(runs: string[]): CompareResponse {
  return {
    comparison: runs.map((run, i) => ({
      run_name: run,
      model: i % 2 === 0 ? "azure/gpt-4o" : "google/gemini-2.5-pro",
      prompt_version: 4,
      metrics: {
        sample_count: 100,
        avg_latency_ms: 1000 + i * 200,
        p50_latency_ms: 950 + i * 150,
        p90_latency_ms: 1800 + i * 200,
        p99_latency_ms: 3200,
        total_cost_usd: 1.23 + i * 0.5,
      },
      scores: {
        exact_match: { avg: 0.87 - i * 0.05, min: 0, max: 1, stddev: 0.2 },
      },
    })),
  };
}

export function useCompareRuns(
  payload: CompareRequest | null,
): UseQueryResult<CompareResponse> {
  return useQuery({
    queryKey: analysisKeys.compare(
      payload?.project_id ?? "",
      payload?.run_names ?? [],
    ),
    queryFn: async () => {
      if (!payload) throw new Error("payload required");
      if (config.useMock) {
        return mockCompareResponse(payload.run_names);
      }
      return apiRequest<CompareResponse>("/analysis/compare", {
        method: "POST",
        body: payload,
      });
    },
    enabled: !!payload && payload.run_names.length > 0,
  });
}

export function useCompareItems(): UseMutationResult<
  CompareItemsResponse,
  Error,
  CompareItemsRequest
> {
  return useMutation({
    mutationFn: async (payload) => {
      if (config.useMock) {
        return {
          items: [],
          total: 0,
          page: payload.page ?? 1,
          page_size: payload.page_size ?? 20,
        };
      }
      return apiRequest<CompareItemsResponse>(
        "/analysis/compare/items",
        { method: "POST", body: payload },
      );
    },
  });
}

export function useScoreDistribution(
  projectId: string,
  runNames: string[],
  scoreName: string,
  bins = 10,
): UseQueryResult<ScoreDistributionResponse> {
  return useQuery({
    queryKey: analysisKeys.scoreDist(projectId, runNames, scoreName, bins),
    queryFn: async () => {
      if (config.useMock) {
        return {
          distribution: Array.from({ length: bins }, (_, i) => ({
            bin_start: i / bins,
            bin_end: (i + 1) / bins,
            count: Math.floor(Math.random() * 20),
          })),
          statistics: {
            mean: 0.85,
            median: 0.9,
            stddev: 0.15,
            min: 0,
            max: 1,
          },
        };
      }
      return apiRequest<ScoreDistributionResponse>(
        "/analysis/scores/distribution",
        {
          query: {
            project_id: projectId,
            run_names: runNames.join(","),
            score_name: scoreName,
            bins,
          },
        },
      );
    },
    enabled: !!projectId && runNames.length > 0 && !!scoreName,
  });
}

export function useLatencyDistribution(
  projectId: string,
  runNames: string[],
  bins = 10,
): UseQueryResult<MultiRunDistributionResponse> {
  return useQuery({
    queryKey: analysisKeys.latencyDist(projectId, runNames, bins),
    queryFn: async () => {
      if (config.useMock) {
        const runs: MultiRunDistributionResponse["runs"] = {};
        for (const name of runNames) {
          runs[name] = {
            distribution: [],
            statistics: { p50: 950, p90: 1800, p99: 3200 },
          };
        }
        return { runs };
      }
      return apiRequest<MultiRunDistributionResponse>(
        "/analysis/latency/distribution",
        {
          query: {
            project_id: projectId,
            run_names: runNames.join(","),
            bins,
          },
        },
      );
    },
    enabled: !!projectId && runNames.length > 0,
  });
}

export function useCostDistribution(
  projectId: string,
  runNames: string[],
  bins = 10,
): UseQueryResult<MultiRunDistributionResponse> {
  return useQuery({
    queryKey: analysisKeys.costDist(projectId, runNames, bins),
    queryFn: async () => {
      if (config.useMock) {
        const runs: MultiRunDistributionResponse["runs"] = {};
        for (const name of runNames) {
          runs[name] = {
            distribution: [],
            statistics: { p50: 0.01, p90: 0.05, p99: 0.1 },
          };
        }
        return { runs };
      }
      return apiRequest<MultiRunDistributionResponse>(
        "/analysis/cost/distribution",
        {
          query: {
            project_id: projectId,
            run_names: runNames.join(","),
            bins,
          },
        },
      );
    },
    enabled: !!projectId && runNames.length > 0,
  });
}
