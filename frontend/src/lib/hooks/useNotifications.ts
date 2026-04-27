/**
 * 알림 도메인 React Query 훅.
 *
 * 폴링 간격은 config.pollInterval.notifications (30s) 기본.
 *
 * 참조: docs/API_DESIGN.md §13
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
import { notifications as mockNotifications } from "../mock/data";
import type {
  Notification,
  NotificationListResponse,
} from "../types/api";

export const notificationKeys = {
  all: ["notifications"] as const,
  list: (projectId: string, unreadOnly: boolean, page: number) =>
    [
      "notifications",
      "list",
      projectId,
      unreadOnly ? "unread" : "all",
      page,
    ] as const,
};

function mockToNotificationList(
  unreadOnly: boolean,
  page: number,
  pageSize: number,
): NotificationListResponse {
  const items: Notification[] = mockNotifications.map((n) => ({
    id: n.id,
    user_id: "mock-admin",
    type: n.type,
    title: n.title,
    body: n.body,
    message: n.body,
    link: n.link,
    target_url: n.link,
    read: n.read,
    created_at: n.createdAt,
  }));
  const filtered = unreadOnly ? items.filter((x) => !x.read) : items;
  const start = (page - 1) * pageSize;
  return {
    items: filtered.slice(start, start + pageSize),
    total: filtered.length,
    unread_count: items.filter((x) => !x.read).length,
    page,
    page_size: pageSize,
  };
}

function normalizeListResponse(
  raw: NotificationListResponse,
): NotificationListResponse {
  // backend는 `notifications` 또는 `items` 두 형태를 모두 사용 — 통합
  if (raw.items) return raw;
  if (raw.notifications) {
    return { ...raw, items: raw.notifications };
  }
  return { ...raw, items: [] };
}

export function useNotificationList(
  projectId: string,
  options: { unreadOnly?: boolean; page?: number; pageSize?: number } = {},
): UseQueryResult<NotificationListResponse> {
  const { unreadOnly = false, page = 1, pageSize = 20 } = options;
  return useQuery({
    queryKey: notificationKeys.list(projectId, unreadOnly, page),
    queryFn: async () => {
      if (config.useMock) {
        return mockToNotificationList(unreadOnly, page, pageSize);
      }
      const raw = await apiRequest<NotificationListResponse>(
        "/notifications",
        {
          query: {
            project_id: projectId,
            unread_only: unreadOnly,
            page,
            page_size: pageSize,
          },
        },
      );
      return normalizeListResponse(raw);
    },
    enabled: !!projectId,
    refetchInterval: config.pollInterval.notifications,
  });
}

export function useMarkNotificationRead(): UseMutationResult<
  { id: string; read: boolean },
  Error,
  { id: string; ifMatch?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ifMatch }) => {
      if (config.useMock) return { id, read: true };
      return apiRequest<{ id: string; read: boolean }>(
        `/notifications/${encodeURIComponent(id)}/read`,
        { method: "PATCH", ifMatch },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: notificationKeys.all });
    },
  });
}

export function useMarkAllNotificationsRead(): UseMutationResult<
  { marked_count: number },
  Error,
  void
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      if (config.useMock)
        return {
          marked_count: mockNotifications.filter((n) => !n.read).length,
        };
      return apiRequest<{ marked_count: number }>(
        "/notifications/mark-all-read",
        { method: "POST" },
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: notificationKeys.all });
    },
  });
}

// ─────────────────────────────────────────────────────────────────────
// 페이지 에이전트 호환 alias
// ─────────────────────────────────────────────────────────────────────
export const useMarkRead = useMarkNotificationRead;
export const useMarkAllRead = useMarkAllNotificationsRead;

