/**
 * Backend Pydantic 모델의 TypeScript 미러.
 *
 * Phase 0~6에서 정의된 응답/요청 타입을 도메인별로 재선언한다.
 * Backend 모델이 변경되면 본 파일도 동시에 업데이트.
 *
 * 참조: docs/API_DESIGN.md §1~§14, BUILD_ORDER.md 작업 7-0
 */

// ─────────────────────────────────────────────────────────────────────
// 공통 / 페이지네이션
// ─────────────────────────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail?: string;
  code?: string;
  instance?: string;
  [key: string]: unknown;
}

// ─────────────────────────────────────────────────────────────────────
// User / Auth
// ─────────────────────────────────────────────────────────────────────

export type RBACRole = "admin" | "user" | "viewer";

export interface User {
  id: string;
  email?: string;
  role: RBACRole;
  name?: string;
  groups: string[];
}

export interface JwtPayload {
  sub: string;
  email?: string;
  name?: string;
  role: RBACRole;
  groups?: string[];
  exp?: number;
  iat?: number;
}

// ─────────────────────────────────────────────────────────────────────
// Health
// ─────────────────────────────────────────────────────────────────────

export interface ServiceHealth {
  status: "ok" | "warn" | "error";
  latency_ms?: number;
  endpoint?: string;
  checked_at: string;
}

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  version: string;
  environment: string;
  services: Record<string, ServiceHealth>;
}

// ─────────────────────────────────────────────────────────────────────
// Project
// ─────────────────────────────────────────────────────────────────────

export interface ProjectInfo {
  id: string;
  name: string;
  description?: string;
  created_at?: string;
}

export interface ProjectListResponse {
  projects: ProjectInfo[];
}

// ─────────────────────────────────────────────────────────────────────
// Prompt
// ─────────────────────────────────────────────────────────────────────

export type PromptType = "text" | "chat";
export type PromptLabel = "production" | "staging" | "draft" | string;

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface PromptSummary {
  name: string;
  latest_version: number;
  labels: string[];
  tags: string[];
  created_at: string;
}

export interface PromptDetail {
  name: string;
  version: number;
  type: PromptType;
  prompt: string | ChatMessage[];
  config: Record<string, unknown>;
  labels: string[];
  variables: string[];
  created_at: string;
}

export interface PromptVersionSummary {
  version: number;
  labels: string[];
  created_at: string;
  created_by?: string;
}

export interface PromptCreate {
  project_id: string;
  name: string;
  prompt: string | ChatMessage[];
  type?: PromptType;
  config?: Record<string, unknown>;
  labels?: string[];
}

export interface PromptCreateResponse {
  name: string;
  version: number;
  labels: string[];
}

export interface PromptLabelsPatch {
  project_id: string;
  labels: string[];
}

// ─────────────────────────────────────────────────────────────────────
// Dataset
// ─────────────────────────────────────────────────────────────────────

export interface DatasetSummary {
  name: string;
  description?: string;
  item_count: number;
  created_at: string;
  last_used_at?: string;
  metadata?: Record<string, unknown>;
}

export interface DatasetItem {
  id: string;
  input: Record<string, unknown>;
  expected_output?: string | Record<string, unknown>;
  metadata: Record<string, unknown>;
}

export type UploadStatus =
  | "pending"
  | "processing"
  | "running"
  | "completed"
  | "partial"
  | "failed";

export interface UploadResponseSync {
  dataset_name: string;
  items_created: number;
  items_failed: number;
  failed_items: Array<{ row: number; error: string }>;
  status: UploadStatus;
  upload_id: string;
}

export interface UploadResponseAsync {
  upload_id: string;
  status: "processing";
  stream_url: string;
}

export type UploadResponse = UploadResponseSync | UploadResponseAsync;

export interface UploadProgress {
  upload_id: string;
  status: UploadStatus;
  processed: number;
  completed?: number;
  failed: number;
  total: number;
  error_message?: string;
}

export interface UploadPreviewResponse {
  columns: string[];
  preview: Array<{
    input: Record<string, unknown>;
    expected_output?: string | Record<string, unknown>;
    metadata: Record<string, unknown>;
  }>;
  total_rows: number;
}

export interface DatasetFromItemsRequest {
  project_id: string;
  source_run_names: string[];
  item_ids: string[];
  new_dataset_name: string;
  description?: string;
}

// ─────────────────────────────────────────────────────────────────────
// Model
// ─────────────────────────────────────────────────────────────────────

export interface ModelInfo {
  id: string;
  name: string;
  provider: string;
  display_name?: string;
  vision?: boolean;
  supports_streaming?: boolean;
  context_window: number;
  input_cost_per_k: number;
  output_cost_per_k: number;
  capabilities?: string[];
}

