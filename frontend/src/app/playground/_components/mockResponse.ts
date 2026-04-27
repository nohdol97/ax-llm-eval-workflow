// Type definitions for the playground response/history.
// (Mock content generators were removed once the page wired up real SSE.)

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
  /** confidence-like score in [0,1] for badge display */
  score: number;
}
