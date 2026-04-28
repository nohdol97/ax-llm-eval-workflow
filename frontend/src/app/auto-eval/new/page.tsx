"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Loader2,
  Rocket,
  Tag as TagIcon,
} from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/PageHeader";
import { TraceFilterForm } from "@/app/experiments/_components/TraceFilterForm";
import { useCreateAutoEvalPolicy } from "@/lib/hooks/useAutoEval";
import type {
  AutoEvalPolicyCreate,
  AutoEvalSchedule,
  AlertThreshold,
  EvaluatorConfig,
  TraceFilter,
} from "@/lib/types/api";
import { cn } from "@/lib/utils";
import { AlertThresholdsInput } from "../_components/AlertThresholdsInput";
import { ScheduleInput } from "../_components/ScheduleInput";
import { formatSchedule } from "../_components/scheduleFormat";
import {
  WizardStepper,
  type WizardStepDef,
} from "@/app/experiments/_components/WizardStepper";

const DEFAULT_PROJECT_ID = "production-api";

const TRACE_EVALUATOR_CATALOG: Array<{
  id: string;
  name: string;
  description: string;
  range: string;
}> = [
  {
    id: "tool_called",
    name: "tool_called",
    description: "지정된 tool이 1회 이상 호출되었는지",
    range: "binary",
  },
  {
    id: "tool_call_sequence",
    name: "tool_call_sequence",
    description: "tool이 기대 순서대로 호출되었는지",
    range: "binary",
  },
  {
    id: "no_error_spans",
    name: "no_error_spans",
    description: "ERROR level observation이 0개인지",
    range: "binary",
  },
  {
    id: "tool_result_grounding",
    name: "tool_result_grounding",
    description: "최종 답변이 tool 결과에 근거하는지",
    range: "0-1",
  },
  {
    id: "factuality",
    name: "factuality",
    description: "Judge LLM이 사실성을 평가",
    range: "0-1",
  },
  {
    id: "exact_match",
    name: "exact_match",
    description: "expected_output과 정확 일치",
    range: "binary",
  },
];

const STEPS: WizardStepDef[] = [
  { id: 1, label: "기본 정보", description: "이름·설명·프로젝트" },
  { id: 2, label: "Trace 필터", description: "어떤 trace를 평가할지" },
  { id: 3, label: "Evaluators", description: "평가 함수 선택" },
  { id: 4, label: "스케줄 / 알림", description: "주기·임계값·비용" },
  { id: 5, label: "확인", description: "미리보기 및 생성" },
];

interface DraftEvaluator {
  id: string;
  weight: number;
}

interface DraftState {
  name: string;
  description: string;
  projectId: string;
  traceFilter: TraceFilter | null;
  expectedDatasetName: string;
  evaluators: DraftEvaluator[];
  schedule: AutoEvalSchedule;
  alertThresholds: AlertThreshold[];
  notificationTargets: string[];
  dailyCostLimitUsd: number | "";
}

const initialDraft: DraftState = {
  name: "",
  description: "",
  projectId: DEFAULT_PROJECT_ID,
  traceFilter: null,
  expectedDatasetName: "",
  evaluators: [],
  schedule: {
    type: "cron",
    cron_expression: "0 3 * * *",
    timezone: "Asia/Seoul",
  },
  alertThresholds: [],
  notificationTargets: [],
  dailyCostLimitUsd: 5,
};

const TRACE_EVALUATOR_NAMES = new Set(TRACE_EVALUATOR_CATALOG.map((e) => e.id));

function buildEvaluatorPayload(draft: DraftEvaluator): EvaluatorConfig {
  const meta = TRACE_EVALUATOR_CATALOG.find((e) => e.id === draft.id);
  if (meta && draft.id === "factuality") {
    return {
      type: "judge",
      name: draft.id,
      config: { judge_model: "gpt-4o" },
      weight: draft.weight,
    };
  }
  if (TRACE_EVALUATOR_NAMES.has(draft.id) && draft.id !== "exact_match") {
    return { type: "trace_builtin", name: draft.id, weight: draft.weight };
  }
  return { type: "builtin", name: draft.id, weight: draft.weight };
}

