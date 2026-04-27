"use client";

import { useMemo, useState } from "react";
import { ArrowRight, Search, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { EmptyState } from "@/components/ui/EmptyState";
import { RequireRole, useAuth } from "@/lib/auth";
import {
  useApprovedEvaluators,
  useApproveEvaluator,
  useBuiltInEvaluators,
  useDeprecateEvaluator,
  useEvaluatorSubmissions,
  useRejectEvaluator,
} from "@/lib/hooks/useEvaluators";
import type {
  ApprovedEvaluator,
  BuiltInEvaluatorInfo,
  Submission,
} from "@/lib/types/api";
import { cn, formatNumber, formatRelativeDate } from "@/lib/utils";

type EvaluatorType = "builtin" | "judge" | "custom";
type EvaluatorStatus = "approved" | "pending" | "rejected" | "deprecated";

interface EvaluatorView {
  id: string;
  name: string;
  type: EvaluatorType;
  status: EvaluatorStatus;
  description: string;
  range: string;
  submittedBy?: string;
  submittedAt?: string;
  approvedBy?: string;
  approvedAt?: string;
  usageCount: number;
  code?: string;
  judgePrompt?: string;
  judgeModel?: string;
}

const STATUS_TONE: Record<
  EvaluatorStatus,
  "success" | "warning" | "error" | "muted"
> = {
  approved: "success",
  pending: "warning",
  rejected: "error",
  deprecated: "muted",
};

const STATUS_LABEL: Record<EvaluatorStatus, string> = {
  approved: "approved",
  pending: "pending",
  rejected: "rejected",
  deprecated: "deprecated",
};

const TYPE_LABEL: Record<EvaluatorType, string> = {
  builtin: "내장",
  judge: "LLM-Judge",
  custom: "Custom",
};

const TYPE_TABS: { value: EvaluatorType; label: string }[] = [
  { value: "builtin", label: "내장" },
  { value: "judge", label: "LLM-Judge" },
  { value: "custom", label: "Custom" },
];

const DEFAULT_PROJECT_ID = "production-api";

/** Built-in 5 default judge rubric (정적) */
const DEFAULT_JUDGE_RUBRICS: EvaluatorView[] = [
  {
    id: "judge_factuality",
    name: "Factuality Judge",
    type: "judge",
    status: "approved",
    description: "응답이 정답에 비해 사실적으로 일치하는지 0~10점으로 평가합니다.",
    range: "0-10",
    usageCount: 0,
    judgeModel: "azure/gpt-4o",
    judgePrompt: `당신은 평가자입니다. 응답이 정답에 비해 얼마나 사실적으로 일치하는지 0~10점으로 평가하세요.

질문/입력: {{input}}
정답: {{expected}}
응답: {{output}}

JSON으로만 응답:
{ "score": 0~10, "rationale": "한 문장" }`,
  },
  {
    id: "judge_relevance",
    name: "Relevance Judge",
    type: "judge",
    status: "approved",
    description: "응답이 입력 질문과 얼마나 관련 있는지 평가합니다.",
    range: "0-10",
    usageCount: 0,
    judgeModel: "azure/gpt-4o",
  },
  {
    id: "judge_completeness",
    name: "Completeness Judge",
    type: "judge",
    status: "approved",
    description: "응답이 정답의 핵심 정보를 빠짐없이 포함하는지 평가합니다.",
    range: "0-10",
    usageCount: 0,
    judgeModel: "azure/gpt-4o",
  },
  {
    id: "judge_conciseness",
    name: "Conciseness Judge",
    type: "judge",
    status: "approved",
    description: "응답이 불필요한 부연 없이 간결한지 평가합니다.",
    range: "0-10",
    usageCount: 0,
    judgeModel: "azure/gpt-4o",
  },
  {
    id: "judge_safety",
    name: "Safety Judge",
    type: "judge",
    status: "approved",
    description: "응답이 위험하거나 부적절한 내용을 포함하는지 평가합니다.",
    range: "0-10",
    usageCount: 0,
    judgeModel: "azure/gpt-4o",
  },
];

function rangeFromBuiltIn(b: BuiltInEvaluatorInfo): string {
  if (b.range) return `${b.range[0]}-${b.range[1]}`;
  if (b.return_type === "binary") return "binary";
  if (b.return_type === "float") return "0-1";
  return "0-1";
}

function fromBuiltIn(b: BuiltInEvaluatorInfo): EvaluatorView {
  return {
    id: b.name,
    name: b.name,
    type: "builtin",
    status: "approved",
    description: b.description,
    range: rangeFromBuiltIn(b),
    usageCount: 0,
  };
}

function fromApproved(a: ApprovedEvaluator): EvaluatorView {
  return {
    id: a.submission_id,
    name: a.name,
    type: "custom",
    status: "approved",
    description: a.description,
    range: "0-1",
    submittedAt: a.approved_at,
    approvedBy: a.approver,
    approvedAt: a.approved_at,
    usageCount: 0,
  };
}

function fromSubmission(s: Submission): EvaluatorView {
  return {
    id: s.submission_id,
    name: s.name,
    type: "custom",
    status: s.status,
    description: s.description,
    range: "0-1",
    submittedBy: s.submitted_by ?? s.submitter,
    submittedAt: s.submitted_at ?? s.created_at,
    approvedBy: s.approved_by ?? s.reviewer,
    approvedAt: s.approved_at ?? s.reviewed_at,
    usageCount: 0,
    code: s.code,
  };
}

export default function EvaluatorsPage() {
  const { hasRole, user } = useAuth();
  const projectId =
    (user as { currentProjectId?: string } | null)?.currentProjectId ??
    DEFAULT_PROJECT_ID;
  const [tab, setTab] = useState<EvaluatorType>("builtin");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const builtInQuery = useBuiltInEvaluators();
  const approvedQuery = useApprovedEvaluators(projectId);
  const pendingQuery = useEvaluatorSubmissions(projectId, "pending");
  const rejectedQuery = useEvaluatorSubmissions(projectId, "rejected");
  const deprecatedQuery = useEvaluatorSubmissions(projectId, "deprecated");

  const builtInList: EvaluatorView[] = useMemo(
    () => (builtInQuery.data?.evaluators ?? []).map(fromBuiltIn),
    [builtInQuery.data]
  );

  const customApproved: EvaluatorView[] = useMemo(
    () => (approvedQuery.data?.evaluators ?? []).map(fromApproved),
    [approvedQuery.data]
  );
  const customPending: EvaluatorView[] = useMemo(
    () => (pendingQuery.data?.submissions ?? []).map(fromSubmission),
    [pendingQuery.data]
  );
  const customRejected: EvaluatorView[] = useMemo(
    () => (rejectedQuery.data?.submissions ?? []).map(fromSubmission),
    [rejectedQuery.data]
  );
  const customDeprecated: EvaluatorView[] = useMemo(
    () => (deprecatedQuery.data?.submissions ?? []).map(fromSubmission),
    [deprecatedQuery.data]
  );

  const allCustom = useMemo(
    () => [...customPending, ...customApproved, ...customRejected, ...customDeprecated],
    [customPending, customApproved, customRejected, customDeprecated]
  );

  const list: EvaluatorView[] = useMemo(() => {
    let base: EvaluatorView[] = [];
    if (tab === "builtin") base = builtInList;
    else if (tab === "judge") base = DEFAULT_JUDGE_RUBRICS;
    else base = allCustom;
    const q = query.trim().toLowerCase();
    if (!q) return base;
    return base.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q)
    );
  }, [tab, query, builtInList, allCustom]);

  const selected: EvaluatorView | null = useMemo(() => {
    return list.find((e) => e.id === selectedId) ?? list[0] ?? null;
  }, [list, selectedId]);

  const isAdmin = hasRole?.("admin") ?? false;

  const onTabChange = (next: string) => {
    const t = next as EvaluatorType;
    setTab(t);
    setSelectedId(null);
    setActionError(null);
  };

  const jumpToFirstPending = () => {
    if (customPending.length === 0) return;
    setTab("custom");
    setSelectedId(customPending[0].id);
    setActionError(null);
  };

  const isLoading =
    (tab === "builtin" && builtInQuery.isLoading) ||
    (tab === "custom" &&
      (pendingQuery.isLoading ||
        approvedQuery.isLoading ||
        rejectedQuery.isLoading ||
        deprecatedQuery.isLoading));

  return (
    <div className="px-8 py-6">
      <PageHeader
        title="평가 (Evaluator)"
        description="내장 / LLM-Judge / Custom 평가 함수 관리"
      />

      {/* Governance queue */}
      {customPending.length > 0 && (
        <Card className="mb-5 border-amber-900/60 bg-amber-950/20">
          <CardContent className="flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <span
                aria-hidden
                className="grid h-9 w-9 place-items-center rounded-md bg-amber-500/15 text-amber-300"
              >
                <ShieldCheck className="h-4 w-4" />
              </span>
              <div>
                <h3 className="text-sm font-semibold text-zinc-100">
                  검토 대기 {customPending.length}건
                </h3>
                <p className="mt-0.5 text-xs text-zinc-400">
                  Custom 평가 함수가 승인 대기 중입니다. 승인 전에는 일부
                  사용자만 사용할 수 있습니다.
                </p>
              </div>
            </div>
            <Button variant="outline" onClick={jumpToFirstPending}>
              지금 검토
              <ArrowRight className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,35fr)_minmax(0,65fr)]">
        {/* Left: list */}
        <Card className="flex flex-col">
          <div className="border-b border-zinc-800 p-3">
            <Tabs value={tab} onValueChange={onTabChange}>
              <TabsList className="w-full" aria-label="평가 함수 분류">
                {TYPE_TABS.map((t) => (
                  <TabsTrigger
                    key={t.value}
                    value={t.value}
                    className="flex-1 justify-center"
                  >
                    {t.label}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            <div className="relative mt-3">
              <Search
                aria-hidden
                className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500"
              />
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="이름 또는 설명 검색"
                className="pl-8"
                aria-label="평가 함수 검색"
              />
            </div>
          </div>
          <ul className="max-h-[calc(100dvh-280px)] divide-y divide-zinc-800 overflow-y-auto">
            {isLoading ? (
              <li className="space-y-2 p-4">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-12 animate-pulse rounded bg-zinc-900/50" />
                ))}
              </li>
            ) : list.length === 0 ? (
              <li className="p-4">
                <EmptyState
                  title="결과 없음"
                  description="검색어를 변경하거나 다른 분류를 선택하세요."
                />
              </li>
            ) : (
              list.map((e) => {
                const active = selected?.id === e.id;
                return (
                  <li key={e.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedId(e.id);
                        setActionError(null);
                      }}
                      className={cn(
                        "flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors",
                        active && "bg-indigo-500/10",
                        !active && "hover:bg-zinc-900/60",
                        e.status === "pending" && "bg-amber-950/20",
                        e.status === "deprecated" && "opacity-60"
                      )}
                      aria-current={active ? "true" : undefined}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className={cn(
                            "truncate text-sm font-medium",
                            e.status === "deprecated"
                              ? "text-zinc-400 line-through"
                              : "text-zinc-100"
                          )}
                        >
                          {e.name}
                        </span>
                        <Badge tone={STATUS_TONE[e.status]}>
                          {STATUS_LABEL[e.status]}
                        </Badge>
                      </div>
                      <p className="line-clamp-1 text-xs text-zinc-400">
                        {e.description}
                      </p>
                      <p className="text-[11px] text-zinc-500">
                        사용 {formatNumber(e.usageCount)}회
                      </p>
                    </button>
                  </li>
                );
              })
            )}
          </ul>
        </Card>

        {/* Right: detail */}
        <Card>
          {selected ? (
            <EvaluatorDetail
              evaluator={selected}
              isAdmin={isAdmin}
              actionError={actionError}
              setActionError={setActionError}
            />
          ) : (
            <CardContent>
              <EmptyState
                title="평가 함수를 선택하세요"
                description="좌측 목록에서 항목을 선택하면 상세가 표시됩니다."
              />
            </CardContent>
          )}
        </Card>
      </div>
    </div>
  );
}

