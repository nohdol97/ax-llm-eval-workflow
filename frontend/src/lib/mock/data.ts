import type {
  ConnectionHealth,
  Dataset,
  DatasetItem,
  Evaluator,
  Experiment,
  ItemResult,
  Model,
  Notification,
  Project,
  Prompt,
  Run,
  User,
} from "./types";
import type {
  AutoEvalPolicy,
  AutoEvalRun,
  CostUsage,
  TraceTree,
} from "../types/api";

export const currentUser: User = {
  id: "user_1",
  name: "노동훈",
  email: "hs97.noh@samsung.com",
  role: "admin",
  initials: "노",
};

export const projects: Project[] = [
  { id: "production-api", name: "production-api", description: "프로덕션 API 프롬프트" },
  { id: "research-lab", name: "research-lab", description: "리서치 실험 워크스페이스" },
  { id: "staging", name: "staging", description: "스테이징 검증" },
];

export const currentProject = projects[0];

export const models: Model[] = [
  {
    id: "azure/gpt-4o",
    name: "GPT-4o",
    provider: "azure",
    vision: true,
    contextWindow: 128_000,
    inputCostPerK: 0.0025,
    outputCostPerK: 0.01,
  },
  {
    id: "azure/gpt-4.1",
    name: "GPT-4.1",
    provider: "azure",
    vision: true,
    contextWindow: 1_000_000,
    inputCostPerK: 0.002,
    outputCostPerK: 0.008,
  },
  {
    id: "openai/o4-mini",
    name: "o4-mini",
    provider: "openai",
    vision: false,
    contextWindow: 200_000,
    inputCostPerK: 0.003,
    outputCostPerK: 0.012,
  },
  {
    id: "google/gemini-2.5-pro",
    name: "Gemini 2.5 Pro",
    provider: "google",
    vision: true,
    contextWindow: 2_000_000,
    inputCostPerK: 0.00125,
    outputCostPerK: 0.005,
  },
  {
    id: "google/gemini-2.5-flash",
    name: "Gemini 2.5 Flash",
    provider: "google",
    vision: true,
    contextWindow: 1_000_000,
    inputCostPerK: 0.00035,
    outputCostPerK: 0.00105,
  },
  {
    id: "anthropic/claude-4-6-opus",
    name: "Claude 4.6 Opus",
    provider: "anthropic",
    vision: true,
    contextWindow: 200_000,
    inputCostPerK: 0.015,
    outputCostPerK: 0.075,
  },
  {
    id: "anthropic/claude-4-5-sonnet",
    name: "Claude 4.5 Sonnet",
    provider: "anthropic",
    vision: true,
    contextWindow: 200_000,
    inputCostPerK: 0.003,
    outputCostPerK: 0.015,
  },
  {
    id: "bedrock/llama-3.3-70b",
    name: "Llama 3.3 70B",
    provider: "bedrock",
    vision: false,
    contextWindow: 128_000,
    inputCostPerK: 0.00072,
    outputCostPerK: 0.00072,
  },
];

export const providerLabels: Record<string, string> = {
  azure: "Azure OpenAI",
  openai: "OpenAI",
  google: "Google",
  anthropic: "Anthropic",
  bedrock: "AWS Bedrock",
};

export const prompts: Prompt[] = [
  {
    id: "prompt_sentiment",
    name: "sentiment-analysis",
    latestVersion: 4,
    labels: ["production"],
    lastUsed: "2026-04-25T08:21:00+09:00",
    usageCount: 134,
    description: "한국어/영어 텍스트 감성 분류 (positive/neutral/negative + confidence)",
    versions: [
      {
        version: 4,
        body: `당신은 텍스트의 감성을 분석하는 전문가입니다.

다음 규칙에 따라 분석하세요:
{{analysis_rules}}

분석할 텍스트:
{{input_text}}

결과를 다음 JSON 형식으로 반환하세요:
{
  "sentiment": "positive" | "neutral" | "negative",
  "confidence": 0.0 ~ 1.0,
  "rationale": "한 문장 설명"
}`,
        systemPrompt: "당신은 정확하고 일관된 감성 분석 결과를 반환합니다.",
        variables: ["analysis_rules", "input_text"],
        createdAt: "2026-04-22T10:00:00+09:00",
        author: "노동훈",
      },
      {
        version: 3,
        body: `텍스트의 감성을 분석하세요.\n\n{{input_text}}`,
        variables: ["input_text"],
        createdAt: "2026-04-10T10:00:00+09:00",
        author: "노동훈",
      },
    ],
  },
  {
    id: "prompt_summary",
    name: "summary-generator",
    latestVersion: 2,
    labels: ["staging"],
    lastUsed: "2026-04-23T14:00:00+09:00",
    usageCount: 58,
    description: "장문 → 3-bullet 요약, 출력 토큰 200 이하 제약",
    versions: [
      {
        version: 2,
        body: `다음 글을 3개 bullet point로 요약하세요. 각 bullet은 60자 이내.\n\n{{document}}`,
        variables: ["document"],
        createdAt: "2026-04-20T10:00:00+09:00",
        author: "노동훈",
      },
      {
        version: 1,
        body: `요약:\n{{document}}`,
        variables: ["document"],
        createdAt: "2026-03-30T10:00:00+09:00",
        author: "노동훈",
      },
    ],
  },
  {
    id: "prompt_rag_qa",
    name: "rag-qa-prompt",
    latestVersion: 7,
    labels: ["production"],
    lastUsed: "2026-04-26T08:00:00+09:00",
    usageCount: 312,
    description: "검색 결과 컨텍스트 기반 QA — 출처 포맷 강제",
    versions: [
      {
        version: 7,
        body: `다음 컨텍스트를 바탕으로 질문에 답하세요. 컨텍스트에 없는 정보는 "모릅니다"로 답하세요.\n\n# 컨텍스트\n{{context}}\n\n# 질문\n{{question}}\n\n# 답변 형식\n답변과 함께 [출처: 문서명] 표기 필수.`,
        variables: ["context", "question"],
        createdAt: "2026-04-26T07:00:00+09:00",
        author: "노동훈",
      },
    ],
  },
  {
    id: "prompt_classification",
    name: "intent-classifier",
    latestVersion: 1,
    labels: ["draft"],
    lastUsed: "2026-04-15T09:00:00+09:00",
    usageCount: 12,
    description: "사용자 발화 → intent 라벨 분류 (총 18개)",
    versions: [
      {
        version: 1,
        body: `다음 발화를 intent로 분류하세요.\n\n사용 가능한 intent: {{intent_list}}\n\n발화: {{utterance}}\n\n응답 형식: intent 라벨만 (소문자 snake_case)`,
        variables: ["intent_list", "utterance"],
        createdAt: "2026-04-12T10:00:00+09:00",
        author: "노동훈",
      },
    ],
  },
];

