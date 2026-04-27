/**
 * Server-Sent Events 클라이언트.
 *
 * 표준 EventSource는 Authorization 헤더를 지원하지 않아 fetch + ReadableStream으로 구현한다.
 * RFC 7807 SSE 규약(API_DESIGN §1.1):
 *   - id: 단조 증가, Last-Event-ID 헤더로 재접속 시 활용
 *   - retry: <ms> 라인에서 재시도 간격 갱신
 *   - 15초 heartbeat 주석
 *   - event: done | error 종결
 *
 * 사용:
 *   const close = subscribeSSE({ url: "/experiments/X/stream", token: "..." }, {
 *     onEvent: (e) => console.log(e),
 *     onError: (err) => console.error(err),
 *   });
 *   close(); // 구독 해제
 *
 * 참조: docs/API_DESIGN.md §1.1 SSE 포맷
 */
import { config } from "./config";
import { getAuthToken } from "./api";

export interface SSEEvent<T = unknown> {
  type: string;
  data: T;
  id?: string;
}

export interface SSEEventHandler<T = unknown> {
  onEvent: (event: SSEEvent<T>) => void;
  onError?: (err: Error) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

export interface SSEOptions {
  /** 절대 URL이거나 `/api/v1/...` 경로 */
  url: string;
  /** 쿼리 파라미터 (선택) */
  query?: Record<
    string,
    string | number | boolean | string[] | null | undefined
  >;
  /** 명시적 토큰. 미지정 시 `getAuthToken()` 사용 */
  token?: string | null;
  /** 마지막으로 수신한 이벤트 id (재접속 시 서버 재전송) */
  lastEventId?: string;
  /** 초기 retry 간격 (ms). 서버 retry 라인이 우선. 기본 3000 */
  retryDelay?: number;
  /** 최대 재시도 횟수 (기본 5). 0이면 재시도 없음 */
  maxRetries?: number;
  /** 외부 abort */
  signal?: AbortSignal;
}

/**
 * SSE 구독을 시작하고 unsubscribe 함수를 반환한다.
 *
 * 동작:
 *  - 내부 AbortController로 fetch 취소
 *  - body.getReader()로 stream chunk 수신 → SSE event 파싱
 *  - 끊어지면 retryDelay 후 재접속 (Last-Event-ID 자동 첨부, maxRetries까지)
 *  - `event: done` 또는 `event: error` 수신 시 자동 종료 (재시도 없음)
 *  - `signal.abort()` 또는 반환된 함수 호출 시 즉시 종료
 */
export function subscribeSSE<T = unknown>(
  options: SSEOptions,
  handler: SSEEventHandler<T>,
): () => void {
  const controller = new AbortController();
  const externalSignal = options.signal;
  if (externalSignal) {
    if (externalSignal.aborted) {
      controller.abort();
    } else {
      externalSignal.addEventListener("abort", () => controller.abort(), {
        once: true,
      });
    }
  }

  let lastEventId = options.lastEventId ?? "";
  let retryDelay = options.retryDelay ?? 3000;
  const maxRetries = options.maxRetries ?? 5;
  let attempt = 0;
  let stopped = false;

  function buildUrl(): string {
    let url: string;
    if (/^https?:\/\//i.test(options.url)) {
      url = options.url;
    } else {
      const base = config.apiBaseUrl.replace(/\/$/, "");
      const p = options.url.startsWith("/") ? options.url : `/${options.url}`;
      const withPrefix = p.startsWith("/api/v1") ? p : `/api/v1${p}`;
      url = `${base}${withPrefix}`;
    }
    if (!options.query) return url;
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(options.query)) {
      if (v === null || v === undefined) continue;
      if (Array.isArray(v)) {
        for (const x of v) {
          if (x !== null && x !== undefined) params.append(k, String(x));
        }
      } else {
        params.append(k, String(v));
      }
    }
    const qs = params.toString();
    if (!qs) return url;
    return url.includes("?") ? `${url}&${qs}` : `${url}?${qs}`;
  }

  let activeReader: ReadableStreamDefaultReader<Uint8Array> | null = null;

