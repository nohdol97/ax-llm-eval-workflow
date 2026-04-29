import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

type UseReviewsModule = typeof import("@/lib/hooks/useReviews");

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

async function loadUseReviewsModule(): Promise<UseReviewsModule> {
  vi.resetModules();
  vi.doMock("@/lib/config", () => ({
    config: {
      apiBaseUrl: "http://localhost:8000",
      useMock: true,
      appName: "GenAI Labs",
      pollInterval: {
        notifications: 30_000,
        experimentList: 60_000,
      },
    },
  }));
  return import("@/lib/hooks/useReviews");
}

afterEach(() => {
  vi.resetModules();
  vi.doUnmock("@/lib/config");
});

describe("decisionLabel", () => {
  it("4가지 결정 라벨을 반환한다", async () => {
    const { decisionLabel } = await loadUseReviewsModule();

    expect(decisionLabel("approve")).toBe("Approve (자동 점수 확정)");
    expect(decisionLabel("override")).toBe("Override (수동 점수)");
    expect(decisionLabel("dismiss")).toBe("Dismiss (false positive)");
    expect(decisionLabel("add_to_dataset")).toBe(
      "Add to Dataset (골든셋 추가)",
    );
  });
});

describe("severityColor", () => {
  it("severity별 className을 반환한다", async () => {
    const { severityColor } = await loadUseReviewsModule();

    expect(severityColor("high")).toBe("text-red-600");
    expect(severityColor("medium")).toBe("text-yellow-600");
    expect(severityColor("low")).toBe("text-green-600");
  });
});

