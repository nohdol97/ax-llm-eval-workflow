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
  // Step 1
  name: string;
  description: string;
  promptId: string;
  promptVersions: number[];
  datasetId: string;

  // Step 2
  models: ModelConfig[];

  // Step 3
  evaluators: EvaluatorConfig[];
  normalizeWeights: boolean;
  judge: JudgeConfig;
}

export const initialWizardState: WizardState = {
  name: "",
  description: "",
  promptId: "",
  promptVersions: [],
  datasetId: "",
  models: [],
  evaluators: [],
  normalizeWeights: true,
  judge: {
    judgeModelId: "azure/gpt-4o",
    judgePrompt:
      "다음 두 응답을 0~10점으로 평가하세요. 정확성, 일관성, 형식 준수를 기준으로 합니다.",
  },
};

export function isStepValid(state: WizardState, step: number): boolean {
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
