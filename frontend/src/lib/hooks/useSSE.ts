"use client";
/**
 * SSE 구독 훅 모음.
 *
 *  - useSSE: 범용 SSE 훅 (URL + onEvent)
 *  - useSingleTestStream: 단일 테스트 토큰 스트리밍
 *  - useExperimentStream: 실험 진행률 스트리밍
 *  - useDatasetUploadStream: 업로드 진행률 스트리밍
 *
 * 참조: docs/API_DESIGN.md §3.1, §4.2, §6.3.1
 */
import { useEffect, useRef, useState } from "react";
import { config } from "../config";
import { subscribeSSE, type SSEEvent, type SSEEventHandler } from "../sse";
import type {
  ExperimentStreamEvent,
  SingleTestResponse,
  SingleTestStreamEvent,
  UploadProgress,
} from "../types/api";

export interface UseSSEOptions<T> {
  url: string | null;
  query?: Record<
    string,
    string | number | boolean | string[] | null | undefined
  >;
  onEvent?: (event: SSEEvent<T>) => void;
  onError?: (err: Error) => void;
  onOpen?: () => void;
  onClose?: () => void;
  enabled?: boolean;
  lastEventId?: string;
}

/** 범용 SSE 구독 훅 (선언형 ref 안정성 보장) */
export function useSSE<T = unknown>(options: UseSSEOptions<T>): void {
  const handlerRef = useRef<UseSSEOptions<T>>(options);
  handlerRef.current = options;

  useEffect(() => {
    if (!options.url || options.enabled === false) return;
    if (config.useMock) {
      // mock 모드에서는 SSE 비활성 (페이지가 mock 데이터로 결과 시뮬레이션)
      return;
    }

    const handler: SSEEventHandler<T> = {
      onEvent: (e) => handlerRef.current.onEvent?.(e),
      onError: (e) => handlerRef.current.onError?.(e),
      onOpen: () => handlerRef.current.onOpen?.(),
      onClose: () => handlerRef.current.onClose?.(),
    };

    const unsubscribe = subscribeSSE<T>(
      {
        url: options.url,
        query: options.query,
        lastEventId: options.lastEventId,
      },
      handler,
    );
    return () => unsubscribe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    options.url,
    options.enabled,
    options.lastEventId,
    JSON.stringify(options.query ?? {}),
  ]);
}

// ─────────────────────────────────────────────────────────────────────
// 단일 테스트 SSE
// ─────────────────────────────────────────────────────────────────────

export interface SingleTestStreamState {
  tokens: string;
  done: SingleTestResponse | null;
  error: string | null;
  isStreaming: boolean;
}

export interface UseSingleTestStreamOptions {
  /** URL 미지정 시 비활성. POST → trace_id 수신 후 stream URL 동적 결정 */
  url: string | null;
  enabled?: boolean;
  onToken?: (token: string) => void;
  onDone?: (result: SingleTestResponse) => void;
  onError?: (err: string) => void;
}

export function useSingleTestStream(
  options: UseSingleTestStreamOptions,
): SingleTestStreamState {
  const [state, setState] = useState<SingleTestStreamState>({
    tokens: "",
    done: null,
    error: null,
    isStreaming: false,
  });
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    if (!options.url || options.enabled === false) return;
    if (config.useMock) return;

    setState({ tokens: "", done: null, error: null, isStreaming: true });

    const unsubscribe = subscribeSSE<unknown>(
      { url: options.url },
      {
        onEvent: (event) => {
          const ev = event as SingleTestStreamEvent;
          if (ev.type === "token" && ev.data?.content) {
            optionsRef.current.onToken?.(ev.data.content);
            setState((prev) => ({
              ...prev,
              tokens: prev.tokens + ev.data.content,
            }));
          } else if (ev.type === "done") {
            optionsRef.current.onDone?.(ev.data as SingleTestResponse);
            setState((prev) => ({
              ...prev,
              done: ev.data as SingleTestResponse,
              isStreaming: false,
            }));
          } else if (ev.type === "error") {
            const msg =
              (ev.data as { message?: string })?.message ?? "stream error";
            optionsRef.current.onError?.(msg);
            setState((prev) => ({ ...prev, error: msg, isStreaming: false }));
          }
        },
        onError: (err) => {
          optionsRef.current.onError?.(err.message);
          setState((prev) => ({
            ...prev,
            error: err.message,
            isStreaming: false,
          }));
        },
        onClose: () => {
          setState((prev) => ({ ...prev, isStreaming: false }));
        },
      },
    );
    return () => unsubscribe();
  }, [options.url, options.enabled]);

  return state;
}

// ─────────────────────────────────────────────────────────────────────
// 실험 진행률 SSE
// ─────────────────────────────────────────────────────────────────────

