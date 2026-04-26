import type { Model } from "@/lib/mock/types";

export interface MockResponseMeta {
  latencyMs: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
}

export interface RunHistoryEntry {
  id: string;
  promptId: string;
  promptName: string;
  promptVersion: number;
  modelId: string;
  modelName: string;
  modelProvider: string;
  variables: Record<string, string>;
  response: string;
  partial: boolean;
  meta: MockResponseMeta;
  createdAt: string;
  /** mock confidence-like score in [0,1] for badge display */
  score: number;
}

const SENTIMENT_RESPONSE = `{
  "sentiment": "positive",
  "confidence": 0.92,
  "rationale": "긍정적 키워드('최고', '추천')가 두 번 등장하고 부정 표현이 없어 신뢰도 높은 positive로 분류했습니다."
}`;

const SUMMARY_RESPONSE = `- 핵심 주장: 본문은 새로운 LLM 평가 워크플로우의 도입 배경과 효과를 설명합니다.
- 근거 데이터: 사내 4개 팀에서 평균 28% 응답 정확도 개선이 측정되었습니다.
- 향후 계획: 다음 분기에 RAG 평가 데이터셋을 200건에서 1000건으로 확장할 예정입니다.`;

const RAG_RESPONSE = `질문에 대한 답변은 다음과 같습니다.

LLM 프롬프트 평가는 정량 지표(정확도·일관성·비용)와 정성 지표(LLM-as-Judge)를 함께 사용해야 합니다. 단일 지표만으로는 회귀 검증이 불충분합니다.

[출처: prompt-eval-best-practices.md, langfuse-v3-guide.md]`;

const INTENT_RESPONSE = `purchase_intent`;

const GENERIC_RESPONSE = `요청하신 내용에 대한 응답입니다. 입력 변수와 모델 파라미터를 기반으로 결과를 생성했습니다. 추가 컨텍스트가 필요하면 system prompt 또는 변수 입력을 보강해 주세요.`;

/**
 * Choose a mock response based on prompt name keywords.
 * No external calls — purely deterministic-ish mock content.
 */
export function pickMockResponse(promptName: string): string {
  const n = promptName.toLowerCase();
  if (n.includes("sentiment")) return SENTIMENT_RESPONSE;
  if (n.includes("summary")) return SUMMARY_RESPONSE;
  if (n.includes("rag") || n.includes("qa")) return RAG_RESPONSE;
  if (n.includes("intent") || n.includes("classif")) return INTENT_RESPONSE;
  return GENERIC_RESPONSE;
}

/**
 * Generate plausible meta values for a mock run.
 * latency: 800~2000ms, output tokens: 150~400, input tokens: derived from prompt length
 */
export function generateMockMeta(
  model: Model,
  promptCharCount: number,
  outputCharCount: number
): MockResponseMeta {
  const latencyMs = Math.round(800 + Math.random() * 1200);
  // ~4 chars per token heuristic
  const inputTokens = Math.max(40, Math.round(promptCharCount / 4));
  const outputTokens = Math.max(
    150,
    Math.min(400, Math.round(outputCharCount / 4) + Math.round(Math.random() * 60))
  );
  const costUsd =
    (inputTokens / 1000) * model.inputCostPerK +
    (outputTokens / 1000) * model.outputCostPerK;
  return {
    latencyMs,
    inputTokens,
    outputTokens,
    costUsd: Number(costUsd.toFixed(4)),
  };
}

/** Random typing interval between 40 and 80 ms */
export function randomTypingIntervalMs(): number {
  return 40 + Math.round(Math.random() * 40);
}

/** Generate a fake confidence score for the run history badge (0.7~0.97) */
export function generateMockScore(): number {
  return Number((0.7 + Math.random() * 0.27).toFixed(2));
}