function EvaluatorDetail({
  evaluator,
  isAdmin,
  actionError,
  setActionError,
}: {
  evaluator: EvaluatorView;
  isAdmin: boolean;
  actionError: string | null;
  setActionError: (m: string | null) => void;
}) {
  const approveMutation = useApproveEvaluator();
  const rejectMutation = useRejectEvaluator();
  const deprecateMutation = useDeprecateEvaluator();
  const [rejectReason, setRejectReason] = useState("");
  const [showRejectForm, setShowRejectForm] = useState(false);

  const handleApprove = async () => {
    setActionError(null);
    try {
      await approveMutation.mutateAsync({ submissionId: evaluator.id });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleReject = async () => {
    if (!rejectReason.trim()) {
      setActionError("반려 사유를 입력해 주세요.");
      return;
    }
    setActionError(null);
    try {
      await rejectMutation.mutateAsync({
        submissionId: evaluator.id,
        payload: { reason: rejectReason.trim() },
      });
      setRejectReason("");
      setShowRejectForm(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleDeprecate = async () => {
    if (!confirm(`'${evaluator.name}'을(를) Deprecated로 전환하시겠습니까?`)) return;
    setActionError(null);
    try {
      await deprecateMutation.mutateAsync({
        submissionId: evaluator.id,
        reason: "manual deprecation",
      });
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-zinc-50">{evaluator.name}</h2>
          <Badge tone={STATUS_TONE[evaluator.status]}>
            {STATUS_LABEL[evaluator.status]}
          </Badge>
          <Badge tone="info">{TYPE_LABEL[evaluator.type]}</Badge>
          <Badge tone="neutral">range: {evaluator.range}</Badge>
        </div>
        <p className="mt-2 text-sm text-zinc-300">{evaluator.description}</p>
      </div>

      <div className="flex-1 space-y-5 px-5 py-4">
        {/* Usage stats */}
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="누적 사용 횟수" value={formatNumber(evaluator.usageCount)} />
          <Stat label="범위" value={evaluator.range} />
          <Stat label="타입" value={TYPE_LABEL[evaluator.type]} />
        </section>

        {evaluator.type === "custom" && (
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              제출 정보
            </h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              <div>
                <dt className="text-zinc-500">제출자</dt>
                <dd className="text-zinc-200">{evaluator.submittedBy ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">제출일</dt>
                <dd className="text-zinc-200">
                  {evaluator.submittedAt
                    ? formatRelativeDate(evaluator.submittedAt)
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500">승인자</dt>
                <dd className="text-zinc-200">{evaluator.approvedBy ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">승인일</dt>
                <dd className="text-zinc-200">
                  {evaluator.approvedAt
                    ? formatRelativeDate(evaluator.approvedAt)
                    : "—"}
                </dd>
              </div>
            </dl>
            {evaluator.code && (
              <>
                <h3 className="mt-4 mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                  평가 코드
                </h3>
                <pre className="overflow-x-auto rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-200">
                  {evaluator.code}
                </pre>
              </>
            )}
          </section>
        )}

        {evaluator.type === "judge" && (
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              Judge 모델
            </h3>
            <Badge tone="info">{evaluator.judgeModel ?? "azure/gpt-4o"}</Badge>
            {evaluator.judgePrompt && (
              <>
                <h3 className="mt-4 mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                  평가 프롬프트
                </h3>
                <pre className="whitespace-pre-wrap rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-200">
                  {evaluator.judgePrompt}
                </pre>
              </>
            )}
          </section>
        )}

        {actionError && (
          <div className="rounded-md border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-xs text-rose-200">
            {actionError}
          </div>
        )}

        {showRejectForm && (
          <section className="rounded-md border border-zinc-800 bg-zinc-950/40 p-3 space-y-2">
            <label htmlFor="reject-reason" className="text-xs font-medium text-zinc-300">
              반려 사유
            </label>
            <textarea
              id="reject-reason"
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              rows={3}
              placeholder="반려 사유를 명확히 작성해 주세요"
              className="w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-100 focus:border-indigo-500 focus:outline-none"
            />
            <div className="flex justify-end gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setShowRejectForm(false);
                  setRejectReason("");
                }}
              >
                취소
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleReject}
                disabled={rejectMutation.isPending}
              >
                {rejectMutation.isPending ? "반려 중..." : "반려 확정"}
              </Button>
            </div>
          </section>
        )}
      </div>

      {/* Actions footer */}
      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-zinc-800 bg-zinc-950/40 px-5 py-3">
        {!isAdmin && (
          <span className="text-xs text-zinc-500">
            관리자만 상태를 변경할 수 있습니다.
          </span>
        )}
        {evaluator.type === "custom" && evaluator.status === "pending" && (
          <RequireRole role="admin">
            <Button
              variant="outline"
              onClick={() => setShowRejectForm(true)}
              disabled={rejectMutation.isPending || approveMutation.isPending}
            >
              반려
            </Button>
            <Button
              variant="primary"
              onClick={handleApprove}
              disabled={approveMutation.isPending || rejectMutation.isPending}
            >
              {approveMutation.isPending ? "승인 중..." : "승인"}
            </Button>
          </RequireRole>
        )}
        {evaluator.type === "custom" && evaluator.status === "approved" && (
          <RequireRole role="admin">
            <Button
              variant="outline"
              onClick={handleDeprecate}
              disabled={deprecateMutation.isPending}
            >
              {deprecateMutation.isPending ? "전환 중..." : "Deprecated로 전환"}
            </Button>
          </RequireRole>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-zinc-800 bg-zinc-950/40 px-3 py-2.5">
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div className="mt-0.5 font-mono text-base font-semibold text-zinc-100 tabular-nums">
        {value}
      </div>
    </div>
  );
}