export const datasets: Dataset[] = [
  {
    id: "ds_sentiment_golden_100",
    name: "sentiment-golden-100",
    description: "감성 분류 골든셋 — 한국어 80건, 영어 20건",
    itemCount: 100,
    createdAt: "2026-03-12T10:00:00+09:00",
    lastUsed: "2026-04-25T11:00:00+09:00",
    recentExperimentCount: 12,
  },
  {
    id: "ds_summary_test_50",
    name: "summary-test-50",
    description: "뉴스 기사 50건 + 사람이 작성한 3-bullet 정답",
    itemCount: 50,
    createdAt: "2026-04-01T10:00:00+09:00",
    lastUsed: "2026-04-22T11:00:00+09:00",
    recentExperimentCount: 4,
  },
  {
    id: "ds_rag_eval_200",
    name: "rag-eval-200",
    description: "RAG 답변 정확도 평가셋 (질문 + 정답 + 출처 문서명)",
    itemCount: 200,
    createdAt: "2026-04-04T10:00:00+09:00",
    lastUsed: "2026-04-26T07:30:00+09:00",
    recentExperimentCount: 8,
  },
  {
    id: "ds_intent_300",
    name: "intent-classifier-golden-300",
    description: "발화 → intent 라벨 (300건)",
    itemCount: 300,
    createdAt: "2026-04-10T10:00:00+09:00",
    lastUsed: "2026-04-15T09:00:00+09:00",
    recentExperimentCount: 1,
  },
  {
    id: "ds_sample_10",
    name: "sample-golden-10",
    description: "온보딩 샘플 — 빠른 시작용",
    itemCount: 10,
    createdAt: "2026-02-01T10:00:00+09:00",
    recentExperimentCount: 0,
  },
];

export const datasetItems: Record<string, DatasetItem[]> = {
  ds_sentiment_golden_100: [
    {
      id: "item_1",
      input: { text: "이 제품 정말 최고예요. 추천합니다!" },
      expectedOutput: "positive",
      metadata: { language: "ko", domain: "review" },
    },
    {
      id: "item_2",
      input: { text: "배송이 너무 늦어서 화가 나네요." },
      expectedOutput: "negative",
      metadata: { language: "ko", domain: "review" },
    },
    {
      id: "item_3",
      input: { text: "그냥 평범한 수준입니다." },
      expectedOutput: "neutral",
      metadata: { language: "ko", domain: "review" },
    },
    {
      id: "item_4",
      input: { text: "Worst experience ever." },
      expectedOutput: "negative",
      metadata: { language: "en", domain: "review" },
    },
    {
      id: "item_5",
      input: { text: "Amazing quality, will buy again." },
      expectedOutput: "positive",
      metadata: { language: "en", domain: "review" },
    },
  ],
};

