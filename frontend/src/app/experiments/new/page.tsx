"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, ArrowRight, Loader2, Rocket } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { useCreateExperiment } from "@/lib/hooks/useExperiments";
import type {
  EvaluatorConfig as ApiEvaluatorConfig,
  ExperimentCreate,
  ModelConfigItem,
  PromptConfigItem,
} from "@/lib/types/api";
import {
  WizardStepper,
  type WizardStepDef,
} from "../_components/WizardStepper";
import { WizardStep1 } from "../_components/WizardStep1";
import { WizardStep2 } from "../_components/WizardStep2";
import { WizardStep3 } from "../_components/WizardStep3";
import { WizardStep4 } from "../_components/WizardStep4";
import {
  initialWizardState,
  isStepValid,
  type WizardState,
} from "../_components/wizardState";

const DEFAULT_PROJECT_ID = "production-api";

const STEPS: WizardStepDef[] = [
  { id: 1, label: "기본 설정", description: "실험명 · 프롬프트 · 데이터셋" },
  { id: 2, label: "모델 선택", description: "비교할 모델과 파라미터" },
  { id: 3, label: "평가 설정", description: "평가 함수와 가중치" },
  { id: 4, label: "확인", description: "예상 비용·실행 검토" },
];

function generateIdempotencyKey(): string {
  return `exp-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function buildEvaluatorConfig(
  evaluatorId: string,
  weight: number
): ApiEvaluatorConfig {
  // Heuristic: built-in evaluators have short snake_case names; custom evaluators
  // come back as submission ids (e.g., uuids or `sub_*`). The Phase 7-A backend
  // accepts either `type: "builtin"` with `name` or `type: "custom_code"` with
  // `submission_id`.
  const looksLikeSubmission =
    evaluatorId.includes("-") && evaluatorId.length >= 20;
  if (looksLikeSubmission) {
    return {
      type: "custom_code",
      submission_id: evaluatorId,
      weight,
    };
  }
  if (evaluatorId.includes("judge")) {
    return { type: "llm_judge", name: evaluatorId, weight };
  }
  return { type: "builtin", name: evaluatorId, weight };
}

export default function NewExperimentPage() {
  const router = useRouter();
  const projectId = DEFAULT_PROJECT_ID;

  const [step, setStep] = useState(1);
  const [state, setState] = useState<WizardState>(initialWizardState);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [idempotencyKey] = useState<string>(() => generateIdempotencyKey());

  const createExperiment = useCreateExperiment();

  const updateState = (patch: Partial<WizardState>) => {
    setState((prev) => ({ ...prev, ...patch }));
  };

  const canGoNext = isStepValid(state, step);
  const isLast = step === STEPS.length;
  const isStarting = createExperiment.isPending;

  const handleNext = () => {
    if (!canGoNext) return;
    if (isLast) {
      handleStart();
    } else {
      setStep((s) => Math.min(STEPS.length, s + 1));
    }
  };

  const handleBack = () => {
    setStep((s) => Math.max(1, s - 1));
  };

  const handleStart = async () => {
    setErrorMessage(null);
    try {
      const promptConfigs: PromptConfigItem[] = state.promptVersions.map(
        (v) => ({
          name: state.promptId,
          version: v,
        })
      );
      const modelConfigs: ModelConfigItem[] = state.models.map((m) => ({
        model: m.modelId,
        parameters: {
          temperature: m.temperature,
          max_tokens: m.maxTokens,
        },
      }));
      const evaluators: ApiEvaluatorConfig[] = state.evaluators.map((e) =>
        buildEvaluatorConfig(e.evaluatorId, e.weight)
      );

      const payload: ExperimentCreate = {
        project_id: projectId,
        name: state.name,
        description: state.description || undefined,
        prompt_configs: promptConfigs,
        dataset_name: state.datasetId,
        model_configs: modelConfigs,
        evaluators,
      };

      const created = await createExperiment.mutateAsync({
        payload,
        idempotencyKey,
      });
      router.push(`/experiments/${created.experiment_id}`);
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "실험 생성에 실패했습니다"
      );
    }
  };

  return (
    <div className="px-6 py-6">
      <PageHeader
        title="새 실험 생성"
        description="프롬프트 버전 × 모델 매트릭스를 평가 함수로 비교합니다."
        actions={
          <Link
            href="/experiments"
            className="inline-flex h-8 items-center gap-2 rounded-md border border-zinc-700 bg-transparent px-3 text-sm text-zinc-200 transition-colors hover:bg-zinc-800"
          >
            취소
          </Link>
        }
      />

      <div className="mb-6 rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-4">
        <WizardStepper steps={STEPS} currentStep={step} />
      </div>

      <div className="sr-only" role="status" aria-live="polite">
        {STEPS[step - 1].label} 단계 (Step {step} / {STEPS.length})
      </div>

      <div className="mb-6 rounded-lg border border-zinc-800 bg-zinc-900 px-5 py-5">
        <AnimatePresence mode="wait">
          <motion.div
            key={step}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
          >
            {step === 1 && (
              <WizardStep1 state={state} onChange={updateState} />
            )}
            {step === 2 && (
              <WizardStep2 state={state} onChange={updateState} />
            )}
            {step === 3 && (
              <WizardStep3 state={state} onChange={updateState} />
            )}
            {step === 4 && <WizardStep4 state={state} />}
          </motion.div>
        </AnimatePresence>
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
          disabled={step === 1 || isStarting}
          aria-label="이전 단계"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden />이전
        </Button>

        <div className="text-xs text-zinc-500">
          Step {step} / {STEPS.length}
        </div>

        <Button
          variant="primary"
          onClick={handleNext}
          disabled={!canGoNext || isStarting}
          aria-label={isLast ? "실험 시작" : "다음 단계"}
        >
          {isStarting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              생성 중…
            </>
          ) : isLast ? (
            <>
              <Rocket className="h-4 w-4" aria-hidden />
              실험 시작
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
