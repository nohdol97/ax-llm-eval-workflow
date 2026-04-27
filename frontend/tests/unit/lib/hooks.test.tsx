/**
 * 도메인 훅 / config mock 분기 테스트.
 *
 * 검증 항목:
 *  - usePromptList: 실 모드에서 backend `/prompts` 호출
 *  - useNotificationList: response shape 정규화 (`notifications` → `items`)
 *  - useHealth: skipAuth 동작 (Authorization 없음)
 *
 * mock 모드(useMock=true)는 환경 변수에 의존하므로 별도 통합 테스트로 검증한다.
 */
import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { server } from "../../mocks/server";
import {
  useHealth,
  useNotificationList,
  usePromptList,
} from "@/lib/hooks";
import { config } from "@/lib/config";
import type { ReactNode } from "react";

const API = `${config.apiBaseUrl}/api/v1`;

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("usePromptList", () => {
  it("backend /prompts 응답을 그대로 반환한다", async () => {
    if (config.useMock) return; // mock 환경에서는 별도
    const payload = {
      items: [
        {
          name: "p1",
          latest_version: 2,
          labels: ["production"],
          tags: [],
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    };
    server.use(
      http.get(`${API}/prompts`, () => HttpResponse.json(payload)),
    );

    const { result } = renderHook(() => usePromptList("proj-1"), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items[0].name).toBe("p1");
    expect(result.current.data?.total).toBe(1);
  });
});

describe("useNotificationList", () => {
  it("`notifications` 키를 `items`로 정규화한다", async () => {
    if (config.useMock) return;
    server.use(
      http.get(`${API}/notifications`, () =>
        HttpResponse.json({
          notifications: [
            {
              id: "n1",
              user_id: "u1",
              type: "experiment_complete",
              title: "T",
              read: false,
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
          total: 1,
          unread_count: 1,
          page: 1,
          page_size: 20,
        }),
      ),
    );

    const { result } = renderHook(
      () => useNotificationList("proj-1"),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.items?.length).toBe(1);
    expect(result.current.data?.unread_count).toBe(1);
  });
});

describe("useHealth", () => {
  it("/health 호출 시 Authorization 헤더 없이 요청한다 (skipAuth)", async () => {
    if (config.useMock) return;
    let auth: string | null = "init";
    server.use(
      http.get(`${API}/health`, ({ request }) => {
        auth = request.headers.get("authorization");
        return HttpResponse.json({
          status: "ok",
          version: "1.0.0",
          environment: "test",
          services: {},
        });
      }),
    );
    const { result } = renderHook(() => useHealth(), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(auth).toBeNull();
  });
});