describe("useReviews mock mode", () => {
  it("useCreateReviewItem → mockStore에 항목을 추가한다", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();

    const { result } = renderHook(() => mod.useCreateReviewItem(), { wrapper });

    let createdId = "";
    await act(async () => {
      const item = await result.current.mutateAsync({
        subject_id: "trace-create",
        project_id: "proj-1",
        severity: "high",
        reason: "manual_reason",
      });
      createdId = item.id;
      expect(item.type).toBe("manual_addition");
      expect(item.status).toBe("open");
      expect(item.severity).toBe("high");
    });

    const listHook = renderHook(() => mod.useReviewItemList({ projectId: "proj-1" }), {
      wrapper,
    });
    await waitFor(() => expect(listHook.result.current.isSuccess).toBe(true));
    expect(listHook.result.current.data?.items.some((it) => it.id === createdId)).toBe(
      true,
    );
  });

  it("useReportTrace → type=user_report, subject_type 기본값 trace", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();
    const { result } = renderHook(() => mod.useReportTrace(), { wrapper });

    await act(async () => {
      const item = await result.current.mutateAsync({
        trace_id: "trace-report",
        project_id: "proj-1",
        reason: "bad answer",
        severity: "medium",
      });
      expect(item.type).toBe("user_report");
      expect(item.subject_type).toBe("trace");
      expect(item.reason).toBe("user_report");
    });
  });

  it("useReportTrace → subject_type=experiment_item 을 그대로 저장한다", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();
    const { result } = renderHook(() => mod.useReportTrace(), { wrapper });

    await act(async () => {
      const item = await result.current.mutateAsync({
        trace_id: "exp-item-1",
        project_id: "proj-1",
        reason: "needs review",
        subject_type: "experiment_item",
      });
      expect(item.subject_type).toBe("experiment_item");
      expect(item.subject_id).toBe("exp-item-1");
    });
  });

  it("useClaimReviewItem → status=in_review, assigned_to/at 설정", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();
    const createHook = renderHook(() => mod.useCreateReviewItem(), { wrapper });
    const claimHook = renderHook(() => mod.useClaimReviewItem(), { wrapper });

    let itemId = "";
    await act(async () => {
      const created = await createHook.result.current.mutateAsync({
        subject_id: "trace-claim",
        project_id: "proj-1",
      });
      itemId = created.id;
    });

    await act(async () => {
      const claimed = await claimHook.result.current.mutateAsync({ itemId });
      expect(claimed.status).toBe("in_review");
      expect(claimed.assigned_to).toBe("current_user");
      expect(claimed.assigned_at).toBeTruthy();
    });
  });

  it("useReleaseReviewItem → status=open 으로 복귀한다", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();
    const createHook = renderHook(() => mod.useCreateReviewItem(), { wrapper });
    const claimHook = renderHook(() => mod.useClaimReviewItem(), { wrapper });
    const releaseHook = renderHook(() => mod.useReleaseReviewItem(), { wrapper });

    let itemId = "";
    await act(async () => {
      const created = await createHook.result.current.mutateAsync({
        subject_id: "trace-release",
        project_id: "proj-1",
      });
      itemId = created.id;
      await claimHook.result.current.mutateAsync({ itemId });
    });

    await act(async () => {
      const released = await releaseHook.result.current.mutateAsync({ itemId });
      expect(released.status).toBe("open");
      expect(released.assigned_to).toBeNull();
      expect(released.assigned_at).toBeNull();
    });
  });

  it("useResolveReviewItem decision=approve → status=resolved", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();
    const createHook = renderHook(() => mod.useCreateReviewItem(), { wrapper });
    const resolveHook = renderHook(() => mod.useResolveReviewItem(), { wrapper });

    let itemId = "";
    await act(async () => {
      const created = await createHook.result.current.mutateAsync({
        subject_id: "trace-resolve-approve",
        project_id: "proj-1",
      });
      itemId = created.id;
    });

    await act(async () => {
      const resolved = await resolveHook.result.current.mutateAsync({
        itemId,
        payload: { decision: "approve", reviewer_comment: "ok" },
      });
      expect(resolved.status).toBe("resolved");
      expect(resolved.decision).toBe("approve");
      expect(resolved.resolved_at).toBeTruthy();
      expect(resolved.resolved_by).toBe("current_user");
    });
  });

  it("useResolveReviewItem decision=dismiss → status=dismissed", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();
    const createHook = renderHook(() => mod.useCreateReviewItem(), { wrapper });
    const resolveHook = renderHook(() => mod.useResolveReviewItem(), { wrapper });

    let itemId = "";
    await act(async () => {
      const created = await createHook.result.current.mutateAsync({
        subject_id: "trace-resolve-dismiss",
        project_id: "proj-1",
      });
      itemId = created.id;
    });

    await act(async () => {
      const resolved = await resolveHook.result.current.mutateAsync({
        itemId,
        payload: { decision: "dismiss" },
      });
      expect(resolved.status).toBe("dismissed");
      expect(resolved.decision).toBe("dismiss");
    });
  });

  it("useDeleteReviewItem → mockStore에서 제거한다", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();
    const createHook = renderHook(() => mod.useCreateReviewItem(), { wrapper });
    const deleteHook = renderHook(() => mod.useDeleteReviewItem(), { wrapper });

    let itemId = "";
    await act(async () => {
      const created = await createHook.result.current.mutateAsync({
        subject_id: "trace-delete",
        project_id: "proj-1",
      });
      itemId = created.id;
    });

    await act(async () => {
      await deleteHook.result.current.mutateAsync({ itemId });
    });

    const listHook = renderHook(() => mod.useReviewItemList({ projectId: "proj-1" }), {
      wrapper,
    });
    await waitFor(() => expect(listHook.result.current.isSuccess).toBe(true));
    expect(listHook.result.current.data?.items).toHaveLength(0);
  });

  it("useReviewSummary → open/in_review/resolved_today 카운트를 집계한다", async () => {
    const mod = await loadUseReviewsModule();
    const wrapper = makeWrapper();
    const createHook = renderHook(() => mod.useCreateReviewItem(), { wrapper });
    const claimHook = renderHook(() => mod.useClaimReviewItem(), { wrapper });
    const resolveHook = renderHook(() => mod.useResolveReviewItem(), { wrapper });

    let inReviewId = "";
    let resolvedId = "";
    await act(async () => {
      await createHook.result.current.mutateAsync({
        subject_id: "trace-open",
        project_id: "proj-1",
      });
      inReviewId = (
        await createHook.result.current.mutateAsync({
          subject_id: "trace-in-review",
          project_id: "proj-1",
        })
      ).id;
      resolvedId = (
        await createHook.result.current.mutateAsync({
          subject_id: "trace-resolved",
          project_id: "proj-1",
        })
      ).id;
      await claimHook.result.current.mutateAsync({ itemId: inReviewId });
      await resolveHook.result.current.mutateAsync({
        itemId: resolvedId,
        payload: { decision: "approve" },
      });
    });

    const summaryHook = renderHook(() => mod.useReviewSummary("proj-1"), {
      wrapper,
    });
    await waitFor(() => expect(summaryHook.result.current.isSuccess).toBe(true));
    expect(summaryHook.result.current.data).toMatchObject({
      open: 1,
      in_review: 1,
      resolved_today: 1,
    });
  });
});