export const experiments: Experiment[] = [
  {
    id: "exp_001",
    name: "감성분석 v3 vs v4 (4 모델)",
    description: "production 승격 전 회귀 검증",
    status: "completed",
    promptId: "prompt_sentiment",
    promptName: "sentiment-analysis",
    promptVersions: [3, 4],
    datasetId: "ds_sentiment_golden_100",
    datasetName: "sentiment-golden-100",
    modelIds: ["azure/gpt-4o", "google/gemini-2.5-pro", "anthropic/claude-4-5-sonnet", "google/gemini-2.5-flash"],
    evaluatorIds: ["exact_match", "llm_judge_consistency"],
    itemCount: 100,
    runCount: 8,
    completedRuns: 8,
    totalCostUsd: 5.67,
    avgScore: 0.88,
    avgLatencyMs: 1250,
    createdAt: "2026-04-25T10:00:00+09:00",
    startedAt: "2026-04-25T10:01:00+09:00",
    completedAt: "2026-04-25T10:18:00+09:00",
    owner: "노동훈",
  },
  {
    id: "exp_002",
    name: "요약 품질 회귀 (Sonnet vs Flash)",
    status: "running",
    promptId: "prompt_summary",
    promptName: "summary-generator",
    promptVersions: [2],
    datasetId: "ds_summary_test_50",
    datasetName: "summary-test-50",
    modelIds: ["anthropic/claude-4-5-sonnet", "google/gemini-2.5-flash"],
    evaluatorIds: ["rouge", "llm_judge_quality"],
    itemCount: 50,
    runCount: 2,
    completedRuns: 1,
    totalCostUsd: 1.23,
    avgScore: 0.84,
    avgLatencyMs: 980,
    createdAt: "2026-04-26T21:30:00+09:00",
    startedAt: "2026-04-26T21:31:00+09:00",
    owner: "노동훈",
  },
  {
    id: "exp_003",
    name: "RAG 정확도 벤치마크",
    status: "completed",
    promptId: "prompt_rag_qa",
    promptName: "rag-qa-prompt",
    promptVersions: [6, 7],
    datasetId: "ds_rag_eval_200",
    datasetName: "rag-eval-200",
    modelIds: [
      "azure/gpt-4o",
      "azure/gpt-4.1",
      "google/gemini-2.5-pro",
      "anthropic/claude-4-6-opus",
    ],
    evaluatorIds: ["llm_judge_factuality", "json_schema_match", "exact_match"],
    itemCount: 200,
    runCount: 8,
    completedRuns: 8,
    totalCostUsd: 12.4,
    avgScore: 0.79,
    avgLatencyMs: 2100,
    createdAt: "2026-04-24T09:00:00+09:00",
    startedAt: "2026-04-24T09:01:00+09:00",
    completedAt: "2026-04-24T09:42:00+09:00",
    owner: "노동훈",
  },
  {
    id: "exp_004",
    name: "intent 분류 초기 베이스라인",
    status: "failed",
    promptId: "prompt_classification",
    promptName: "intent-classifier",
    promptVersions: [1],
    datasetId: "ds_intent_300",
    datasetName: "intent-classifier-golden-300",
    modelIds: ["google/gemini-2.5-flash"],
    evaluatorIds: ["exact_match"],
    itemCount: 300,
    runCount: 1,
    completedRuns: 0,
    totalCostUsd: 0.05,
    avgScore: null,
    avgLatencyMs: null,
    createdAt: "2026-04-15T09:00:00+09:00",
    startedAt: "2026-04-15T09:01:00+09:00",
    completedAt: "2026-04-15T09:03:00+09:00",
    owner: "노동훈",
  },
  {
    id: "exp_005",
    name: "Claude Opus 비용 검증",
    status: "paused",
    promptId: "prompt_rag_qa",
    promptName: "rag-qa-prompt",
    promptVersions: [7],
    datasetId: "ds_rag_eval_200",
    datasetName: "rag-eval-200",
    modelIds: ["anthropic/claude-4-6-opus"],
    evaluatorIds: ["llm_judge_factuality"],
    itemCount: 200,
    runCount: 1,
    completedRuns: 0,
    totalCostUsd: 0.85,
    avgScore: 0.91,
    avgLatencyMs: 2350,
    createdAt: "2026-04-26T20:10:00+09:00",
    startedAt: "2026-04-26T20:11:00+09:00",
    owner: "노동훈",
  },
];

