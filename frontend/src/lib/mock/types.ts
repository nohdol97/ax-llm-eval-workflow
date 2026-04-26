export type ProviderId =
  | "azure"
  | "openai"
  | "google"
  | "anthropic"
  | "bedrock";

export type RBACRole = "admin" | "user" | "viewer";

export interface Model {
  id: string;
  name: string;
  provider: ProviderId;
  vision: boolean;
  contextWindow: number;
  inputCostPerK: number;
  outputCostPerK: number;
}

export interface Prompt {
  id: string;
  name: string;
  latestVersion: number;
  versions: PromptVersion[];
  labels: ("production" | "staging" | "draft")[];
  lastUsed: string;
  usageCount: number;
  description?: string;
}

export interface PromptVersion {
  version: number;
  body: string;
  systemPrompt?: string;
  variables: string[];
  createdAt: string;
  author: string;
}

export interface Dataset {
  id: string;
  name: string;
  description?: string;
  itemCount: number;
  createdAt: string;
  lastUsed?: string;
  recentExperimentCount: number;
}

export interface DatasetItem {
  id: string;
  input: Record<string, unknown>;
  expectedOutput: string;
  metadata: Record<string, unknown>;
}

export type ExperimentStatus =
  | "completed"
  | "running"
  | "paused"
  | "failed"
  | "cancelled";

export interface Experiment {
  id: string;
  name: string;
  description?: string;
  status: ExperimentStatus;
  promptId: string;
  promptName: string;
  promptVersions: number[];
  datasetId: string;
  datasetName: string;
  modelIds: string[];
  evaluatorIds: string[];
  itemCount: number;
  runCount: number;
  completedRuns: number;
  totalCostUsd: number;
  avgScore: number | null;
  avgLatencyMs: number | null;
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  owner: string;
}

export interface Run {
  id: string;
  experimentId: string;
  promptVersion: number;
  modelId: string;
  modelName: string;
  status: ExperimentStatus;
  itemsCompleted: number;
  itemsTotal: number;
  avgScore: number | null;
  avgLatencyMs: number | null;
  totalCostUsd: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  scoresByEvaluator: Record<string, number>;
}

export interface ItemResult {
  itemId: string;
  itemIndex: number;
  input: string;
  expected: string;
  outputs: Record<string, string>;
  scoresByRun: Record<string, number | null>;
  latenciesByRun: Record<string, number>;
  costsByRun: Record<string, number>;
}

export type EvaluatorType = "builtin" | "judge" | "custom";
export type EvaluatorStatus = "approved" | "pending" | "rejected" | "deprecated";

export interface Evaluator {
  id: string;
  name: string;
  type: EvaluatorType;
  status: EvaluatorStatus;
  description: string;
  range: "0-1" | "0-10" | "binary";
  submittedBy?: string;
  submittedAt?: string;
  approvedBy?: string;
  approvedAt?: string;
  usageCount: number;
}

export interface Notification {
  id: string;
  type:
    | "experiment_complete"
    | "experiment_failed"
    | "evaluator_approved"
    | "evaluator_rejected";
  title: string;
  body: string;
  read: boolean;
  createdAt: string;
  link?: string;
}

export interface User {
  id: string;
  name: string;
  email: string;
  role: RBACRole;
  initials: string;
}

export interface Project {
  id: string;
  name: string;
  description: string;
}

export interface ConnectionHealth {
  langfuse: "ok" | "warn" | "error";
  litellm: "ok" | "warn" | "error";
  clickhouse: "ok" | "warn" | "error";
  redis: "ok" | "warn" | "error";
}