  async function connect(): Promise<void> {
    if (stopped) return;
    const url = buildUrl();
    const headers = new Headers();
    headers.set("Accept", "text/event-stream");
    headers.set("Cache-Control", "no-cache");

    const token =
      options.token === undefined ? getAuthToken() : options.token;
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (lastEventId) headers.set("Last-Event-ID", lastEventId);

    // jsdom/undici 호환: AbortSignal을 fetch에 직접 전달하면 realm 충돌이 발생할 수 있어
    // signal을 제외하고 fetch한 뒤, abort 시 reader.cancel()로 스트림을 종료한다.
    let response: Response;
    try {
      response = await fetch(url, {
        method: "GET",
        headers,
        credentials: "omit",
      });
    } catch (err) {
      if (stopped || controller.signal.aborted) return;
      handler.onError?.(err instanceof Error ? err : new Error(String(err)));
      scheduleRetry();
      return;
    }

    if (!response.ok || !response.body) {
      handler.onError?.(
        new Error(`SSE connection failed: ${response.status}`),
      );
      // 4xx는 보통 재시도해도 의미 없음 → 401/403 등은 종료
      if (response.status === 401 || response.status === 403) {
        cleanup();
        return;
      }
      scheduleRetry();
      return;
    }

    handler.onOpen?.();
    attempt = 0; // 성공 시 재시도 카운터 리셋

    const reader = response.body.getReader();
    activeReader = reader;
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    try {
      while (!stopped) {
        const { value, done } = await reader.read();
        if (done) {
          // EOF: 남은 버퍼에 미종결 이벤트가 있으면 flush
          buffer += decoder.decode();
          if (buffer.trim().length > 0) {
            handleRawEvent(buffer);
            buffer = "";
          }
          break;
        }
        buffer += decoder.decode(value, { stream: true });

        // SSE는 \n\n 또는 \r\n\r\n 으로 이벤트 분리
        let sepIdx: number;
        while (
          (sepIdx = buffer.search(/\r?\n\r?\n/)) !== -1
        ) {
          const rawEvent = buffer.slice(0, sepIdx);
          // 분리자 길이 (\n\n=2, \r\n\r\n=4)
          const match = buffer.slice(sepIdx).match(/^\r?\n\r?\n/);
          const sepLen = match ? match[0].length : 2;
          buffer = buffer.slice(sepIdx + sepLen);
          handleRawEvent(rawEvent);
          if (stopped) break;
        }
      }
    } catch (err) {
      if (!stopped && !controller.signal.aborted) {
        handler.onError?.(
          err instanceof Error ? err : new Error(String(err)),
        );
        scheduleRetry();
      }
      return;
    }

    if (!stopped) {
      // 서버가 정상 종료 (done 미전송)
      scheduleRetry();
    }
  }

  function handleRawEvent(raw: string): void {
    if (!raw) return;
    let eventType = "message";
    let eventTypeExplicit = false;
    const dataLines: string[] = [];
    let eventId: string | undefined;
    let hasField = false;

    for (const line of raw.split(/\r?\n/)) {
      if (!line) continue;
      if (line.startsWith(":")) continue; // comment / heartbeat
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);

      switch (field) {
        case "event":
          eventType = value || "message";
          eventTypeExplicit = true;
          hasField = true;
          break;
        case "data":
          dataLines.push(value);
          hasField = true;
          break;
        case "id":
          eventId = value;
          lastEventId = value;
          hasField = true;
          break;
        case "retry": {
          const n = Number.parseInt(value, 10);
          if (Number.isFinite(n) && n >= 0) retryDelay = n;
          hasField = true;
          break;
        }
        default:
          break;
      }
    }

    // 모든 라인이 주석이거나 비어있는 경우 dispatch하지 않음
    if (!hasField) return;
    // 타입 미지정 + 데이터 없음 → 의미 없는 이벤트는 dispatch하지 않음
    if (!eventTypeExplicit && dataLines.length === 0) return;

    const dataStr = dataLines.join("\n");
    let parsed: unknown = dataStr;
    if (dataStr) {
      try {
        parsed = JSON.parse(dataStr);
      } catch {
        parsed = dataStr;
      }
    }

    handler.onEvent({ type: eventType, data: parsed as T, id: eventId });

    if (eventType === "done" || eventType === "error") {
      cleanup();
    }
  }

  function scheduleRetry(): void {
    if (stopped) return;
    if (attempt >= maxRetries) {
      cleanup();
      return;
    }
    attempt += 1;
    const delay = retryDelay;
    setTimeout(() => {
      if (!stopped) void connect();
    }, delay);
  }

  function cleanup(): void {
    if (stopped) return;
    stopped = true;
    try {
      controller.abort();
    } catch {
      // ignore
    }
    if (activeReader) {
      try {
        void activeReader.cancel();
      } catch {
        // ignore
      }
      activeReader = null;
    }
    handler.onClose?.();
  }

  void connect();

  return () => {
    cleanup();
  };
}