export const runsByExperiment: Record<string, Run[]> = {
  exp_001: [
    {
      id: "run_001_a",
      experimentId: "exp_001",
      promptVersion: 3,
      modelId: "azure/gpt-4o",
      modelName: "GPT-4o",
      status: "completed",
      itemsCompleted: 100,
      itemsTotal: 100,
      avgScore: 0.87,
      avgLatencyMs: 1180,
      totalCostUsd: 1.42,
      totalInputTokens: 18_400,
      totalOutputTokens: 9_200,
      scoresByEvaluator: { exact_match: 0.85, llm_judge_consistency: 0.89 },
    },
    {
      id: "run_001_b",
      experimentId: "exp_001",
      promptVersion: 4,
      modelId: "azure/gpt-4o",
      modelName: "GPT-4o",
      status: "completed",
      itemsCompleted: 100,
      itemsTotal: 100,
      avgScore: 0.92,
      avgLatencyMs: 1230,
      totalCostUsd: 1.51,
      totalInputTokens: 19_100,
      totalOutputTokens: 9_500,
      scoresByEvaluator: { exact_match: 0.91, llm_judge_consistency: 0.93 },
    },
    {
      id: "run_001_c",
      experimentId: "exp_001",
      promptVersion: 4,
      modelId: "google/gemini-2.5-pro",
      modelName: "Gemini 2.5 Pro",
      status: "completed",
      itemsCompleted: 100,
      itemsTotal: 100,
      avgScore: 0.88,
      avgLatencyMs: 1410,
      totalCostUsd: 0.95,
      totalInputTokens: 19_100,
      totalOutputTokens: 9_300,
      scoresByEvaluator: { exact_match: 0.86, llm_judge_consistency: 0.9 },
    },
    {
      id: "run_001_d",
      experimentId: "exp_001",
      promptVersion: 4,
      modelId: "google/gemini-2.5-flash",
      modelName: "Gemini 2.5 Flash",
      status: "completed",
      itemsCompleted: 100,
      itemsTotal: 100,
      avgScore: 0.81,
      avgLatencyMs: 820,
      totalCostUsd: 0.28,
      totalInputTokens: 19_100,
      totalOutputTokens: 9_400,
      scoresByEvaluator: { exact_match: 0.79, llm_judge_consistency: 0.83 },
    },
    {
      id: "run_001_e",
      experimentId: "exp_001",
      promptVersion: 4,
      modelId: "anthropic/claude-4-5-sonnet",
      modelName: "Claude 4.5 Sonnet",
      status: "completed",
      itemsCompleted: 100,
      itemsTotal: 100,
      avgScore: 0.9,
      avgLatencyMs: 1480,
      totalCostUsd: 1.51,
      totalInputTokens: 19_100,
      totalOutputTokens: 9_400,
      scoresByEvaluator: { exact_match: 0.88, llm_judge_consistency: 0.92 },
    },
  ],
  exp_002: [
    {
      id: "run_002_a",
      experimentId: "exp_002",
      promptVersion: 2,
      modelId: "anthropic/claude-4-5-sonnet",
      modelName: "Claude 4.5 Sonnet",
      status: "completed",
      itemsCompleted: 50,
      itemsTotal: 50,
      avgScore: 0.86,
      avgLatencyMs: 1050,
      totalCostUsd: 0.83,
      totalInputTokens: 9_200,
      totalOutputTokens: 4_100,
      scoresByEvaluator: { rouge: 0.78, llm_judge_quality: 0.94 },
    },
    {
      id: "run_002_b",
      experimentId: "exp_002",
      promptVersion: 2,
      modelId: "google/gemini-2.5-flash",
      modelName: "Gemini 2.5 Flash",
      status: "running",
      itemsCompleted: 22,
      itemsTotal: 50,
      avgScore: 0.81,
      avgLatencyMs: 690,
      totalCostUsd: 0.13,
      totalInputTokens: 4_100,
      totalOutputTokens: 1_800,
      scoresByEvaluator: { rouge: 0.74, llm_judge_quality: 0.88 },
    },
  ],
};

const seedRandom = (seed: number) => {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
};

export function generateItemResults(experimentId: string): ItemResult[] {
  const runs = runsByExperiment[experimentId] || [];
  if (runs.length === 0) return [];
  const exp = experiments.find((e) => e.id === experimentId);
  const itemCount = Math.min(exp?.itemCount ?? 30, 30);
  const baseInputs = [
    "이 제품 정말 최고예요. 추천합니다!",
    "배송이 너무 늦어서 화가 나네요.",
    "그냥 평범한 수준입니다.",
    "Worst experience ever.",
    "Amazing quality, will buy again.",
    "디자인은 마음에 드는데 가격이 비싸요.",
    "기대보다 훨씬 좋네요. 강추!",
    "환불 절차가 너무 복잡합니다.",
    "포장이 깔끔해서 좋았어요.",
    "다음에는 다른 브랜드 살게요.",
  ];
  const expecteds = ["positive", "negative", "neutral", "negative", "positive"];

  const rand = seedRandom(experimentId.length * 17 + 3);

  return Array.from({ length: itemCount }).map((_, i) => {
    const outputs: Record<string, string> = {};
    const scoresByRun: Record<string, number | null> = {};
    const latenciesByRun: Record<string, number> = {};
    const costsByRun: Record<string, number> = {};
    runs.forEach((r) => {
      const baseScore = r.avgScore ?? 0.7;
      const noise = (rand() - 0.5) * 0.4;
      const score = Math.max(0, Math.min(1, baseScore + noise));
      scoresByRun[r.id] = rand() < 0.04 ? null : Number(score.toFixed(2));
      outputs[r.id] = score > 0.7 ? expecteds[i % expecteds.length] : "neutral";
      latenciesByRun[r.id] = Math.round((r.avgLatencyMs ?? 1000) * (0.7 + rand() * 0.6));
      costsByRun[r.id] = Number(((r.totalCostUsd / r.itemsTotal) * (0.7 + rand() * 0.6)).toFixed(4));
    });
    return {
      itemId: `item_${i + 1}`,
      itemIndex: i + 1,
      input: baseInputs[i % baseInputs.length],
      expected: expecteds[i % expecteds.length],
      outputs,
      scoresByRun,
      latenciesByRun,
      costsByRun,
    };
  });
}

