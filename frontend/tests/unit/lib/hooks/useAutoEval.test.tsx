/**
 * Phase 8-B: useAutoEval 훅 단위 테스트.
 *
 * 검증
 * - useAutoEvalPolicyList: backend 호출, status 필터, 페이지네이션 응답
 * - useAutoEvalPolicy: 단건 조회
 * - useCreateAutoEvalPolicy: POST body 전달, idempotency 헤더
 * - usePausePolicy / useResumePolicy: 상태 전환 mutation
 * - useRunPolicyNow: POST run-now 트리거
 * - useAutoEvalRunList: 정책별 run 목록 query 파라미터
 * - useCostUsage: from_date/to_date 쿼리 전달
 *
 * 본 테스트는 실 모드 (config.useMock=false) 가정. mock 분기 동작은
 * 별도 통합 환경(NEXT_PUBLIC_USE_MOCK=true)에서 next build로 검증한다.
 */
import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../../mocks/server";
import {
  useAutoEvalPolicy,
  useAutoEvalPolicyList,
  useAutoEvalRunList,
  useCostUsage,
  useCreateAutoEvalPolicy,
  usePausePolicy,
  useResumePolicy,
  useRunPolicyNow,
} from "@/lib/hooks/useAutoEval";
import { config } from "@/lib/config";
import type {
  AutoEvalPolicy,
  AutoEvalPolicyCreate,
  AutoEvalPolicyListResponse,
  AutoEvalRun,
  AutoEvalRunListResponse,
  CostUsage,
} from "@/lib/types/api";

const API = `${config.apiBaseUrl}/api/v1`;

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const samplePolicy: AutoEvalPolicy = {
  id: "policy_1",
  name: "qa-daily",
  project_id: "proj-1",
  trace_filter: { project_id: "proj-1", name: "qa-agent", sample_size: 100 },
  evaluators: [{ type: "trace_builtin", name: "tool_called", weight: 1.0 }],
  schedule: { type: "cron", cron_expression: "0 3 * * *", timezone: "Asia/Seoul" },
  alert_thresholds: [],
  notification_targets: [],
  status: "active",
  owner: "user_1",
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-01T00:00:00Z",
};

