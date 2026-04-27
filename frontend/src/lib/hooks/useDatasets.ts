/**
 * 데이터셋 도메인 React Query 훅.
 *
 * 참조: docs/API_DESIGN.md §6, §12
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { apiRequest, apiUpload } from "../api";
import { config } from "../config";
import { datasetItems, datasets as mockDatasets } from "../mock/data";
import type {
  DatasetFromItemsRequest,
  DatasetItem,
  DatasetSummary,
  PaginatedResponse,
  UploadPreviewResponse,
  UploadResponse,
} from "../types/api";

export const datasetKeys = {
  all: ["datasets"] as const,
  list: (projectId: string) =>
    ["datasets", "list", projectId] as const,
  items: (projectId: string, name: string, page: number) =>
    ["datasets", "items", projectId, name, page] as const,
};

function mockToSummary(): DatasetSummary[] {
  return mockDatasets.map((d) => ({
    name: d.name,
    description: d.description,
    item_count: d.itemCount,
    created_at: d.createdAt,
    last_used_at: d.lastUsed,
  }));
}

export function useDatasetList(
  projectId: string,
): UseQueryResult<{ datasets: DatasetSummary[] } | PaginatedResponse<DatasetSummary>> {
  return useQuery({
    queryKey: datasetKeys.list(projectId),
    queryFn: async () => {
      if (config.useMock) {
        return { datasets: mockToSummary() };
      }
      return apiRequest<{ datasets: DatasetSummary[] }>("/datasets", {
        query: { project_id: projectId },
      });
    },
    enabled: !!projectId,
  });
}

export function useDatasetItems(
  projectId: string,
  name: string | null | undefined,
  page = 1,
  pageSize = 20,
): UseQueryResult<PaginatedResponse<DatasetItem>> {
  return useQuery({
    queryKey: datasetKeys.items(projectId, name ?? "", page),
    queryFn: async () => {
      if (!name) throw new Error("dataset name required");
      if (config.useMock) {
        const found = mockDatasets.find((d) => d.name === name);
        const items = found ? datasetItems[found.id] ?? [] : [];
        return {
          items: items.map((it) => ({
            id: it.id,
            input: it.input,
            expected_output: it.expectedOutput,
            metadata: it.metadata,
          })),
          total: items.length,
          page,
          page_size: pageSize,
        };
      }
      return apiRequest<PaginatedResponse<DatasetItem>>(
        `/datasets/${encodeURIComponent(name)}/items`,
        {
          query: { project_id: projectId, page, page_size: pageSize },
        },
      );
    },
    enabled: !!projectId && !!name,
  });
}

export interface UploadDatasetInput {
  formData: FormData;
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export function useUploadDataset(): UseMutationResult<
  UploadResponse,
  Error,
  UploadDatasetInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ formData, idempotencyKey, signal }) => {
      if (config.useMock) {
        return {
          dataset_name:
            (formData.get("dataset_name") as string | null) ?? "mock-dataset",
          items_created: 10,
          items_failed: 0,
          failed_items: [],
          status: "completed" as const,
          upload_id: "mock-upload-id",
        };
      }
      return apiUpload<UploadResponse>("/datasets/upload", formData, {
        idempotencyKey,
        signal,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: datasetKeys.all });
    },
  });
}

export function useUploadPreview(): UseMutationResult<
  UploadPreviewResponse,
  Error,
  FormData
> {
  return useMutation({
    mutationFn: async (formData) => {
      if (config.useMock) {
        return {
          columns: ["input_text", "expected_label"],
          preview: [
            {
              input: { input_text: "샘플" },
              expected_output: "positive",
              metadata: {},
            },
          ],
          total_rows: 1,
        };
      }
      return apiUpload<UploadPreviewResponse>(
        "/datasets/upload/preview",
        formData,
      );
    },
  });
}

export function useDeriveDataset(): UseMutationResult<
  { dataset_name: string; items_created: number; status: string },
  Error,
  DatasetFromItemsRequest
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      if (config.useMock) {
        return {
          dataset_name: payload.new_dataset_name,
          items_created: payload.item_ids.length,
          status: "completed",
        };
      }
      return apiRequest<{
        dataset_name: string;
        items_created: number;
        status: string;
      }>("/datasets/from-items", { method: "POST", body: payload });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: datasetKeys.all });
    },
  });
}

export function useDeleteDataset(): UseMutationResult<
  void,
  Error,
  { name: string; ifMatch?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ name, ifMatch }) => {
      if (config.useMock) return;
      await apiRequest(`/datasets/${encodeURIComponent(name)}`, {
        method: "DELETE",
        ifMatch,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: datasetKeys.all });
    },
  });
}
