import type { TraceFilter } from "@/lib/types/api";

export type WizardMode = "live" | "trace_eval";

export interface ModelConfig {
  modelId: string;
  temperature: number;
  maxTokens: number;
  expanded: boolean;
}

export interface EvaluatorConfig {
  evaluatorId: string;
  weight: number;
}

export interface JudgeConfig {
  judgeModelId: string;
  judgePrompt: string;
}

export interface WizardState {
  /** Phase 8-A: 실험 모드 (live | trace_eval). */
  mode: WizardMode;

  // Step 1 (mode=live)
  name: string;
  description: string;
  promptId: string;
  promptVersions: number[];
  datasetId: string;

  // Step 1 (mode=trace_eval) — Phase 8-A
  traceFilter: TraceFilter | null;
  expectedDatasetName: string;

  // Step 2 (mode=live only)
  models: ModelConfig[];

  // Step 3
  evaluators: EvaluatorConfig[];
  normalizeWeights: boolean;
  judge: JudgeConfig;
}

const DEFAULT_PROJECT_ID = "production-api";

export const initialWizardState: WizardState = {
  mode: "live",
  name: "",
  description: "",
  promptId: "",
  promptVersions: [],
  datasetId: "",
  traceFilter: null,
  expectedDatasetName: "",
  models: [],
  evaluators: [],
  normalizeWeights: true,
  judge: {
    judgeModelId: "azure/gpt-4o",
    judgePrompt:
      "다음 두 응답을 0~10점으로 평가하세요. 정확성, 일관성, 형식 준수를 기준으로 합니다.",
  },
};

export function defaultTraceFilter(projectId: string = DEFAULT_PROJECT_ID): TraceFilter {
  return {
    project_id: projectId,
    sample_size: 200,
    sample_strategy: "random",
  };
}

export function isStepValid(state: WizardState, step: number): boolean {
  if (state.mode === "trace_eval") {
    switch (step) {
      case 1:
        return (
          state.name.trim().length > 0 &&
          state.traceFilter !== null &&
          state.traceFilter.project_id.length > 0
        );
      case 2:
        // mode=trace_eval은 Step2(모델) 건너뛰기 — 항상 valid
        return true;
      case 3:
        return state.evaluators.length > 0;
      case 4:
        return true;
      default:
        return false;
    }
  }
  switch (step) {
    case 1:
      return (
        state.name.trim().length > 0 &&
        state.promptId !== "" &&
        state.promptVersions.length > 0 &&
        state.datasetId !== ""
      );
    case 2:
      return state.models.length > 0;
    case 3:
      return state.evaluators.length > 0;
    case 4:
      return true;
    default:
      return false;
  }
}
