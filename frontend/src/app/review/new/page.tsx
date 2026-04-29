"use client";

/**
 * Review Queue 수동 추가 페이지 (Phase 8-C-9 / §18.1).
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowLeft } from "lucide-react";
import { useCreateReviewItem } from "@/lib/hooks/useReviews";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import type { ReviewSeverity, ReviewSubjectType } from "@/lib/types/api";

const DEFAULT_PROJECT_ID = "production-api";

export default function NewReviewPage() {
  const router = useRouter();
  const create = useCreateReviewItem();
  const [projectId, setProjectId] = useState(DEFAULT_PROJECT_ID);
  const [subjectType, setSubjectType] = useState<ReviewSubjectType>("trace");
  const [subjectId, setSubjectId] = useState("");
  const [severity, setSeverity] = useState<ReviewSeverity>("medium");
  const [reason, setReason] = useState("manual_addition");
  const [reasonText, setReasonText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!subjectId.trim()) {
      setError("subject_id 가 필요합니다.");
      return;
    }
    try {
      const item = await create.mutateAsync({
        project_id: projectId,
        subject_type: subjectType,
        subject_id: subjectId.trim(),
        severity,
        reason: reason.trim() || "manual_addition",
        reason_detail: reasonText.trim()
          ? { note: reasonText.trim() }
          : undefined,
      });
      router.push(`/review/${encodeURIComponent(item.id)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="px-6 py-6">
      <PageHeader
        title="수동 추가 (Manual Addition)"
        description="Reviewer 가 직접 trace 를 큐에 등록합니다."
        actions={
          <Link
            href="/review"
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-900 px-2.5 text-xs font-medium text-zinc-200 hover:border-zinc-600"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            뒤로
          </Link>
        }
      />

      <form
        onSubmit={onSubmit}
        className="max-w-xl space-y-4 rounded-lg border border-zinc-800 bg-zinc-950 p-5"
      >
        <Field label="Project ID">
          <Input
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
          />
        </Field>

        <Field label="Subject Type">
          <select
            value={subjectType}
            onChange={(e) =>
              setSubjectType(e.target.value as ReviewSubjectType)
            }
            className="h-9 w-full rounded border border-zinc-800 bg-zinc-950 px-2 text-sm text-zinc-200 focus:border-indigo-500 focus:outline-none"
          >
            <option value="trace">trace</option>
            <option value="experiment_item">experiment_item</option>
            <option value="submission">submission</option>
          </select>
        </Field>

        <Field label="Subject ID">
          <Input
            value={subjectId}
            onChange={(e) => setSubjectId(e.target.value)}
            placeholder="trace_abc123 / experiment_item_xxx"
            required
          />
        </Field>

        <Field label="Severity">
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as ReviewSeverity)}
            className="h-9 w-full rounded border border-zinc-800 bg-zinc-950 px-2 text-sm text-zinc-200 focus:border-indigo-500 focus:outline-none"
          >
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
          </select>
        </Field>

        <Field label="Reason (key)">
          <Input
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="manual_addition"
          />
        </Field>

        <Field label="Note (선택)">
          <textarea
            value={reasonText}
            onChange={(e) => setReasonText(e.target.value)}
            rows={3}
            className="w-full rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 text-sm text-zinc-200 focus:border-indigo-500 focus:outline-none"
            placeholder="이 trace 를 큐에 추가하는 이유"
          />
        </Field>

        {error ? (
          <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">
            {error}
          </div>
        ) : null}

        <Button type="submit" disabled={create.isPending} className="w-full">
          {create.isPending ? "추가 중…" : "큐에 추가"}
        </Button>
      </form>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-zinc-400">{label}</span>
      {children}
    </label>
  );
}
