"use client";

/**
 * Review 항목 상세 + 결정 폼 페이지 (Phase 8-C-9).
 *
 * AGENT_EVAL.md §19.2 명세 — 진입 사유 + Trace 요약 + 자동 점수 + 결정 폼.
 */
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft } from "lucide-react";
import { useAuth } from "@/lib/auth";
import {
  decisionLabel,
  useClaimReviewItem,
  useDeleteReviewItem,
  useReleaseReviewItem,
  useResolveReviewItem,
  useReviewItem,
  severityColor,
} from "@/lib/hooks/useReviews";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import type { ReviewDecision } from "@/lib/types/api";
import { cn } from "@/lib/utils";

export default function ReviewDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const itemId = params?.id ?? "";
  const { user, hasRole } = useAuth();

  const { data: item, isLoading, error, refetch } = useReviewItem(itemId);
  const claim = useClaimReviewItem();
  const release = useReleaseReviewItem();
  const resolve = useResolveReviewItem();
  const remove = useDeleteReviewItem();

  const [decision, setDecision] = useState<ReviewDecision>("approve");
  const [reviewerScore, setReviewerScore] = useState<number>(0.5);
  const [reviewerComment, setReviewerComment] = useState<string>("");
  const [expectedOutput, setExpectedOutput] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="px-6 py-6 text-sm text-zinc-500">불러오는 중…</div>
    );
  }
  if (error || !item) {
    return (
      <div className="px-6 py-6 text-sm text-red-400">
        오류: {error?.message ?? "not found"}
        <Button
          size="sm"
          variant="ghost"
          className="ml-2"
          onClick={() => refetch()}
        >
          재시도
        </Button>
      </div>
    );
  }

  const isAssignedToMe = item.assigned_to === user?.id;
  const isReviewer = hasRole("reviewer");
  const canClaim = isReviewer && item.status === "open";
  const canRelease = isReviewer && item.status === "in_review" && (isAssignedToMe || hasRole("admin"));
  const canResolve = isReviewer && (
    (item.status === "in_review" && isAssignedToMe) ||
    (item.status === "open" && decision === "dismiss")
  );

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError(null);
    setSubmitting(true);
    try {
      await resolve.mutateAsync({
        itemId,
        payload: {
          decision,
          reviewer_score:
            decision === "override" ? reviewerScore : undefined,
          reviewer_comment: reviewerComment || undefined,
          expected_output:
            decision === "add_to_dataset" && expectedOutput
              ? safeParseJsonOrText(expectedOutput)
              : undefined,
        },
      });
      router.push("/review");
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const weighted = item.automatic_scores?.weighted_score;

  return (
    <div className="px-6 py-6">
      <PageHeader
        title={`Review #${item.id}`}
        description={
          item.status === "in_review" && item.assigned_to
            ? `in_review by ${item.assigned_to}`
            : `status=${item.status}`
        }
        actions={
          <div className="flex items-center gap-2">
            <Link
              href="/review"
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 px-2.5 text-xs font-medium text-zinc-200 transition-colors hover:border-zinc-600"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
              뒤로
            </Link>
            {canClaim ? (
              <Button
                size="sm"
                onClick={() => claim.mutate({ itemId })}
                disabled={claim.isPending}
              >
                {claim.isPending ? "claim 중…" : "Claim"}
              </Button>
            ) : null}
            {canRelease ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => release.mutate({ itemId })}
                disabled={release.isPending}
              >
                Unassign
              </Button>
            ) : null}
            {hasRole("admin") ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={async () => {
                  if (!confirm("이 항목을 삭제할까요? (admin only)")) return;
                  await remove.mutateAsync({ itemId });
                  router.push("/review");
                }}
              >
                삭제
              </Button>
            ) : null}
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* Left: 진입 사유 + Trace + 자동 점수 */}
        <div className="space-y-4 lg:col-span-2">
          <Section title="진입 사유">
            <div className="space-y-2 text-sm">
              <div>
                <span className="text-zinc-500">type:</span>{" "}
                <span className="font-mono text-zinc-200">{item.type}</span>
              </div>
              <div>
                <span className="text-zinc-500">reason:</span>{" "}
                <span className="font-mono text-zinc-200">{item.reason}</span>
              </div>
              <div>
                <span className="text-zinc-500">severity:</span>{" "}
                <span className={cn("font-mono", severityColor(item.severity))}>
                  {item.severity}
                </span>
              </div>
              {Object.keys(item.reason_detail ?? {}).length > 0 ? (
                <pre className="overflow-auto rounded bg-zinc-900 p-2 text-xs text-zinc-300">
                  {JSON.stringify(item.reason_detail, null, 2)}
                </pre>
              ) : null}
              {item.auto_eval_policy_id ? (
                <div>
                  <span className="text-zinc-500">policy_id:</span>{" "}
                  <Link
                    className="font-mono text-indigo-300 hover:underline"
                    href={`/auto-eval/${item.auto_eval_policy_id}`}
                  >
                    {item.auto_eval_policy_id}
                  </Link>
                </div>
              ) : null}
            </div>
          </Section>

          <Section title="Trace">
            <div className="space-y-2 text-sm">
              <div>
                <span className="text-zinc-500">subject_type:</span>{" "}
                <span className="font-mono text-zinc-200">
                  {item.subject_type}
                </span>
              </div>
              <div>
                <span className="text-zinc-500">subject_id:</span>{" "}
                <span className="font-mono text-zinc-200">
                  {item.subject_id}
                </span>
              </div>
              <div className="text-xs text-zinc-500">
                상세 trace 는 Langfuse 또는 /experiments 페이지에서 확인하세요.
              </div>
            </div>
          </Section>

          <Section title="자동 평가 결과 (snapshot)">
            <div className="space-y-1.5 text-sm">
              {Object.entries(item.automatic_scores ?? {}).map(
                ([name, value]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5"
                  >
                    <span className="font-mono text-xs text-zinc-300">
                      {name}
                    </span>
                    <span
                      className={cn(
                        "font-mono text-xs",
                        typeof value === "number" && value < 0.5
                          ? "text-red-400"
                          : "text-green-300",
                      )}
                    >
                      {value === null || value === undefined
                        ? "—"
                        : typeof value === "number"
                          ? value.toFixed(2)
                          : String(value)}
                    </span>
                  </div>
                ),
              )}
              {weighted != null ? (
                <div className="mt-2 flex items-center justify-between rounded border border-indigo-500/40 bg-indigo-500/10 px-2.5 py-1.5">
                  <span className="font-mono text-xs text-indigo-200">
                    weighted_score
                  </span>
                  <span className="font-mono text-xs text-indigo-200">
                    {weighted.toFixed(2)}
                  </span>
                </div>
              ) : null}
            </div>
          </Section>
        </div>

        {/* Right: 결정 폼 */}
        <div>
          <Section title="결정">
            {!canResolve ? (
              <div className="text-sm text-zinc-500">
                {item.status === "open"
                  ? "Claim 한 후 결정할 수 있습니다 (또는 dismiss 만 가능)."
                  : item.status === "in_review"
                    ? `이 항목은 ${item.assigned_to ?? "다른 사용자"} 가 claim 중입니다.`
                    : `이미 ${item.status} 상태입니다.`}
              </div>
            ) : (
              <form onSubmit={onSubmit} className="space-y-3 text-sm">
                {(
                  [
                    "approve",
                    "override",
                    "dismiss",
                    "add_to_dataset",
                  ] as ReviewDecision[]
                ).map((d) => (
                  <label
                    key={d}
                    className={cn(
                      "flex cursor-pointer items-start gap-2 rounded border px-2.5 py-2 transition-colors",
                      decision === d
                        ? "border-indigo-500 bg-indigo-500/10"
                        : "border-zinc-800 bg-zinc-950 hover:border-zinc-700",
                    )}
                  >
                    <input
                      type="radio"
                      name="decision"
                      checked={decision === d}
                      onChange={() => setDecision(d)}
                      className="mt-0.5"
                    />
                    <span className="text-xs text-zinc-200">
                      {decisionLabel(d)}
                    </span>
                  </label>
                ))}

                {decision === "override" ? (
                  <div className="space-y-1.5">
                    <label className="text-xs text-zinc-400">
                      수정된 점수 ({reviewerScore.toFixed(2)})
                    </label>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={reviewerScore}
                      onChange={(e) =>
                        setReviewerScore(Number(e.target.value))
                      }
                      className="w-full"
                    />
                  </div>
                ) : null}

                {decision === "add_to_dataset" ? (
                  <div className="space-y-1.5">
                    <label className="text-xs text-zinc-400">
                      expected_output (JSON 또는 텍스트)
                    </label>
                    <textarea
                      value={expectedOutput}
                      onChange={(e) => setExpectedOutput(e.target.value)}
                      rows={4}
                      className="w-full rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-zinc-200 focus:border-indigo-500 focus:outline-none"
                      placeholder='{"answer": "..."}'
                    />
                  </div>
                ) : null}

                <div className="space-y-1.5">
                  <label className="text-xs text-zinc-400">
                    코멘트 (선택)
                  </label>
                  <textarea
                    value={reviewerComment}
                    onChange={(e) => setReviewerComment(e.target.value)}
                    rows={3}
                    className="w-full rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-xs text-zinc-200 focus:border-indigo-500 focus:outline-none"
                    placeholder="결정 사유 / 메모"
                  />
                </div>

                {submitError ? (
                  <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
                    {submitError}
                  </div>
                ) : null}

                <Button type="submit" disabled={submitting} className="w-full">
                  {submitting ? "저장 중…" : "결정 저장"}
                </Button>
              </form>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-zinc-800 bg-zinc-950 p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">
        {title}
      </h2>
      {children}
    </section>
  );
}

function safeParseJsonOrText(text: string): unknown {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}