export interface ProviderGroup {
  id: string;
  name: string;
  models: ModelInfo[];
}

export interface ModelListResponse {
  models: ModelInfo[];
}

// ─────────────────────────────────────────────────────────────────────
// Search
// ─────────────────────────────────────────────────────────────────────

export type SearchResultType = "prompt" | "dataset" | "experiment";

export interface SearchResult {
  type: SearchResultType;
  id: string;
  name: string;
  snippet?: string;
  score: number;
  match_context?: string;
}

export interface SearchResponse {
  query: string;
  results: {
    prompts?: SearchResult[];
    datasets?: SearchResult[];
    experiments?: SearchResult[];
  };
  total: number;
}

// ─────────────────────────────────────────────────────────────────────
// Notification
// ─────────────────────────────────────────────────────────────────────

export type NotificationType =
  | "experiment_complete"
  | "experiment_failed"
  | "experiment_cancelled"
  | "evaluator_approved"
  | "evaluator_rejected"
  | "evaluator_deprecated"
  | "evaluator_submission_pending"
  | "budget_warning";

export interface Notification {
  id: string;
  user_id: string;
  type: NotificationType;
  title: string;
  body?: string;
  message?: string;
  link?: string;
  target_url?: string;
  read: boolean;
  created_at: string;
  read_at?: string;
}

export interface NotificationListResponse {
  items?: Notification[];
  notifications?: Notification[];
  total: number;
  unread_count: number;
  page: number;
  page_size: number;
}

// ─────────────────────────────────────────────────────────────────────
// Single Test
// ─────────────────────────────────────────────────────────────────────

export interface PromptSource {
  source: "langfuse" | "inline";
  name?: string;
  version?: number;
  label?: string;
  content?: string | ChatMessage[];
  body?: string | ChatMessage[];
  type?: PromptType;
}

export interface ModelParameters {
  temperature?: number;
  max_tokens?: number;
  top_p?: number;
  frequency_penalty?: number;
  presence_penalty?: number;
  [key: string]: unknown;
}

export interface SingleTestRequest {
  project_id: string;
  prompt: PromptSource;
  variables: Record<string, unknown>;
  model: string;
  parameters: ModelParameters;
  system_prompt?: string;
  images?: string[];
  expected_output?: string | Record<string, unknown>;
  evaluators?: EvaluatorConfig[];
  stream?: boolean;
}

export interface UsageStats {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface SingleTestResponse {
  trace_id: string;
  output: string;
  usage: UsageStats;
  latency_ms: number;
  cost_usd: number;
  model_cost_usd?: number;
  eval_cost_usd?: number;
  model: string;
  scores?: Record<string, number>;
}

export type SingleTestStreamEvent =
  | { type: "token"; data: { content: string } }
  | { type: "done"; data: SingleTestResponse }
  | { type: "error"; data: { code: string; message: string } };

// ─────────────────────────────────────────────────────────────────────
// Experiment
// ─────────────────────────────────────────────────────────────────────

export type ExperimentStatus =
  | "pending"
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | "degraded";

export interface PromptConfigItem {
  name: string;
  version?: number;
  label?: string | null;
}

export interface ModelConfigItem {
  model: string;
  parameters?: ModelParameters;
}

export type ExperimentMode = "live" | "trace_eval";

export interface ExperimentCreate {
  project_id: string;
  name: string;
  description?: string;

  /** 실험 모드 (Phase 8-A) — 기본값 live. */
  mode?: ExperimentMode;

  // mode=live (기존 — optional로 변경)
  prompt_configs?: PromptConfigItem[];
  dataset_name?: string;
  dataset_variable_mapping?: Record<string, string>;
  model_configs?: ModelConfigItem[];

  // mode=trace_eval (Phase 8-A 신규)
  trace_filter?: TraceFilter;
  expected_dataset_name?: string;