export const evaluators: Evaluator[] = [
  {
    id: "exact_match",
    name: "exact_match",
    type: "builtin",
    status: "approved",
    description: "출력과 기대값의 정확 일치 (대소문자/공백 무시 옵션)",
    range: "binary",
    usageCount: 412,
  },
  {
    id: "contains",
    name: "contains",
    type: "builtin",
    status: "approved",
    description: "출력에 기대 키워드 포함 여부",
    range: "binary",
    usageCount: 130,
  },
  {
    id: "regex_match",
    name: "regex_match",
    type: "builtin",
    status: "approved",
    description: "출력이 정규표현식 패턴에 매칭",
    range: "binary",
    usageCount: 88,
  },
  {
    id: "json_validity",
    name: "json_validity",
    type: "builtin",
    status: "approved",
    description: "출력이 유효한 JSON인지 검증",
    range: "binary",
    usageCount: 211,
  },
  {
    id: "json_schema_match",
    name: "json_schema_match",
    type: "builtin",
    status: "approved",
    description: "출력이 지정 JSON 스키마를 따르는지",
    range: "binary",
    usageCount: 102,
  },
  {
    id: "rouge",
    name: "rouge",
    type: "builtin",
    status: "approved",
    description: "ROUGE-L 스코어 (요약 평가)",
    range: "0-1",
    usageCount: 64,
  },
  {
    id: "levenshtein_similarity",
    name: "levenshtein_similarity",
    type: "builtin",
    status: "approved",
    description: "편집 거리 기반 유사도",
    range: "0-1",
    usageCount: 51,
  },
  {
    id: "llm_judge_consistency",
    name: "llm-judge / consistency",
    type: "judge",
    status: "approved",
    description: "GPT-4o가 두 응답의 일관성을 0~10으로 평가",
    range: "0-10",
    usageCount: 320,
  },
  {
    id: "llm_judge_factuality",
    name: "llm-judge / factuality",
    type: "judge",
    status: "approved",
    description: "근거 문서 기반 사실성 평가 (Judge: GPT-4o)",
    range: "0-10",
    usageCount: 148,
  },
  {
    id: "llm_judge_quality",
    name: "llm-judge / summary-quality",
    type: "judge",
    status: "approved",
    description: "요약문 품질 평가 (정확성·완결성·간결성)",
    range: "0-10",
    usageCount: 72,
  },
  {
    id: "custom_pii_check",
    name: "pii_leakage_check",
    type: "custom",
    status: "approved",
    description: "출력에 PII(주민번호/전화번호 등) 패턴 누출이 있는지 검사",
    range: "binary",
    submittedBy: "노동훈",
    submittedAt: "2026-03-20T10:00:00+09:00",
    approvedBy: "운영팀",
    approvedAt: "2026-03-22T14:00:00+09:00",
    usageCount: 41,
  },
  {
    id: "custom_korean_morph",
    name: "korean_morph_match",
    type: "custom",
    status: "pending",
    description: "한국어 형태소 단위 일치율 계산 (조사·어미 무시)",
    range: "0-1",
    submittedBy: "리서치 인턴",
    submittedAt: "2026-04-25T11:00:00+09:00",
    usageCount: 0,
  },
  {
    id: "custom_brand_voice",
    name: "brand_voice_consistency",
    type: "custom",
    status: "pending",
    description: "사내 브랜드 보이스 가이드라인 일치율 (키워드 + 패턴 검사)",
    range: "0-1",
    submittedBy: "디자인팀",
    submittedAt: "2026-04-26T09:00:00+09:00",
    usageCount: 0,
  },
  {
    id: "custom_old_regex",
    name: "legacy_regex_check",
    type: "custom",
    status: "deprecated",
    description: "구 버전 정규식 검사 — schema 변경으로 deprecated",
    range: "binary",
    submittedBy: "노동훈",
    submittedAt: "2026-01-10T10:00:00+09:00",
    approvedBy: "운영팀",
    approvedAt: "2026-01-12T10:00:00+09:00",
    usageCount: 8,
  },
];

export const notifications: Notification[] = [
  {
    id: "n_1",
    type: "experiment_complete",
    title: "감성분석 v3 vs v4 실험 완료",
    body: "8개 Run 완료 · 평균 스코어 0.88 · 총 비용 $5.67",
    read: false,
    createdAt: "2026-04-25T10:18:00+09:00",
    link: "/compare?experiment=exp_001",
  },
  {
    id: "n_2",
    type: "evaluator_approved",
    title: "평가 함수 'pii_leakage_check' 승인됨",
    body: "운영팀이 승인했습니다. 이제 모든 사용자가 사용할 수 있습니다.",
    read: false,
    createdAt: "2026-03-22T14:00:00+09:00",
    link: "/evaluators",
  },
  {
    id: "n_3",
    type: "experiment_failed",
    title: "intent-classifier 베이스라인 실험 실패",
    body: "데이터셋 컬럼 'utterance' 누락으로 모든 아이템 실패",
    read: true,
    createdAt: "2026-04-15T09:03:00+09:00",
    link: "/experiments/exp_004",
  },
  {
    id: "n_4",
    type: "experiment_complete",
    title: "RAG 정확도 벤치마크 완료",
    body: "8개 Run 완료 · Claude 4.6 Opus가 0.91로 최고 스코어",
    read: true,
    createdAt: "2026-04-24T09:42:00+09:00",
    link: "/compare?experiment=exp_003",
  },
];

export const connectionHealth: ConnectionHealth = {
  langfuse: "ok",
  litellm: "ok",
  clickhouse: "ok",
  redis: "warn",
};

// ─────────────────────────────────────────────────────────────────────
// Phase 8-A: Trace eval mock traces
// ─────────────────────────────────────────────────────────────────────