describe("useAutoEvalPolicyList", () => {
  it("페이지네이션 응답을 그대로 반환한다", async () => {
    if (config.useMock) return;
    const payload: AutoEvalPolicyListResponse = {
      items: [samplePolicy],
      total: 1,
      page: 1,
      page_size: 20,
    };
    server.use(
      http.get(`${API}/auto-eval/policies`, () => HttpResponse.json(payload)),
    );

    const { result } = renderHook(() => useAutoEvalPolicyList("proj-1"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.total).toBe(1);
    expect(result.current.data?.items[0].id).toBe("policy_1");
  });

  it("status 필터를 query string으로 전달한다", async () => {
    if (config.useMock) return;
    const captured: { status?: string | null } = {};
    server.use(
      http.get(`${API}/auto-eval/policies`, ({ request }) => {
        const url = new URL(request.url);
        captured.status = url.searchParams.get("status");
        return HttpResponse.json({ items: [], total: 0, page: 1, page_size: 20 });
      }),
    );
    const { result } = renderHook(
      () => useAutoEvalPolicyList("proj-1", { status: "paused" }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured.status).toBe("paused");
  });

  it("projectId가 빈 문자열이면 호출하지 않는다 (enabled=false)", async () => {
    if (config.useMock) return;
    const { result } = renderHook(() => useAutoEvalPolicyList(""), {
      wrapper: makeWrapper(),
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(result.current.fetchStatus).toBe("idle");
    expect(result.current.isSuccess).toBe(false);
  });
});

describe("useAutoEvalPolicy", () => {
  it("단건 정책 응답을 반환한다", async () => {
    if (config.useMock) return;
    server.use(
      http.get(`${API}/auto-eval/policies/policy_1`, () =>
        HttpResponse.json(samplePolicy),
      ),
    );
    const { result } = renderHook(() => useAutoEvalPolicy("policy_1"), {
      wrapper: makeWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.id).toBe("policy_1");
    expect(result.current.data?.schedule.type).toBe("cron");
  });

  it("policyId=null이면 호출하지 않는다 (enabled=false)", async () => {
    if (config.useMock) return;
    const { result } = renderHook(() => useAutoEvalPolicy(null), {
      wrapper: makeWrapper(),
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(result.current.fetchStatus).toBe("idle");
  });
});

describe("useCreateAutoEvalPolicy", () => {
  it("POST body와 Idempotency-Key 헤더를 전달한다", async () => {
    if (config.useMock) return;
    type Captured = {
      body: AutoEvalPolicyCreate | null;
      idemKey: string | null;
    };
    const captured: Captured = { body: null, idemKey: null };
    server.use(
      http.post(`${API}/auto-eval/policies`, async ({ request }) => {
        captured.body = (await request.json()) as AutoEvalPolicyCreate;
        captured.idemKey = request.headers.get("Idempotency-Key");
        return HttpResponse.json(samplePolicy, { status: 201 });
      }),
    );
    const { result } = renderHook(() => useCreateAutoEvalPolicy(), {
      wrapper: makeWrapper(),
    });
    const payload: AutoEvalPolicyCreate = {
      name: "qa-daily",
      project_id: "proj-1",
      trace_filter: { project_id: "proj-1", sample_size: 100 },
      evaluators: [{ type: "trace_builtin", name: "tool_called", weight: 1.0 }],
      schedule: { type: "cron", cron_expression: "0 3 * * *" },
    };
    await result.current.mutateAsync({
      payload,
      idempotencyKey: "key-abc",
    });
    expect(captured.body?.name).toBe("qa-daily");
    expect(captured.body?.evaluators[0].name).toBe("tool_called");
    expect(captured.idemKey).toBe("key-abc");
  });
});

describe("usePausePolicy / useResumePolicy", () => {
  it("usePausePolicy → POST /pause 호출", async () => {
    if (config.useMock) return;
    let invoked = false;
    server.use(
      http.post(`${API}/auto-eval/policies/policy_1/pause`, () => {
        invoked = true;
        return HttpResponse.json({ ...samplePolicy, status: "paused" });
      }),
    );
    const { result } = renderHook(() => usePausePolicy(), {
      wrapper: makeWrapper(),
    });
    const updated = await result.current.mutateAsync("policy_1");
    expect(invoked).toBe(true);
    expect(updated.status).toBe("paused");
  });

  it("useResumePolicy → POST /resume 호출", async () => {
    if (config.useMock) return;
    server.use(
      http.post(`${API}/auto-eval/policies/policy_1/resume`, () =>
        HttpResponse.json({ ...samplePolicy, status: "active" }),
      ),
    );
    const { result } = renderHook(() => useResumePolicy(), {
      wrapper: makeWrapper(),
    });
    const updated = await result.current.mutateAsync("policy_1");
    expect(updated.status).toBe("active");
  });
});

describe("useRunPolicyNow", () => {
  it("POST /run-now → AutoEvalRun 응답 (202)", async () => {
    if (config.useMock) return;
    const run: AutoEvalRun = {
      id: "run_x",
      policy_id: "policy_1",
      started_at: "2026-04-25T10:00:00Z",
      status: "running",
      traces_evaluated: 0,
      traces_total: 100,
      cost_usd: 0,
      scores_by_evaluator: {},
      triggered_alerts: [],
      review_items_created: 0,
    };
    server.use(
      http.post(`${API}/auto-eval/policies/policy_1/run-now`, () =>
        HttpResponse.json(run, { status: 202 }),
      ),
    );
    const { result } = renderHook(() => useRunPolicyNow(), {
      wrapper: makeWrapper(),
    });
    const ret = await result.current.mutateAsync("policy_1");
    expect(ret.id).toBe("run_x");
    expect(ret.status).toBe("running");
  });
});

describe("useAutoEvalRunList", () => {
  it("policy_id query를 전달하고 응답을 반환한다", async () => {
    if (config.useMock) return;
    const captured: { policyId: string | null; status: string | null } = {
      policyId: null,
      status: null,
    };
    const payload: AutoEvalRunListResponse = {
      items: [
        {
          id: "run_1",
          policy_id: "policy_1",
          started_at: "2026-04-25T03:00:00Z",
          status: "completed",
          traces_evaluated: 100,
          traces_total: 100,
          avg_score: 0.9,
          pass_rate: 0.92,
          cost_usd: 0.5,
          scores_by_evaluator: { tool_called: 0.95 },
          triggered_alerts: [],
          review_items_created: 8,
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    };
    server.use(
      http.get(`${API}/auto-eval/runs`, ({ request }) => {
        const url = new URL(request.url);
        captured.policyId = url.searchParams.get("policy_id");
        captured.status = url.searchParams.get("status");
        return HttpResponse.json(payload);
      }),
    );
    const { result } = renderHook(
      () => useAutoEvalRunList("policy_1", { status: "completed" }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured.policyId).toBe("policy_1");
    expect(captured.status).toBe("completed");
    expect(result.current.data?.items[0].avg_score).toBe(0.9);
  });
});

describe("useCostUsage", () => {
  it("from_date / to_date query를 전달한다", async () => {
    if (config.useMock) return;
    const captured: { from: string | null; to: string | null } = {
      from: null,
      to: null,
    };
    const payload: CostUsage = {
      policy_id: "policy_1",
      date_range: "2026-04-01..2026-04-25",
      daily_breakdown: [
        { date: "2026-04-25", cost_usd: 0.5, runs_count: 1 },
      ],
      total_cost_usd: 0.5,
      daily_limit_usd: 5,
    };
    server.use(
      http.get(
        `${API}/auto-eval/policies/policy_1/cost-usage`,
        ({ request }) => {
          const url = new URL(request.url);
          captured.from = url.searchParams.get("from_date");
          captured.to = url.searchParams.get("to_date");
          return HttpResponse.json(payload);
        },
      ),
    );
    const { result } = renderHook(
      () => useCostUsage("policy_1", "2026-04-01", "2026-04-25"),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured.from).toBe("2026-04-01");
    expect(captured.to).toBe("2026-04-25");
    expect(result.current.data?.total_cost_usd).toBe(0.5);
  });
});
