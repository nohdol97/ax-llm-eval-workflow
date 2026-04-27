/**
 * 모델 도메인 React Query 훅 / 프로젝트 정보.
 *
 * 참조: docs/API_DESIGN.md §7, §9
 */
import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { apiRequest } from "../api";
import { config } from "../config";
import {
  models as mockModels,
  projects as mockProjects,
} from "../mock/data";
import type {
  ModelInfo,
  ModelListResponse,
  ProjectInfo,
  ProviderGroup,
} from "../types/api";

export const modelKeys = {
  all: ["models"] as const,
  list: () => ["models", "list"] as const,
  providers: () => ["models", "providers"] as const,
};

export const projectKeys = {
  all: ["projects"] as const,
  list: () => ["projects", "list"] as const,
};

function mockToModelInfo(): ModelInfo[] {
  return mockModels.map((m) => ({
    id: m.id,
    name: m.name,
    provider: m.provider,
    vision: m.vision,
    context_window: m.contextWindow,
    input_cost_per_k: m.inputCostPerK,
    output_cost_per_k: m.outputCostPerK,
  }));
}

function groupByProvider(models: ModelInfo[]): ProviderGroup[] {
  const map = new Map<string, ModelInfo[]>();
  for (const m of models) {
    const list = map.get(m.provider) ?? [];
    list.push(m);
    map.set(m.provider, list);
  }
  return Array.from(map.entries()).map(([id, list]) => ({
    id,
    name: id,
    models: list,
  }));
}

export function useModelList(): UseQueryResult<ModelListResponse> {
  return useQuery({
    queryKey: modelKeys.list(),
    queryFn: async () => {
      if (config.useMock) {
        return { models: mockToModelInfo() };
      }
      return apiRequest<ModelListResponse>("/models");
    },
    staleTime: 5 * 60_000,
  });
}

export function useModelProviders(): UseQueryResult<ProviderGroup[]> {
  return useQuery({
    queryKey: modelKeys.providers(),
    queryFn: async () => {
      if (config.useMock) {
        return groupByProvider(mockToModelInfo());
      }
      const res = await apiRequest<ModelListResponse>("/models");
      return groupByProvider(res.models);
    },
    staleTime: 5 * 60_000,
  });
}

export function useProjectList(): UseQueryResult<{ projects: ProjectInfo[] }> {
  return useQuery({
    queryKey: projectKeys.list(),
    queryFn: async () => {
      if (config.useMock) {
        return {
          projects: mockProjects.map((p) => ({
            id: p.id,
            name: p.name,
            description: p.description,
          })),
        };
      }
      return apiRequest<{ projects: ProjectInfo[] }>("/projects");
    },
  });
}

export function useSwitchProject(): UseMutationResult<
  { project_id: string; name: string },
  Error,
  string
> {
  return useMutation({
    mutationFn: async (projectId) => {
      if (config.useMock) {
        const p = mockProjects.find((x) => x.id === projectId);
        return { project_id: projectId, name: p?.name ?? projectId };
      }
      return apiRequest<{ project_id: string; name: string }>(
        "/projects/switch",
        { method: "POST", body: { project_id: projectId } },
      );
    },
  });
}