export const traces: TraceTree[] = [
  {
    id: "trace-001",
    project_id: "production-api",
    name: "qa-agent-v3",
    input: { question: "프롬프트 평가 도구를 추천해줘." },
    output: "GenAI Labs를 사용하시면 됩니다.",
    user_id: "u-100",
    session_id: "sess-1",
    tags: ["production", "qa"],
    metadata: { source: "slack_bot" },
    observations: [
      {
        id: "obs-001-1",
        type: "span",
        name: "tool_search",
        level: "DEFAULT",
        start_time: "2026-04-25T08:30:00.000Z",
        end_time: "2026-04-25T08:30:00.420Z",
        latency_ms: 420,
        metadata: {},
      },
      {
        id: "obs-001-2",
        type: "generation",
        name: "answer",
        level: "DEFAULT",
        start_time: "2026-04-25T08:30:00.500Z",
        end_time: "2026-04-25T08:30:01.100Z",
        latency_ms: 600,
        model: "azure/gpt-4o",
        usage: { prompt_tokens: 220, completion_tokens: 35, total_tokens: 255 },
        cost_usd: 0.0014,
        metadata: {},
      },
    ],
    scores: [],
    total_cost_usd: 0.0014,
    total_latency_ms: 1020,
    timestamp: "2026-04-25T08:30:00.000Z",
  },
  {
    id: "trace-002",
    project_id: "production-api",
    name: "qa-agent-v3",
    input: { question: "비용을 추정해줘." },
    output: "{\n  \"estimated_cost_usd\": 0.42\n}",
    user_id: "u-101",
    session_id: "sess-2",
    tags: ["production", "qa"],
    metadata: {},
    observations: [
      {
        id: "obs-002-1",
        type: "span",
        name: "tool_calc_cost",
        level: "DEFAULT",
        start_time: "2026-04-25T09:00:00.000Z",
        end_time: "2026-04-25T09:00:00.080Z",
        latency_ms: 80,
        metadata: {},
      },
      {
        id: "obs-002-2",
        type: "generation",
        name: "answer",
        level: "DEFAULT",
        start_time: "2026-04-25T09:00:00.150Z",
        end_time: "2026-04-25T09:00:00.840Z",
        latency_ms: 690,
        model: "azure/gpt-4o",
        usage: { prompt_tokens: 180, completion_tokens: 25, total_tokens: 205 },
        cost_usd: 0.0011,
        metadata: {},
      },
    ],
    scores: [],
    total_cost_usd: 0.0011,
    total_latency_ms: 770,
    timestamp: "2026-04-25T09:00:00.000Z",
  },
  {
    id: "trace-003",
    project_id: "production-api",
    name: "qa-agent-v3",
    input: { question: "오류가 발생했어요. 도와주세요." },
    output: "어떤 오류 메시지가 나타나는지 알려주세요.",
    user_id: "u-200",
    session_id: "sess-3",
    tags: ["staging", "qa"],
    metadata: {},
    observations: [
      {
        id: "obs-003-1",
        type: "generation",
        name: "answer",
        level: "DEFAULT",
        start_time: "2026-04-25T10:15:00.000Z",
        end_time: "2026-04-25T10:15:00.500Z",
        latency_ms: 500,
        model: "azure/gpt-4o",
        usage: { prompt_tokens: 150, completion_tokens: 18, total_tokens: 168 },
        cost_usd: 0.0009,
        metadata: {},
      },
    ],
    scores: [],
    total_cost_usd: 0.0009,
    total_latency_ms: 500,
    timestamp: "2026-04-25T10:15:00.000Z",
  },
  {
    id: "trace-004",
    project_id: "production-api",
    name: "qa-agent-v3",
    input: { question: "deployment status를 확인해줘." },
    output: "현재 production 환경은 정상 운영 중입니다.",
    user_id: "u-300",
    session_id: "sess-4",
    tags: ["production"],
    metadata: { critical: true },
    observations: [
      {
        id: "obs-004-1",
        type: "span",
        name: "tool_health_check",
        level: "WARNING",
        status_message: "redis latency above threshold",
        start_time: "2026-04-25T11:00:00.000Z",
        end_time: "2026-04-25T11:00:00.230Z",
        latency_ms: 230,
        metadata: {},
      },
      {
        id: "obs-004-2",
        type: "generation",
        name: "answer",
        level: "DEFAULT",
        start_time: "2026-04-25T11:00:00.300Z",
        end_time: "2026-04-25T11:00:00.910Z",
        latency_ms: 610,
        model: "azure/gpt-4o",
        usage: { prompt_tokens: 200, completion_tokens: 30, total_tokens: 230 },
        cost_usd: 0.0013,
        metadata: {},
      },
    ],
    scores: [],
    total_cost_usd: 0.0013,
    total_latency_ms: 840,
    timestamp: "2026-04-25T11:00:00.000Z",
  },
];

// ─────────────────────────────────────────────────────────────────────
// Phase 8-B: Auto-Eval Policies / Runs / Cost
// ─────────────────────────────────────────────────────────────────────

