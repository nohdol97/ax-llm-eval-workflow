/**
 * 프롬프트 도메인 React Query 훅.
 *
 * Mock 모드(config.useMock=true)에서는 src/lib/mock/data.ts의 더미 데이터를 반환한다.
 * 실 모드에서는 backend `/api/v1/prompts/*` 호출.
 *
 * 참조: docs/API_DESIGN.md §2
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
import { prompts as mockPrompts } from "../mock/data";
import type {
  PaginatedResponse,
  PromptCreate,
  PromptCreateResponse,
  PromptDetail,
  PromptLabelsPatch,
  PromptSummary,
  PromptVersionSummary,
} from "../types/api";

// ─────────────────────────────────────────────────────────────────────
// Query keys
// ─────────────────────────────────────────────────────────────────────

export const promptKeys = {
  all: ["prompts"] as const,
  list: (projectId: string, page: number) =>
    ["prompts", "list", projectId, page] as const,
  detail: (projectId: string, name: string, version?: number) =>
    ["prompts", "detail", projectId, name, version ?? "latest"] as const,
  versions: (projectId: string, name: string) =>
    ["prompts", "versions", projectId, name] as const,
};

// ─────────────────────────────────────────────────────────────────────
// Mock helpers
// ─────────────────────────────────────────────────────────────────────

function mockToSummary(): PromptSummary[] {
  return mockPrompts.map((p) => ({
    name: p.name,
    latest_version: p.latestVersion,
    labels: p.labels,
    tags: [],
    created_at: p.versions[0]?.createdAt ?? new Date().toISOString(),
  }));
}

function mockDetail(name: string, version?: number): PromptDetail | null {
  const p = mockPrompts.find((x) => x.name === name);
  if (!p) return null;
  const v = version
    ? p.versions.find((x) => x.version === version)
    : p.versions.find((x) => x.version === p.latestVersion);
  if (!v) return null;
  return {
    name: p.name,
    version: v.version,
    type: "text",
    prompt: v.body,
    config: v.systemPrompt ? { system_prompt: v.systemPrompt } : {},
    labels: p.labels,
    variables: v.variables,
    created_at: v.createdAt,
  };
}

// ─────────────────────────────────────────────────────────────────────
// Hooks
// ─────────────────────────────────────────────────────────────────────

export function usePromptList(
  projectId: string,
  page = 1,
  pageSize = 20,
): UseQueryResult<PaginatedResponse<PromptSummary>> {
  return useQuery({
    queryKey: promptKeys.list(projectId, page),
    queryFn: async () => {
      if (config.useMock) {
        const items = mockToSummary();
        return {
          items,
          total: items.length,
          page,
          page_size: pageSize,
        };
      }
      return apiRequest<PaginatedResponse<PromptSummary>>("/prompts", {
        query: { project_id: projectId, page, page_size: pageSize },
      });
    },
    enabled: !!projectId,
  });
}

export function usePromptDetail(
  projectId: string,
  name: string | null | undefined,
  version?: number,
  label?: string,
): UseQueryResult<PromptDetail> {
  return useQuery({
    queryKey: promptKeys.detail(projectId, name ?? "", version),
    queryFn: async () => {
      if (!name) throw new Error("prompt name required");
      if (config.useMock) {
        const detail = mockDetail(name, version);
        if (!detail) throw new Error(`Mock prompt not found: ${name}`);
        return detail;
      }
      return apiRequest<PromptDetail>(`/prompts/${encodeURIComponent(name)}`, {
        query: { project_id: projectId, version, label },
      });
    },
    enabled: !!projectId && !!name,
  });
}

export function usePromptVersions(
  projectId: string,
  name: string | null | undefined,
): UseQueryResult<{ versions: PromptVersionSummary[] }> {
  return useQuery({
    queryKey: promptKeys.versions(projectId, name ?? ""),
    queryFn: async () => {
      if (!name) throw new Error("prompt name required");
      if (config.useMock) {
        const p = mockPrompts.find((x) => x.name === name);
        return {
          versions:
            p?.versions.map((v) => ({
              version: v.version,
              labels: p.labels,
              created_at: v.createdAt,
              created_by: v.author,
            })) ?? [],
        };
      }
      return apiRequest<{ versions: PromptVersionSummary[] }>(
        `/prompts/${encodeURIComponent(name)}/versions`,
        { query: { project_id: projectId } },
      );
    },
    enabled: !!projectId && !!name,
  });
}

export function useCreatePrompt(): UseMutationResult<
  PromptCreateResponse,
  Error,
  PromptCreate
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: PromptCreate) => {
      if (config.useMock) {
        return {
          name: payload.name,
          version: 1,
          labels: payload.labels ?? [],
        };
      }
      return apiRequest<PromptCreateResponse>("/prompts", {
        method: "POST",
        body: payload,
      });
    },
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: promptKeys.all });
      void variables.project_id;
    },
  });
}

export interface PromoteLabelInput {
  name: string;
  version: number;
  payload: PromptLabelsPatch;
  ifMatch?: string;
}

export function usePromoteLabel(): UseMutationResult<
  PromptCreateResponse,
  Error,
  PromoteLabelInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, version, payload, ifMatch }) => {
      if (config.useMock) {
        return { name, version, labels: payload.labels };
      }
      return apiRequest<PromptCreateResponse>(
        `/prompts/${encodeURIComponent(name)}/versions/${version}/labels`,
        {
          method: "PATCH",
          body: payload,
          ifMatch,
        },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: promptKeys.all });
    },
  });
}