export interface ExperimentStreamState {
  events: ExperimentStreamEvent[];
  latest: ExperimentStreamEvent | null;
  error: string | null;
  isStreaming: boolean;
}

export interface UseExperimentStreamOptions {
  experimentId: string | null;
  enabled?: boolean;
  onEvent?: (event: ExperimentStreamEvent) => void;
  lastEventId?: string;
}

export function useExperimentStream(
  options: UseExperimentStreamOptions,
): ExperimentStreamState {
  const [state, setState] = useState<ExperimentStreamState>({
    events: [],
    latest: null,
    error: null,
    isStreaming: false,
  });
  const onEventRef = useRef(options.onEvent);
  onEventRef.current = options.onEvent;

  const url = options.experimentId
    ? `/experiments/${encodeURIComponent(options.experimentId)}/stream`
    : null;

  useEffect(() => {
    if (!url || options.enabled === false) return;
    if (config.useMock) return;

    setState({ events: [], latest: null, error: null, isStreaming: true });

    const unsubscribe = subscribeSSE<unknown>(
      { url, lastEventId: options.lastEventId },
      {
        onEvent: (event) => {
          const ev = event as ExperimentStreamEvent;
          onEventRef.current?.(ev);
          setState((prev) => ({
            ...prev,
            events: [...prev.events, ev],
            latest: ev,
            isStreaming: ev.type !== "experiment_complete",
          }));
        },
        onError: (err) => {
          setState((prev) => ({
            ...prev,
            error: err.message,
            isStreaming: false,
          }));
        },
        onClose: () => {
          setState((prev) => ({ ...prev, isStreaming: false }));
        },
      },
    );
    return () => unsubscribe();
  }, [url, options.enabled, options.lastEventId]);

  return state;
}

// ─────────────────────────────────────────────────────────────────────
// 데이터셋 업로드 진행률 SSE
// ─────────────────────────────────────────────────────────────────────

export interface DatasetUploadStreamState {
  progress: UploadProgress | null;
  error: string | null;
  isStreaming: boolean;
}

export interface UseDatasetUploadStreamOptions {
  uploadId: string | null;
  enabled?: boolean;
}

export function useDatasetUploadStream(
  options: UseDatasetUploadStreamOptions,
): DatasetUploadStreamState {
  const [state, setState] = useState<DatasetUploadStreamState>({
    progress: null,
    error: null,
    isStreaming: false,
  });

  const url = options.uploadId
    ? `/datasets/upload/${encodeURIComponent(options.uploadId)}/stream`
    : null;

  useEffect(() => {
    if (!url || options.enabled === false) return;
    if (config.useMock) return;

    setState({ progress: null, error: null, isStreaming: true });

    const unsubscribe = subscribeSSE<unknown>(
      { url },
      {
        onEvent: (event) => {
          if (event.type === "progress") {
            const data = event.data as Partial<UploadProgress> &
              Record<string, unknown>;
            setState((prev) => ({
              ...prev,
              progress: {
                upload_id: options.uploadId ?? "",
                status: (data.status as UploadProgress["status"]) ?? "running",
                processed:
                  typeof data.processed === "number"
                    ? data.processed
                    : typeof data.completed === "number"
                      ? data.completed
                      : 0,
                completed:
                  typeof data.completed === "number"
                    ? data.completed
                    : undefined,
                failed: typeof data.failed === "number" ? data.failed : 0,
                total: typeof data.total === "number" ? data.total : 0,
              },
            }));
          } else if (event.type === "done") {
            const data = event.data as Record<string, unknown>;
            setState((prev) => ({
              ...prev,
              progress: prev.progress
                ? {
                    ...prev.progress,
                    status: "completed",
                    processed: prev.progress.total,
                  }
                : {
                    upload_id: options.uploadId ?? "",
                    status: "completed",
                    processed:
                      typeof data.items_created === "number"
                        ? (data.items_created as number)
                        : 0,
                    failed:
                      typeof data.items_failed === "number"
                        ? (data.items_failed as number)
                        : 0,
                    total:
                      typeof data.items_created === "number"
                        ? (data.items_created as number)
                        : 0,
                  },
              isStreaming: false,
            }));
          } else if (event.type === "error") {
            const msg =
              (event.data as { message?: string })?.message ?? "upload error";
            setState((prev) => ({
              ...prev,
              error: msg,
              isStreaming: false,
            }));
          }
        },
        onError: (err) => {
          setState((prev) => ({
            ...prev,
            error: err.message,
            isStreaming: false,
          }));
        },
        onClose: () => {
          setState((prev) => ({ ...prev, isStreaming: false }));
        },
      },
    );
    return () => unsubscribe();
  }, [url, options.enabled, options.uploadId]);

  return state;
}