  // 공통
  evaluators: EvaluatorConfig[];
  concurrency?: number;
  system_prompt?: string;
  metadata?: Record<string, unknown>;
}

export interface RunInitInfo {
  run_name: string;
  prompt_version: number;
  model: string;
  status: ExperimentStatus;
}

export interface ExperimentInitResponse {
  experiment_id: string;
  status: ExperimentStatus;
  total_runs: number;
  total_items: number;
  runs?: RunInitInfo[];
  started_at?: string;
}

export interface ExperimentSummary {
  experiment_id: string;
  name: string;
  status: ExperimentStatus;
  runs_total?: number;
  total_runs?: number;
  runs_completed?: number;
  total_cost: number;
  total_cost_usd?: number;
  avg_score?: number;
  created_at: string;
}

export interface RunSummary {
  run_name: string;
  model: string;
  prompt_version: number;
  status: ExperimentStatus;
  items_completed?: number;
  items_total?: number;
  avg_score?: number;
  total_cost?: number;
  avg_latency_ms?: number;
  summary?: {
    avg_score?: number;
    total_cost?: number;
    avg_latency?: number;
    avg_latency_ms?: number;
  };
}

export interface ExperimentProgress {
  processed?: number;
  completed?: number;
  failed?: number;
  total: number;
  percentage?: number;
  eta_sec?: number;
}

export interface ExperimentDetail {
  experiment_id: string;
  name: string;
  description?: string;
  status: ExperimentStatus;
  project_id: string;
  owner: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  progress: ExperimentProgress;
  runs: RunSummary[];
  config_snapshot: Record<string, unknown>;
  evaluator_summary?: Record<string, unknown>;
  /** Phase 8-A — 실험 모드. 백엔드 default=live. */
  mode?: ExperimentMode;
  /** mode=trace_eval에서 사용된 trace 필터 snapshot. */
  trace_filter?: TraceFilter;
  /** mode=trace_eval에서 평가 완료된 trace 수. */
  traces_evaluated?: number;
}

export type ExperimentStreamEvent =
  | {
      type: "progress";
      data: {
        run_name: string;
        completed: number;
        total: number;
        current_item?: { id: string; status: string; score?: number };
      };
    }
  | {
      type: "run_complete";
      data: {
        run_name: string;
        summary: { avg_score: number; total_cost: number; avg_latency: number };
      };
    }
  | {
      type: "experiment_complete";
      data: {
        experiment_id: string;
        total_duration_sec: number;
        total_cost_usd: number;
      };
    }
  | {
      type: "error";
      data: { run_name?: string; item_id?: string; error: string };
    };

export interface ExperimentControlResponse {
  experiment_id: string;
  status: ExperimentStatus;
  updated_at: string;
}

// ─────────────────────────────────────────────────────────────────────
// Evaluator
// ─────────────────────────────────────────────────────────────────────

export type EvaluatorType =
  | "builtin"
  | "built_in"
  | "judge"
  | "llm_judge"
  | "approved"
  | "inline_custom"
  | "custom_code"
  | "trace_builtin";

export interface EvaluatorConfig {
  type: EvaluatorType;
  name?: string;
  submission_id?: string;
  version?: number;
  config?: Record<string, unknown>;
  code?: string;
  weight?: number;
}

export type SubmissionStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "deprecated";

export interface Submission {
  submission_id: string;
  name: string;
  description: string;
  code?: string;
  code_hash?: string;
  status: SubmissionStatus;
  version?: number;
  submitted_by?: string;
  submitter?: string;
  submitted_at?: string;
  created_at?: string;
  approved_by?: string;
  approved_at?: string;
  reviewed_at?: string;
  reviewer?: string;
  rejected_by?: string;
  rejected_at?: string;
  rejection_reason?: string;
  deprecated_at?: string;
  deprecated_by?: string;
  reason?: string;
}

export interface SubmissionListResponse {
  submissions: Submission[];
}

export interface ApprovedEvaluator {
  submission_id: string;
  name: string;
  description: string;
  version: number;
  approved_at: string;
  approver: string;
}

export interface ApprovedEvaluatorListResponse {
  evaluators: ApprovedEvaluator[];
}

export interface BuiltInEvaluatorInfo {
  name: string;
  description: string;
  data_type?: "BOOLEAN" | "NUMERIC";
  return_type?: "binary" | "float" | "integer";
  range?: [number, number];
  parameters?: Record<string, unknown>;
  config_schema?: Record<string, unknown>;
}

export interface BuiltInEvaluatorListResponse {
  evaluators: BuiltInEvaluatorInfo[];
}

export interface SubmissionCreate {
  name: string;
  description: string;
  code: string;
}

export interface SubmissionRejectRequest {
  reason: string;
}

export interface ValidateRequest {
  code: string;
  test_cases: Array<{
    output: string;
    expected: string;
    metadata?: Record<string, unknown>;
  }>;
}

export interface ValidateResponse {
  valid: boolean;
  test_results: Array<{
    input_index: number;
    result: number | null;
    error: string | null;
  }>;
}

export interface ScoreConfig {
  name: string;
  data_type: "BOOLEAN" | "NUMERIC" | "CATEGORICAL";
  min_value?: number;
  max_value?: number;
  source: "built_in" | "llm_judge" | "custom_code" | "system";
  registered: boolean;
}

export interface ScoreConfigListResponse {
  score_configs: ScoreConfig[];
}

// ─────────────────────────────────────────────────────────────────────
// Analysis
// ─────────────────────────────────────────────────────────────────────

export interface CompareRequest {
  project_id: string;
  run_names: string[];
}

export interface RunMetrics {
  sample_count?: number;
  avg_latency_ms?: number;
  p50_latency_ms?: number;
  p90_latency_ms?: number;
  p99_latency_ms?: number;
  total_cost_usd: number;
  model_cost_usd?: number;
  eval_cost_usd?: number;
  avg_input_tokens?: number;
  avg_output_tokens?: number;
  avg_total_tokens?: number;
  items_completed?: number;
}

export interface ScoreSummary {
  avg: number;
  min: number;
  max: number;
  stddev?: number;
}

export interface CompareEntry {
  run_name: string;
  model: string;
  prompt_version: number;
  metrics: RunMetrics;
  scores: Record<string, ScoreSummary | number | null>;
}

export interface CompareResponse {
  comparison: CompareEntry[];
}

export interface CompareItemsFilter {
  score_name?: string;
  score_min?: number | null;
  score_max?: number | null;
  latency_min_ms?: number | null;
  latency_max_ms?: number | null;
}

export interface CompareItemsRequest {
  project_id: string;
  run_names: string[];
  score_name?: string;
  sort_by?: string;
  sort_order?: "asc" | "desc";
  page?: number;
  page_size?: number;
  filter?: CompareItemsFilter;
}

export interface CompareItemEntry {
  dataset_item_id: string;
  input: Record<string, unknown>;
  expected_output?: string | Record<string, unknown>;
  results: Record<
    string,
    {
      output: string;
      score?: number | null;
      latency_ms: number;
      cost_usd: number;
    }
  >;
  score_range?: number;
}

export interface CompareItemsResponse {
  items: CompareItemEntry[];
  total: number;
  page: number;
  page_size: number;
}

export interface DistributionBin {
  bin_start: number;
  bin_end: number;
  count: number;
}

export interface DistributionStatistics {
  mean?: number;
  median?: number;
  stddev?: number;
  min?: number;
  max?: number;
  p50?: number;
  p90?: number;
  p99?: number;
}

export interface ScoreDistributionResponse {
  distribution: DistributionBin[];
  statistics: DistributionStatistics;
}

export interface MultiRunDistributionResponse {
  runs: Record<
    string,
    {
      distribution: DistributionBin[];
      statistics: DistributionStatistics;
    }
  >;
}

// ─────────────────────────────────────────────────────────────────────
// Trace (Phase 8-A) — Agent trace 평가
// ─────────────────────────────────────────────────────────────────────

export type TraceObservationType = "span" | "generation" | "event";
export type TraceObservationLevel = "DEBUG" | "DEFAULT" | "WARNING" | "ERROR";
export type TraceSampleStrategy = "random" | "first" | "stratified";

export interface TraceObservation {
  id: string;
  type: TraceObservationType;
  name: string;
  parent_observation_id?: string | null;
  input?: Record<string, unknown> | unknown[] | string | null;
  output?: Record<string, unknown> | unknown[] | string | null;
  level: TraceObservationLevel;
  status_message?: string | null;
  start_time: string;
  end_time?: string | null;
  latency_ms?: number | null;
  model?: string | null;
  usage?: Record<string, number> | null;
  cost_usd?: number | null;
  metadata: Record<string, unknown>;
}

export interface TraceTree {
  id: string;
  project_id: string;
  name: string;
  input?: Record<string, unknown> | unknown[] | string | null;
  output?: Record<string, unknown> | unknown[] | string | null;
  user_id?: string | null;
  session_id?: string | null;
  tags: string[];
  metadata: Record<string, unknown>;
  observations: TraceObservation[];
  scores: Record<string, unknown>[];
  total_cost_usd: number;
  total_latency_ms?: number | null;
  timestamp: string;
}

export interface TraceFilter {
  project_id: string;
  name?: string;
  tags?: string[];
  user_ids?: string[];
  session_ids?: string[];
  from_timestamp?: string;
  to_timestamp?: string;
  sample_size?: number;
  sample_strategy?: TraceSampleStrategy;
  metadata_match?: Record<string, unknown>;
}

export interface TraceSummary {
  id: string;
  name: string;
  user_id?: string | null;
  session_id?: string | null;
  tags: string[];
  total_cost_usd: number;
  total_latency_ms?: number | null;
  timestamp: string;
  observation_count: number;
}

export interface TraceSearchRequest {
  filter: TraceFilter;
  page?: number;
  page_size?: number;
  include_observations?: boolean;
}

export interface TraceSearchResponse {
  items: TraceSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface TraceScoreRequest {
  name: string;
  value: number;
  comment?: string;
}

export interface TraceScoreResponse {
  trace_id: string;
  score_id: string;
  name: string;
  value: number;
  created_at: string;
}