export const autoEvalPolicies: AutoEvalPolicy[] = [
  {
    id: "policy_qa_v3_daily",
    name: "qa-agent-v3-daily",
    description: "qa-agent v3 production daily evaluation",
    project_id: "production-api",
    trace_filter: {
      project_id: "production-api",
      name: "qa-agent",
      tags: ["v3", "production"],
      sample_size: 200,
      sample_strategy: "random",
    },
    evaluators: [
      {
        type: "trace_builtin",
        name: "tool_called",
        config: { tool_name: "web_search" },
        weight: 0.3,
      },
      {
        type: "trace_builtin",
        name: "no_error_spans",
        config: {},
        weight: 0.2,
      },
      {
        type: "judge",
        name: "factuality",
        config: { judge_model: "gpt-4o" },
        weight: 0.5,
      },
    ],
    schedule: {
      type: "cron",
      cron_expression: "0 3 * * *",
      timezone: "Asia/Seoul",
    },
    alert_thresholds: [
      {
        metric: "pass_rate",
        operator: "lt",
        value: 0.85,
        drop_pct: 0.1,
        window_minutes: 60,
      },
    ],
    notification_targets: ["user_1"],
    daily_cost_limit_usd: 5.0,
    status: "active",
    owner: "user_1",
    created_at: "2026-04-19T10:00:00.000Z",
    updated_at: "2026-04-25T10:00:00.000Z",
    last_run_at: "2026-04-25T18:00:00.000Z",
    next_run_at: "2026-04-26T18:00:00.000Z",
  },
  {
    id: "policy_summary_hourly",
    name: "summary-agent-hourly",
    description: "summary-agent staging 1시간마다 자동 평가",
    project_id: "production-api",
    trace_filter: {
      project_id: "production-api",
      name: "summary-agent",
      tags: ["staging"],
      sample_size: 50,
      sample_strategy: "first",
    },
    evaluators: [
      { type: "builtin", name: "rouge", weight: 0.4 },
      {
        type: "judge",
        name: "summary_quality",
        config: { judge_model: "gpt-4o" },
        weight: 0.6,
      },
    ],
    schedule: { type: "interval", interval_seconds: 3600 },
    alert_thresholds: [
      { metric: "avg_score", operator: "lt", value: 0.7 },
    ],
    notification_targets: ["user_1"],
    daily_cost_limit_usd: 2.0,
    status: "active",
    owner: "user_1",
    created_at: "2026-04-21T09:00:00.000Z",
    updated_at: "2026-04-22T09:00:00.000Z",
    last_run_at: "2026-04-25T17:00:00.000Z",
    next_run_at: "2026-04-25T18:00:00.000Z",
  },
  {
    id: "policy_rag_event",
    name: "rag-agent-on-new-traces",
    description: "rag-agent 새 trace가 100개 누적될 때마다 평가",
    project_id: "production-api",
    trace_filter: {
      project_id: "production-api",
      name: "rag-agent",
      tags: ["production"],
      sample_size: 100,
      sample_strategy: "first",
    },
    evaluators: [
      {
        type: "judge",
        name: "factuality",
        config: { judge_model: "gpt-4o" },
        weight: 0.7,
      },
      {
        type: "trace_builtin",
        name: "tool_result_grounding",
        config: {},
        weight: 0.3,
      },
    ],
    schedule: {
      type: "event",
      event_trigger: "new_traces",
      event_threshold: 100,
    },
    alert_thresholds: [
      { metric: "pass_rate", operator: "lt", value: 0.8 },
    ],
    notification_targets: ["user_1"],
    daily_cost_limit_usd: 8.0,
    status: "active",
    owner: "user_1",
    created_at: "2026-04-15T11:00:00.000Z",
    updated_at: "2026-04-23T11:00:00.000Z",
    last_run_at: "2026-04-25T13:30:00.000Z",
    next_run_at: undefined,
  },
  {
    id: "policy_intent_paused",
    name: "intent-classifier-weekly",
    description: "intent-classifier 주간 회귀 테스트 (현재 일시정지)",
    project_id: "production-api",
    trace_filter: {
      project_id: "production-api",
      name: "intent-classifier",
      sample_size: 300,
      sample_strategy: "stratified",
    },
    evaluators: [{ type: "builtin", name: "exact_match", weight: 1.0 }],
    schedule: {
      type: "cron",
      cron_expression: "0 0 * * 1",
      timezone: "Asia/Seoul",
    },
    alert_thresholds: [],
    notification_targets: [],
    daily_cost_limit_usd: 1.0,
    status: "paused",
    owner: "user_1",
    created_at: "2026-04-10T08:00:00.000Z",
    updated_at: "2026-04-22T08:00:00.000Z",
    last_run_at: "2026-04-15T00:00:00.000Z",
    next_run_at: undefined,
  },
  {
    id: "policy_legacy_deprecated",
    name: "legacy-pipeline-eval",
    description: "구 버전 평가 — 신규 정책으로 대체됨",
    project_id: "production-api",
    trace_filter: {
      project_id: "production-api",
      name: "legacy-agent",
      sample_size: 100,
    },
    evaluators: [{ type: "builtin", name: "contains", weight: 1.0 }],
    schedule: {
      type: "cron",
      cron_expression: "0 6 * * *",
      timezone: "Asia/Seoul",
    },
    alert_thresholds: [],
    notification_targets: [],
    daily_cost_limit_usd: 0.5,
    status: "deprecated",
    owner: "user_1",
    created_at: "2026-02-01T10:00:00.000Z",
    updated_at: "2026-03-15T10:00:00.000Z",
    last_run_at: "2026-03-14T06:00:00.000Z",
    next_run_at: undefined,
  },
];

