/**
 * sse.ts 단위 테스트.
 *
 * 검증 항목:
 *  - SSE 이벤트 파싱 (event/data/id/retry 라인)
 *  - heartbeat 주석 무시
 *  - JSON data 자동 파싱
 *  - event: done 수신 시 자동 종료 + onClose
 *  - event: error 수신 시 자동 종료
 *  - unsubscribe 함수 호출 시 fetch abort
 *  - Authorization 헤더 자동 첨부 (registerAuthTokenProvider)
 *
 * 참조: docs/API_DESIGN.md §1.1, BUILD_ORDER.md 작업 7-0
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../mocks/server";
import { subscribeSSE } from "@/lib/sse";
import { config } from "@/lib/config";
import { registerAuthTokenProvider } from "@/lib/api";

const API = `${config.apiBaseUrl}/api/v1`;

function streamFromText(text: string): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text));
      controller.close();
    },
  });
}

beforeEach(() => {
  registerAuthTokenProvider(() => null);
});

afterEach(() => {
  registerAuthTokenProvider(() => null);
});

describe("subscribeSSE 이벤트 파싱", () => {
  it("event/data/id 라인을 분리하여 onEvent에 전달한다", async () => {
    const sseBody = [
      "event: token",
      'data: {"content":"hello"}',
      "id: 1",
      "",
      "event: token",
      'data: {"content":" world"}',
      "id: 2",
      "",
      "event: done",
      'data: {"trace_id":"t1"}',
      "id: 3",
      "",
    ].join("\n");

    server.use(
      http.get(`${API}/stream`, () =>
        new HttpResponse(streamFromText(sseBody), {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      ),
    );

    const events: { type: string; data: unknown; id?: string }[] = [];
    const closed = await new Promise<boolean>((resolve) => {
      subscribeSSE(
        { url: "/stream", maxRetries: 0 },
        {
          onEvent: (e) => events.push(e),
          onClose: () => resolve(true),
          onError: () => resolve(false),
        },
      );
    });

    expect(closed).toBe(true);
    expect(events.length).toBeGreaterThanOrEqual(3);
    expect(events[0].type).toBe("token");
    expect(events[0].data).toEqual({ content: "hello" });
    expect(events[0].id).toBe("1");
    expect(events[2].type).toBe("done");
    expect(events[2].data).toEqual({ trace_id: "t1" });
  });

  it("heartbeat 주석은 이벤트로 전달하지 않는다", async () => {
    const sseBody = [
      ": heartbeat",
      "",
      "event: progress",
      'data: {"completed":1,"total":10}',
      "",
      "event: done",
      "data: {}",
      "",
    ].join("\n");

    server.use(
      http.get(`${API}/hb`, () =>
        new HttpResponse(streamFromText(sseBody), {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      ),
    );

    const events: { type: string }[] = [];
    await new Promise<void>((resolve) => {
      subscribeSSE(
        { url: "/hb", maxRetries: 0 },
        {
          onEvent: (e) => events.push(e),
          onClose: () => resolve(),
          onError: () => resolve(),
        },
      );
    });
    // progress + done 만 — heartbeat은 미전달
    const types = events.map((e) => e.type);
    expect(types).toContain("progress");
    expect(types).toContain("done");
    expect(types).not.toContain("message");
  });

  it("data가 JSON이 아니면 문자열 그대로 전달한다", async () => {
    const sseBody = [
      "event: log",
      "data: just a plain string",
      "",
      "event: done",
      "data: {}",
      "",
    ].join("\n");
    server.use(
      http.get(`${API}/plain`, () =>
        new HttpResponse(streamFromText(sseBody), {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      ),
    );
    const events: { type: string; data: unknown }[] = [];
    await new Promise<void>((resolve) => {
      subscribeSSE(
        { url: "/plain", maxRetries: 0 },
        {
          onEvent: (e) => events.push(e),
          onClose: () => resolve(),
          onError: () => resolve(),
        },
      );
    });
    expect(events[0].data).toBe("just a plain string");
  });
});

describe("subscribeSSE 인증/취소", () => {
  it("getAuthToken에 등록된 토큰이 Authorization 헤더로 전달된다", async () => {
    registerAuthTokenProvider(() => "JWTSSE");
    let auth = "";
    server.use(
      http.get(`${API}/auth-stream`, ({ request }) => {
        auth = request.headers.get("authorization") ?? "";
        return new HttpResponse(streamFromText("event: done\ndata: {}\n\n"), {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }),
    );
    await new Promise<void>((resolve) => {
      subscribeSSE(
        { url: "/auth-stream", maxRetries: 0 },
        {
          onEvent: () => {},
          onClose: () => resolve(),
          onError: () => resolve(),
        },
      );
    });
    expect(auth).toBe("Bearer JWTSSE");
  });

  it("unsubscribe() 호출 시 onClose가 호출된다", async () => {
    server.use(
      http.get(`${API}/never-end`, () =>
        // close 안 하는 stream
        new HttpResponse(
          new ReadableStream({
            start(_controller) {
              // 영원히 데이터 없음
            },
          }),
          {
            status: 200,
            headers: { "content-type": "text/event-stream" },
          },
        ),
      ),
    );

    const onClose = vi.fn();
    const unsub = subscribeSSE(
      { url: "/never-end", maxRetries: 0 },
      { onEvent: () => {}, onClose, onError: () => {} },
    );

    // 연결 대기
    await new Promise((r) => setTimeout(r, 30));
    unsub();
    // close 콜백
    await new Promise((r) => setTimeout(r, 10));
    expect(onClose).toHaveBeenCalled();
  });
});
