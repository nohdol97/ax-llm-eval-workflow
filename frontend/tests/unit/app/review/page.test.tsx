import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReviewItem, ReviewQueueSummary } from "@/lib/types/api";

const mockUseAuth = vi.fn();
const mockUseReviewItemList = vi.fn();
const mockUseReviewSummary = vi.fn();

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock("@/lib/hooks/useReviews", () => ({
  useReviewItemList: (...args: unknown[]) => mockUseReviewItemList(...args),
  useReviewSummary: (...args: unknown[]) => mockUseReviewSummary(...args),
}));

import ReviewQueuePage from "@/app/review/page";

function makeSummary(overrides: Partial<ReviewQueueSummary> = {}): ReviewQueueSummary {
  return {
    open: 2,
    in_review: 1,
    resolved_today: 3,
    dismissed_today: 0,
    avg_resolution_time_min: 12.4,
    ...overrides,
  };
}

function makeItem(overrides: Partial<ReviewItem> = {}): ReviewItem {
  return {
    id: "review_1",
    type: "user_report",
    severity: "high",
    subject_type: "trace",
    subject_id: "trace_subject_1234567890",
    project_id: "production-api",
    reason: "user_report",
    reason_detail: {},
    automatic_scores: { weighted_score: 0.42 },
    status: "open",
    created_at: "2026-04-29T10:00:00Z",
    updated_at: "2026-04-29T10:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  mockUseAuth.mockReset();
  mockUseReviewItemList.mockReset();
  mockUseReviewSummary.mockReset();

  mockUseAuth.mockReturnValue({
    user: { id: "reviewer-1", role: "reviewer" },
    hasRole: (role: string) => role === "reviewer",
  });
  mockUseReviewSummary.mockReturnValue({ data: makeSummary() });
  mockUseReviewItemList.mockReturnValue({
    data: { items: [], total: 0, page: 1, page_size: 50 },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
});

describe("ReviewQueuePage", () => {
  it("빈 큐면 EmptyState를 렌더한다", () => {
    render(<ReviewQueuePage />);

    expect(screen.getByText("큐가 비어 있습니다")).toBeInTheDocument();
  });

  it("KPI 카드 4개를 렌더한다", () => {
    render(<ReviewQueuePage />);

    expect(screen.getByText("Open")).toBeInTheDocument();
    expect(screen.getByText("In Review")).toBeInTheDocument();
    expect(screen.getByText("Resolved (오늘)")).toBeInTheDocument();
    expect(screen.getByText("평균 처리 시간")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("12.4분")).toBeInTheDocument();
  });

  it("큐 테이블 헤더를 렌더한다", () => {
    mockUseReviewItemList.mockReturnValue({
      data: { items: [makeItem()], total: 1, page: 1, page_size: 50 },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<ReviewQueuePage />);

    expect(screen.getByText("우선순위")).toBeInTheDocument();
    expect(screen.getByText("사유")).toBeInTheDocument();
    expect(screen.getByText("type")).toBeInTheDocument();
    expect(screen.getByText("Subject")).toBeInTheDocument();
    expect(screen.getByText("자동 점수")).toBeInTheDocument();
    expect(screen.getByText("상태")).toBeInTheDocument();
    expect(screen.getByText("생성")).toBeInTheDocument();
  });

  it("탭 4개를 렌더한다", () => {
    render(<ReviewQueuePage />);

    expect(screen.getByRole("tab", { name: "전체" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "내가 담당" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "높은 우선순위" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "User Report" })).toBeInTheDocument();
  });

  it("useReviewItemList가 반환한 항목을 테이블 행으로 표시한다", () => {
    mockUseReviewItemList.mockReturnValue({
      data: { items: [makeItem()], total: 1, page: 1, page_size: 50 },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<ReviewQueuePage />);

    expect(screen.getByText("🔴 high")).toBeInTheDocument();
    expect(screen.getAllByText("user_report")).toHaveLength(2);
    expect(screen.getByText("0.42")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "상세" })).toHaveAttribute(
      "href",
      "/review/review_1",
    );
  });
});