function generateIdempotencyKey(): string {
  return `policy-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function isStepValid(state: DraftState, step: number): boolean {
  switch (step) {
    case 1:
      return state.name.trim().length > 0 && state.projectId.length > 0;
    case 2:
      return state.traceFilter !== null;
    case 3:
      return state.evaluators.length > 0;
    case 4:
      return (
        (state.schedule.type !== "cron" ||
          (state.schedule.cron_expression?.trim().length ?? 0) > 0) &&
        (state.schedule.type !== "interval" ||
          (state.schedule.interval_seconds ?? 0) >= 60)
      );
    case 5:
      return true;
    default:
      return false;
  }
}

export default function NewAutoEvalPolicyPage() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [draft, setDraft] = useState<DraftState>(initialDraft);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [idempotencyKey] = useState(() => generateIdempotencyKey());
  const create = useCreateAutoEvalPolicy();

  const update = (patch: Partial<DraftState>) =>
    setDraft((prev) => ({ ...prev, ...patch }));

  const isLast = step === STEPS.length;
  const canGoNext = isStepValid(draft, step);

  const handleNext = () => {
    if (!canGoNext) return;
    if (isLast) handleSubmit();
    else setStep((s) => Math.min(STEPS.length, s + 1));
  };

  const handleBack = () => setStep((s) => Math.max(1, s - 1));

  const handleSubmit = async () => {
    setErrorMessage(null);
    try {
      if (!draft.traceFilter) {
        throw new Error("Trace 필터가 설정되지 않았습니다.");
      }
      const payload: AutoEvalPolicyCreate = {
        name: draft.name,
        description: draft.description || undefined,
        project_id: draft.projectId,
        trace_filter: draft.traceFilter,
        expected_dataset_name: draft.expectedDatasetName || undefined,
        evaluators: draft.evaluators.map(buildEvaluatorPayload),
        schedule: draft.schedule,
        alert_thresholds: draft.alertThresholds,
        notification_targets: draft.notificationTargets,
        daily_cost_limit_usd:
          draft.dailyCostLimitUsd === ""
            ? undefined
            : Number(draft.dailyCostLimitUsd),
      };
      const created = await create.mutateAsync({
        payload,
        idempotencyKey,
      });
      router.push(`/auto-eval/${encodeURIComponent(created.id)}`);
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "정책 생성에 실패했습니다.",
      );
    }
  };

  return (
    <div className="px-6 py-6">
      <PageHeader
        title="새 Auto-Eval 정책"
        description="Production trace를 자동 평가하는 정책을 생성합니다."
        actions={
          <Link
            href="/auto-eval"
            className="inline-flex h-8 items-center gap-2 rounded-md border border-zinc-700 bg-transparent px-3 text-sm text-zinc-200 hover:bg-zinc-800"
          >
            취소
          </Link>
        }
      />

      <div className="mb-6 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-4">
        <WizardStepper steps={STEPS} currentStep={step} />
      </div>

      <div className="mb-6 rounded-lg border border-zinc-800 bg-zinc-900 px-5 py-5">
        {step === 1 && <Step1 state={draft} onChange={update} />}
        {step === 2 && <Step2 state={draft} onChange={update} />}
        {step === 3 && <Step3 state={draft} onChange={update} />}
        {step === 4 && <Step4 state={draft} onChange={update} />}
        {step === 5 && <Step5 state={draft} />}
      </div>

      {errorMessage && (
        <div
          role="alert"
          className="mb-4 rounded-md border border-rose-900/40 bg-rose-950/20 px-4 py-3 text-sm text-rose-200"
        >
          {errorMessage}
        </div>
      )}

      <div className="flex items-center justify-between">
        <Button
          variant="outline"
          onClick={handleBack}
          disabled={step === 1 || create.isPending}
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />
          이전
        </Button>
        <div className="text-xs text-zinc-500">
          Step {step} / {STEPS.length}
        </div>
        <Button
          variant="primary"
          onClick={handleNext}
          disabled={!canGoNext || create.isPending}
        >
          {create.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              생성 중…
            </>
          ) : isLast ? (
            <>
              <Rocket className="h-4 w-4" aria-hidden />
              정책 생성
            </>
          ) : (
            <>
              다음
              <ArrowRight className="h-4 w-4" aria-hidden />
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

// ── Step 1 ─────────────────────────────────────────────────────────

function Step1({
  state,
  onChange,
}: {
  state: DraftState;
  onChange: (patch: Partial<DraftState>) => void;
}) {
  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <label
          htmlFor="policy-name"
          className="block text-sm font-medium text-zinc-200"
        >
          정책 이름
        </label>
        <Input
          id="policy-name"
          placeholder="예: qa-agent-v3-daily"
          value={state.name}
          onChange={(e) => onChange({ name: e.target.value })}
          required
        />
        <p className="text-[11px] text-zinc-500">
          영어/한국어 자유. 정책 목록·알림 메시지에 노출됩니다.
        </p>
      </div>

      <div className="space-y-1.5">
        <label
          htmlFor="policy-description"
          className="block text-sm font-medium text-zinc-200"
        >
          설명 (선택)
        </label>
        <Textarea
          id="policy-description"
          rows={3}
          placeholder="이 정책의 목적·범위를 한 두 줄로 정리"
          value={state.description}
          onChange={(e) => onChange({ description: e.target.value })}
        />
      </div>

      <div className="space-y-1.5">
        <label
          htmlFor="policy-project"
          className="block text-sm font-medium text-zinc-200"
        >
          프로젝트
        </label>
        <Input
          id="policy-project"
          value={state.projectId}
          onChange={(e) => onChange({ projectId: e.target.value })}
        />
      </div>
    </div>
  );
}

// ── Step 2 ─────────────────────────────────────────────────────────

function Step2({
  state,
  onChange,
}: {
  state: DraftState;
  onChange: (patch: Partial<DraftState>) => void;
}) {
  return (
    <div className="space-y-5">
      <TraceFilterForm
        value={state.traceFilter}
        onChange={(filter) => onChange({ traceFilter: filter })}
        projectId={state.projectId}
      />
      <div className="space-y-1.5">
        <label
          htmlFor="policy-expected-dataset"
          className="block text-sm font-medium text-zinc-200"
        >
          Expected dataset (선택)
        </label>
        <Input
          id="policy-expected-dataset"
          placeholder="예: rag-eval-200"
          value={state.expectedDatasetName}
          onChange={(e) => onChange({ expectedDatasetName: e.target.value })}
        />
        <p className="text-[11px] text-zinc-500">
          평가에 정답이 필요한 evaluator를 사용할 때만 지정합니다.
        </p>
      </div>
    </div>
  );
}

// ── Step 3 ─────────────────────────────────────────────────────────

function Step3({
  state,
  onChange,
}: {
  state: DraftState;
  onChange: (patch: Partial<DraftState>) => void;
}) {
  const toggle = (id: string) => {
    const exists = state.evaluators.find((e) => e.id === id);
    let next: DraftEvaluator[];
    if (exists) {
      next = state.evaluators.filter((e) => e.id !== id);
    } else {
      const equalWeight = 1 / (state.evaluators.length + 1);
      next = [
        ...state.evaluators.map((e) => ({ ...e, weight: equalWeight })),
        { id, weight: equalWeight },
      ];
    }
    onChange({ evaluators: next });
  };

  const updateWeight = (id: string, weight: number) => {
    onChange({
      evaluators: state.evaluators.map((e) =>
        e.id === id ? { ...e, weight } : e,
      ),
    });
  };

  const total = state.evaluators.reduce((s, e) => s + e.weight, 0);

  return (
    <div className="space-y-4">
      <p className="text-sm text-zinc-300">
        평가에 사용할 함수를 선택하세요. 가중치 합은 자동으로 1.0이 되도록
        조정됩니다.
      </p>
      <div className="grid grid-cols-1 gap-2">
        {TRACE_EVALUATOR_CATALOG.map((ev) => {
          const cfg = state.evaluators.find((e) => e.id === ev.id);
          const checked = !!cfg;
          return (
            <label
              key={ev.id}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded-md border bg-zinc-900 px-3 py-2.5 transition-colors",
                checked
                  ? "border-indigo-500/50 bg-indigo-950/20"
                  : "border-zinc-800",
              )}
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggle(ev.id)}
                className="mt-1 h-4 w-4 rounded border-zinc-600 bg-zinc-800 accent-indigo-500"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-zinc-100">{ev.name}</span>
                  <span className="rounded-full border border-zinc-800 bg-zinc-900 px-1.5 py-0.5 text-[10px] text-zinc-400">
                    {ev.range}
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] text-zinc-500">
                  {ev.description}
                </p>
              </div>
              {checked && cfg && (
                <Input
                  type="number"
                  step={0.05}
                  min={0}
                  max={1}
                  value={cfg.weight.toFixed(3)}
                  onClick={(e) => e.preventDefault()}
                  onChange={(e) => updateWeight(ev.id, Number(e.target.value))}
                  className="h-7 w-20 text-right"
                  aria-label={`${ev.name} 가중치`}
                />
              )}
            </label>
          );
        })}
      </div>
      <div className="rounded-md border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-xs text-zinc-300">
        가중치 합 ·{" "}
        <span
          className={cn(
            "font-mono tabular-nums",
            Math.abs(total - 1) < 0.01 ? "text-emerald-300" : "text-amber-300",
          )}
        >
          {total.toFixed(3)}
        </span>
      </div>
    </div>
  );
}

// ── Step 4 ─────────────────────────────────────────────────────────

function Step4({
  state,
  onChange,
}: {
  state: DraftState;
  onChange: (patch: Partial<DraftState>) => void;
}) {
  return (
    <div className="space-y-4">
      <ScheduleInput
        value={state.schedule}
        onChange={(next) => onChange({ schedule: next })}
      />
      <AlertThresholdsInput
        value={state.alertThresholds}
        onChange={(next) => onChange({ alertThresholds: next })}
      />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label
            htmlFor="policy-cost-limit"
            className="block text-sm font-medium text-zinc-200"
          >
            일일 비용 한도 (USD)
          </label>
          <Input
            id="policy-cost-limit"
            type="number"
            step={0.5}
            min={0}
            value={state.dailyCostLimitUsd}
            onChange={(e) =>
              onChange({
                dailyCostLimitUsd:
                  e.target.value === "" ? "" : Number(e.target.value),
              })
            }
          />
          <p className="text-[11px] text-zinc-500">
            한도 초과 시 자동 일시정지 + 알림.
          </p>
        </div>
        <div className="space-y-1.5">
          <label
            htmlFor="policy-targets"
            className="block text-sm font-medium text-zinc-200"
          >
            알림 수신자 (user_id, 콤마 구분)
          </label>
          <Input
            id="policy-targets"
            placeholder="user_1, user_2"
            value={state.notificationTargets.join(", ")}
            onChange={(e) =>
              onChange({
                notificationTargets: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter((s) => s.length > 0),
              })
            }
          />
        </div>
      </div>
    </div>
  );
}

// ── Step 5 ─────────────────────────────────────────────────────────

function Step5({ state }: { state: DraftState }) {
  return (
    <div className="space-y-4 text-sm">
      <SummaryRow label="이름" value={state.name} />
      {state.description && (
        <SummaryRow label="설명" value={state.description} />
      )}
      <SummaryRow label="프로젝트" value={state.projectId} />
      <SummaryRow
        label="Agent 이름"
        value={state.traceFilter?.name ?? "(전체)"}
      />
      <SummaryRow
        label="태그"
        value={
          state.traceFilter?.tags?.length
            ? state.traceFilter.tags.join(", ")
            : "—"
        }
        icon={<TagIcon className="h-3.5 w-3.5" />}
      />
      <SummaryRow
        label="샘플"
        value={`${state.traceFilter?.sample_size ?? "전체"} (${state.traceFilter?.sample_strategy ?? "—"})`}
      />
      <SummaryRow
        label="Evaluators"
        value={state.evaluators
          .map((e) => `${e.id}@${e.weight.toFixed(2)}`)
          .join(", ")}
      />
      <SummaryRow label="스케줄" value={formatSchedule(state.schedule)} />
      <SummaryRow
        label="알림 조건"
        value={
          state.alertThresholds.length === 0
            ? "—"
            : state.alertThresholds
                .map(
                  (t) =>
                    `${t.metric} ${t.operator} ${t.value}` +
                    (t.evaluator_name ? ` (${t.evaluator_name})` : ""),
                )
                .join(" · ")
        }
      />
      <SummaryRow
        label="일일 한도"
        value={
          state.dailyCostLimitUsd === ""
            ? "—"
            : `$${Number(state.dailyCostLimitUsd).toFixed(2)}`
        }
      />
      <SummaryRow
        label="알림 수신자"
        value={
          state.notificationTargets.length === 0
            ? "—"
            : state.notificationTargets.join(", ")
        }
      />
    </div>
  );
}

function SummaryRow({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[140px_1fr] items-baseline gap-3 border-b border-zinc-900 pb-2 last:border-b-0">
      <dt className="inline-flex items-center gap-1.5 text-xs uppercase tracking-wide text-zinc-500">
        {icon}
        {label}
      </dt>
      <dd className="text-sm text-zinc-100">{value}</dd>
    </div>
  );
}
