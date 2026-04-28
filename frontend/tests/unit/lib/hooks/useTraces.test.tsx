/**
 * Phase 8-A: useTraces 훅 단위 테스트.
 *
 * 검증
 * - useTraceSearch: 실 모드 backend 호출 (POST /traces/search)
 * - useTraceSearch: 페이지네이션 응답 구조
 * - useTraceSearch: filter=null이면 enabled=false
 * - useTraceDetail: 실 모드 backend 호출 (GET /traces/{id})
 * - useTraceDetail: traceId=null이면 enabled=false
 *
 * mock 모드 분기는 별도 통합 테스트(NEXT_PUBLIC_USE_MOCK=true)에서 검증.
 */
import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../../mocks/server";
import { useTraceDetail, useTraceSearch } from "@/lib/hooks/useTraces";
import { config } from "@/lib/config";
import type { TraceFilter } from "@/lib/types/api";

const API = `${config.apiBaseUrl}/api/v1`;

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const sampleFilter: TraceFilter = {
  project_id: "proj-1",
  name: "qa-agent",
  sample_size: 50,
  sample_strategy: "random",
};

describe("useTraceSearch", () => {
  it("페이지네이션 응답을 그대로 반환한다", async () => {
    if (config.useMock) return;
    const payload = {
      items: [
        {
          id: "trace-1",
          name: "qa-agent",
          user_id: null,
          session_id: null,
          tags: ["production"],
          total_cost_usd: 0.0014,
          total_latency_ms: 800,
          timestamp: "2026-04-25T08:30:00.000Z",
          observation_count: 2,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    };
    server.use(
      http.post(`${API}/traces/search`, () => HttpResponse.json(payload)),
    );

    const { result } = renderHook(() => useTraceSearch(sampleFilter, 1, 20), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.total).toBe(1);
    expect(result.current.data?.items[0].id).toBe("trace-1");
    expect(result.current.data?.page).toBe(1);
    expect(result.current.data?.page_size).toBe(20);
  });

  it("filter=null이면 호출하지 않는다 (enabled=false)", async () => {
    if (config.useMock) return;
    const { result } = renderHook(() => useTraceSearch(null), {
      wrapper: makeWrapper(),
    });
    // 약간 기다려도 fetching 상태가 아니어야 함 (enabled=false)
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(result.current.fetchStatus).toBe("idle");
    expect(result.current.isSuccess).toBe(false);
  });

  it("page/page_size 인자를 호출 body에 포함한다", async () => {
    if (config.useMock) return;
    type Captured = { page?: number; page_size?: number } | null;
    const captured: { value: Captured } = { value: null };
    server.use(
      http.post(`${API}/traces/search`, async ({ request }) => {
        const body = (await request.json()) as {
          page?: number;
          page_size?: number;
        };
        captured.value = body;
        return HttpResponse.json({
          items: [],
          total: 0,
          page: body.page ?? 1,
          page_size: body.page_size ?? 20,
        });
      }),
    );
    const { result } = renderHook(() => useTraceSearch(sampleFilter, 3, 50), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured.value?.page).toBe(3);
    expect(captured.value?.page_size).toBe(50);
  });
});

describe("useTraceDetail", () => {
  it("trace 단건 응답을 반환한다", async () => {
    if (config.useMock) return;
    const payload = {
      id: "trace-x",
      project_id: "proj-1",
      name: "qa-agent",
      input: { question: "hi" },
      output: "hello",
      user_id: null,
      session_id: null,
      tags: [],
      metadata: {},
      observations: [],
      scores: [],
      total_cost_usd: 0.001,
      total_latency_ms: 500,
      timestamp: "2026-04-25T08:30:00.000Z",
    };
    server.use(
      http.get(`${API}/traces/trace-x`, () => HttpResponse.json(payload)),
    );
    const { result } = renderHook(() => useTraceDetail("trace-x", "proj-1"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.id).toBe("trace-x");
    expect(result.current.data?.observations).toEqual([]);
  });

  it("traceId=null이면 호출하지 않는다 (enabled=false)", async () => {
    if (config.useMock) return;
    const { result } = renderHook(() => useTraceDetail(null, "proj-1"), {
      wrapper: makeWrapper(),
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(result.current.fetchStatus).toBe("idle");
    expect(result.current.isSuccess).toBe(false);
  });

  it("project_id 쿼리스트링이 URL에 포함된다", async () => {
    if (config.useMock) return;
    const captured: { url: URL | null } = { url: null };
    server.use(
      http.get(`${API}/traces/trace-y`, ({ request }) => {
        captured.url = new URL(request.url);
        return HttpResponse.json({
          id: "trace-y",
          project_id: "proj-2",
          name: "agent",
          input: null,
          output: null,
          user_id: null,
          session_id: null,
          tags: [],
          metadata: {},
          observations: [],
          scores: [],
          total_cost_usd: 0,
          total_latency_ms: null,
          timestamp: "2026-04-25T00:00:00.000Z",
        });
      }),
    );
    const { result } = renderHook(
      () => useTraceDetail("trace-y", "proj-2"),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured.url?.searchParams.get("project_id")).toBe("proj-2");
  });
});