const RUN_RAND = seedRandom(42);

function buildRunsFor(
  policyId: string,
  baseScore: number,
  basePassRate: number,
  baseCost: number,
  count: number,
  status: "running" | "completed" | "failed" | "skipped" = "completed",
): AutoEvalRun[] {
  const result: AutoEvalRun[] = [];
  const now = Date.now();
  for (let i = 0; i < count; i += 1) {
    const startedAt = new Date(now - i * 86_400_000).toISOString();
    const noise = (RUN_RAND() - 0.5) * 0.18;
    const passRate = Math.max(0, Math.min(1, basePassRate + noise));
    const avgScore = Math.max(0, Math.min(1, baseScore + noise));
    const cost = Number((baseCost * (0.7 + RUN_RAND() * 0.6)).toFixed(4));
    const isFail = status === "failed" && i === 0;
    const isSkip = status === "skipped" && i === 0;
    const isRun = status === "running" && i === 0;
    const runStatus: AutoEvalRun["status"] = isFail
      ? "failed"
      : isSkip
        ? "skipped"
        : isRun
          ? "running"
          : "completed";
    result.push({
      id: `run_${policyId}_${i}`,
      policy_id: policyId,
      started_at: startedAt,
      completed_at:
        runStatus === "running"
          ? undefined
          : new Date(
              new Date(startedAt).getTime() + 4 * 60_000,
            ).toISOString(),
      status: runStatus,
      skip_reason: runStatus === "skipped" ? "trace_count_below_min" : undefined,
      traces_evaluated: runStatus === "completed" ? 200 : runStatus === "running" ? 80 : 0,
      traces_total: 200,
      avg_score: runStatus === "completed" ? Number(avgScore.toFixed(3)) : undefined,
      pass_rate: runStatus === "completed" ? Number(passRate.toFixed(3)) : undefined,
      cost_usd: cost,
      duration_ms: runStatus === "completed" ? 240_000 + Math.floor(RUN_RAND() * 60_000) : undefined,
      scores_by_evaluator:
        runStatus === "completed"
          ? {
              tool_called: Number((avgScore - 0.05).toFixed(3)),
              no_error_spans: Number(Math.min(1, avgScore + 0.05).toFixed(3)),
              factuality: Number(avgScore.toFixed(3)),
            }
          : {},
      triggered_alerts: passRate < 0.85 && runStatus === "completed" ? ["pass_rate_below_threshold"] : [],
      review_items_created:
        runStatus === "completed"
          ? Math.floor((1 - passRate) * 200)
          : 0,
      error_message:
        runStatus === "failed" ? "Langfuse trace fetch timeout" : undefined,
    });
  }
  return result;
}

export const autoEvalRuns: AutoEvalRun[] = [
  ...buildRunsFor("policy_qa_v3_daily", 0.88, 0.9, 0.8, 14),
  ...buildRunsFor("policy_summary_hourly", 0.78, 0.82, 0.18, 10),
  ...buildRunsFor("policy_rag_event", 0.82, 0.86, 1.2, 9),
  ...buildRunsFor(
    "policy_intent_paused",
    0.7,
    0.72,
    0.05,
    4,
    "skipped",
  ),
  ...buildRunsFor(
    "policy_legacy_deprecated",
    0.55,
    0.58,
    0.04,
    3,
    "failed",
  ),
];

/**
 * 정책별 일일 비용 누적치 mock 생성기.
 *
 * `from_date` ~ `to_date` 사이의 날짜에 대해 해당 정책의 mock run을 집계한다.
 */
export function buildMockCostUsage(
  policyId: string,
  fromDate: string,
  toDate: string,
): CostUsage {
  const policy = autoEvalPolicies.find((p) => p.id === policyId);
  const policyRuns = autoEvalRuns.filter((r) => r.policy_id === policyId);
  const fromMs = new Date(fromDate).getTime();
  const toMs = new Date(toDate).getTime();
  const buckets: Record<string, { cost: number; runs: number }> = {};
  for (const r of policyRuns) {
    const ts = new Date(r.started_at).getTime();
    if (ts < fromMs || ts > toMs) continue;
    const day = r.started_at.slice(0, 10);
    if (!buckets[day]) buckets[day] = { cost: 0, runs: 0 };
    buckets[day].cost += r.cost_usd;
    buckets[day].runs += 1;
  }
  const dailyBreakdown = Object.entries(buckets)
    .map(([date, v]) => ({
      date,
      cost_usd: Number(v.cost.toFixed(4)),
      runs_count: v.runs,
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
  const total = dailyBreakdown.reduce((s, d) => s + d.cost_usd, 0);
  return {
    policy_id: policyId,
    date_range: `${fromDate}..${toDate}`,
    daily_breakdown: dailyBreakdown,
    total_cost_usd: Number(total.toFixed(4)),
    daily_limit_usd: policy?.daily_cost_limit_usd,
  };
}
