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
import { currentUser, evaluators } from "@/lib/mock/data";
import type { Evaluator, EvaluatorStatus, EvaluatorType } from "@/lib/mock/types";
import { cn, formatNumber, formatRelativeDate } from "@/lib/utils";

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

const PYTHON_PLACEHOLDER = `def evaluate(output: str, expected: str, metadata: dict) -> float:
    """
    Custom evaluator. Returns a score in [0, 1].
    """
    if not output or not expected:
        return 0.0
    # Example: token overlap as a baseline
    out_tokens = set(output.lower().split())
    exp_tokens = set(expected.lower().split())
    if not exp_tokens:
        return 0.0
    return len(out_tokens & exp_tokens) / len(exp_tokens)
`;

const JUDGE_PROMPT_PLACEHOLDER = `당신은 평가자입니다. 다음 응답이 정답에 비해 얼마나 일치하는지
0~10점으로 평가하세요.

질문/입력: {{input}}
정답: {{expected}}
응답: {{output}}

평가 기준:
1. 사실성 — 정답과 사실관계가 일치하는가
2. 완결성 — 핵심 정보가 빠짐없이 포함되는가
3. 간결성 — 불필요한 부연이 없는가

JSON으로만 응답:
{ "score": 0~10, "rationale": "한 문장" }`;

const TYPE_TABS: { value: EvaluatorType; label: string }[] = [
  { value: "builtin", label: "내장" },
  { value: "judge", label: "LLM-Judge" },
  { value: "custom", label: "Custom" },
];

export default function EvaluatorsPage() {
  const [tab, setTab] = useState<EvaluatorType>("builtin");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(
    evaluators.find((e) => e.type === "builtin")?.id ?? null
  );

  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    return evaluators
      .filter((e) => e.type === tab)
      .filter(
        (e) =>
          !q ||
          e.name.toLowerCase().includes(q) ||
          e.description.toLowerCase().includes(q)
      );
  }, [tab, query]);

  const selected =
    evaluators.find((e) => e.id === selectedId && e.type === tab) ?? list[0] ?? null;

  const pending = useMemo(
    () => evaluators.filter((e) => e.status === "pending"),
    []
  );

  const isAdmin = currentUser.role === "admin";

  const onTabChange = (next: string) => {
    const t = next as EvaluatorType;
    setTab(t);
    const first = evaluators.find((e) => e.type === t);
    setSelectedId(first?.id ?? null);
  };

  const jumpToFirstPending = () => {
    if (pending.length === 0) return;
    const first = pending[0];
    setTab(first.type);
    setSelectedId(first.id);
  };

  return (
    <div className="px-8 py-6">
      <PageHeader
        title="평가 (Evaluator)"
        description="내장 / LLM-Judge / Custom 평가 함수 관리"
      />

      {/* Governance queue */}
      {pending.length > 0 && (
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
                  검토 대기 {pending.length}건
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
            {list.length === 0 ? (
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
                      onClick={() => setSelectedId(e.id)}
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
            <EvaluatorDetail evaluator={selected} isAdmin={isAdmin} />
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
}: {
  evaluator: Evaluator;
  isAdmin: boolean;
}) {
  const trend = useMemo(() => {
    // Deterministic mock 7-day trend
    let h = 0;
    for (let i = 0; i < evaluator.id.length; i++)
      h = (h * 31 + evaluator.id.charCodeAt(i)) >>> 0;
    return Array.from({ length: 7 }).map(() => {
      h = (h * 9301 + 49297) >>> 0;
      return h % 60;
    });
  }, [evaluator.id]);
  const monthlyTotal = trend.reduce((a, b) => a + b, 0);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-zinc-800 px-5 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-zinc-50">
            {evaluator.name}
          </h2>
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
          <Stat label="최근 30일 (mock)" value={formatNumber(monthlyTotal)} />
          <Stat label="범위" value={evaluator.range} />
        </section>
        <section>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
            최근 7일 사용 트렌드
          </h3>
          <div className="flex items-end gap-1.5 rounded-md border border-zinc-800 bg-zinc-950/40 p-3">
            {trend.map((v, i) => (
              <div key={i} className="flex flex-1 flex-col items-center gap-1">
                <div
                  className="w-full rounded-sm bg-indigo-500/40"
                  style={{ height: `${4 + v}px` }}
                  aria-hidden
                />
                <span className="text-[10px] text-zinc-500">{v}</span>
              </div>
            ))}
          </div>
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
            <h3 className="mt-4 mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              평가 코드
            </h3>
            <pre className="overflow-x-auto rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-200">
              {PYTHON_PLACEHOLDER}
            </pre>
          </section>
        )}

        {evaluator.type === "judge" && (
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              Judge 모델
            </h3>
            <Badge tone="info">azure/gpt-4o</Badge>
            <h3 className="mt-4 mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              평가 프롬프트
            </h3>
            <pre className="whitespace-pre-wrap rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-200">
              {JUDGE_PROMPT_PLACEHOLDER}
            </pre>
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
        {evaluator.status === "pending" && (
          <>
            <Button variant="outline" disabled={!isAdmin}>
              반려
            </Button>
            <Button variant="primary" disabled={!isAdmin}>
              승인
            </Button>
          </>
        )}
        {evaluator.status === "approved" && (
          <Button variant="outline" disabled={!isAdmin}>
            Deprecated로 전환
          </Button>
        )}
        {evaluator.status === "deprecated" && (
          <Button variant="primary" disabled={!isAdmin}>
            재활성화
          </Button>
        )}
        {evaluator.status === "rejected" && (
          <Button variant="outline" disabled={!isAdmin}>
            재제출 요청
          </Button>
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
