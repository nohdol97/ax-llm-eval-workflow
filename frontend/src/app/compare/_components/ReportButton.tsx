"use client";

/**
 * Compare row 신고 버튼 + 모달 (Phase 8-C-10).
 *
 * 클릭 → 모달 (severity + 사유) → POST /api/v1/reviews/report.
 * subject_type=experiment_item 으로 전송 — backend 가 ReviewItem 생성.
 *
 * AGENT_EVAL.md §20.
 */
import { useState } from "react";
import { Flag } from "lucide-react";
import { useReportTrace } from "@/lib/hooks/useReviews";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import type { ReviewSeverity } from "@/lib/types/api";
import { cn } from "@/lib/utils";

interface ReportButtonProps {
  itemId: string;
  projectId: string;
  /** "trace" | "experiment_item" — compare 행은 항상 experiment_item */
  subjectType?: "trace" | "experiment_item" | "submission";
  /** 작은 사이즈 — 행 안 인라인 */
  size?: "sm" | "md";
}

export function ReportButton({
  itemId,
  projectId,
  subjectType = "experiment_item",
  size = "sm",
}: ReportButtonProps) {
  const [open, setOpen] = useState(false);
  const [severity, setSeverity] = useState<ReviewSeverity>("medium");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const report = useReportTrace();

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!reason.trim()) {
      setError("사유를 입력하세요.");
      return;
    }
    try {
      await report.mutateAsync({
        trace_id: itemId,
        project_id: projectId,
        reason: reason.trim(),
        severity,
        subject_type: subjectType,
      });
      setSubmitted(true);
      setTimeout(() => {
        setOpen(false);
        setSubmitted(false);
        setReason("");
        setSeverity("medium");
      }, 1200);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        aria-label="이 결과 신고"
        title="이 결과가 잘못되었나요? (신고)"
        className={cn(
          "inline-flex items-center gap-1 rounded border border-zinc-700 bg-zinc-900 font-medium text-zinc-400 transition-colors hover:border-rose-500/50 hover:text-rose-300",
          size === "sm" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-xs",
        )}
      >
        <Flag className={size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5"} />
        신고
      </button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="결과 신고 (Review Queue 추가)"
      >
        {submitted ? (
          <div className="py-6 text-center text-sm text-emerald-300">
            ✅ 신고가 접수되었습니다 — Reviewer 가 검토할 예정입니다.
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-3">
            <div className="rounded border border-zinc-800 bg-zinc-950 p-2 text-[11px] text-zinc-400">
              <span className="font-mono text-zinc-300">{subjectType}:</span>{" "}
              <span className="font-mono text-zinc-200">{itemId}</span>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-zinc-400">
                Severity
              </label>
              <div className="flex gap-1.5">
                {(["low", "medium", "high"] as ReviewSeverity[]).map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setSeverity(s)}
                    aria-pressed={severity === s}
                    className={cn(
                      "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                      severity === s
                        ? "border-rose-500 bg-rose-500/15 text-rose-200"
                        : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700",
                    )}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-zinc-400">
                사유 *
              </label>
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={3}
                required
                placeholder="응답이 사실과 다름 / 형식 오류 등"
                className="w-full rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-sm text-zinc-200 focus:border-indigo-500 focus:outline-none"
              />
            </div>

            {error ? (
              <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
                {error}
              </div>
            ) : null}

            <div className="flex justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setOpen(false)}
              >
                취소
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={report.isPending}
              >
                {report.isPending ? "신고 중…" : "신고 보내기"}
              </Button>
            </div>
          </form>
        )}
      </Modal>
    </>
  );
}
