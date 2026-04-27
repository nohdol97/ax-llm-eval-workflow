/**
 * api.ts 단위 테스트.
 *
 * 검증 항목:
 *  - URL 자동 조립 (`/api/v1` prefix 부착)
 *  - JWT Bearer 자동 첨부
 *  - 401 응답 시 unauthorizedHandler 호출
 *  - RFC 7807 Problem Details → ApiError 변환
 *  - 레거시 `{status:"error",error:{...}}` → ApiError 변환
 *  - Idempotency-Key / If-Match 헤더 전달
 *  - ETag 응답 헤더 노출
 *  - JSON / FormData body 분기
 *  - skipAuth 옵션
 *
 * 참조: docs/API_DESIGN.md §1.1, BUILD_ORDER.md 작업 7-0
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "../../mocks/server";
import {
  ApiError,
  apiRequest,
  apiRequestRaw,
  apiUpload,
  registerAuthTokenProvider,
  registerUnauthorizedHandler,
} from "@/lib/api";
import { config } from "@/lib/config";

const BASE = config.apiBaseUrl;
const API = `${BASE}/api/v1`;

beforeEach(() => {
  registerAuthTokenProvider(() => null);
  registerUnauthorizedHandler(() => {});
});

afterEach(() => {
  registerAuthTokenProvider(() => null);
  registerUnauthorizedHandler(() => {});
});

describe("apiRequest URL 조립", () => {
  it("path에 /api/v1 prefix가 없으면 자동으로 부착한다", async () => {
    let capturedUrl = "";
    server.use(
      http.get(`${API}/foo/bar`, ({ request }) => {
        capturedUrl = request.url;
        return HttpResponse.json({ ok: true });
      }),
    );
    const data = await apiRequest<{ ok: boolean }>("/foo/bar");
    expect(data.ok).toBe(true);
    expect(capturedUrl).toBe(`${API}/foo/bar`);
  });

  it("path가 이미 /api/v1로 시작하면 중복 부착하지 않는다", async () => {
    server.use(
      http.get(`${API}/already`, () => HttpResponse.json({ ok: 1 })),
    );
    await expect(
      apiRequest<{ ok: number }>("/api/v1/already"),
    ).resolves.toEqual({ ok: 1 });
  });

  it("query 파라미터를 URL에 직렬화한다", async () => {
    let captured: URL | null = null;
    server.use(
      http.get(`${API}/items`, ({ request }) => {
        captured = new URL(request.url);
        return HttpResponse.json({});
      }),
    );
    await apiRequest("/items", {
      query: { project_id: "p1", page: 2, archived: false, tags: ["a", "b"] },
    });
    expect(captured!.searchParams.get("project_id")).toBe("p1");
    expect(captured!.searchParams.get("page")).toBe("2");
    expect(captured!.searchParams.get("archived")).toBe("false");
    expect(captured!.searchParams.getAll("tags")).toEqual(["a", "b"]);
  });

  it("query 의 null/undefined 값은 무시한다", async () => {
    let captured: URL | null = null;
    server.use(
      http.get(`${API}/items`, ({ request }) => {
        captured = new URL(request.url);
        return HttpResponse.json({});
      }),
    );
    await apiRequest("/items", { query: { a: null, b: undefined, c: "x" } });
    expect(captured!.searchParams.has("a")).toBe(false);
    expect(captured!.searchParams.has("b")).toBe(false);
    expect(captured!.searchParams.get("c")).toBe("x");
  });
});

describe("apiRequest 인증 헤더", () => {
  it("토큰이 있으면 Authorization Bearer를 자동 첨부한다", async () => {
    registerAuthTokenProvider(() => "JWT123");
    let auth = "";
    server.use(
      http.get(`${API}/secure`, ({ request }) => {
        auth = request.headers.get("authorization") ?? "";
        return HttpResponse.json({ ok: true });
      }),
    );
    await apiRequest("/secure");
    expect(auth).toBe("Bearer JWT123");
  });

  it("토큰이 없으면 Authorization 헤더를 첨부하지 않는다", async () => {
    let auth: string | null = "init";
    server.use(
      http.get(`${API}/anon`, ({ request }) => {
        auth = request.headers.get("authorization");
        return HttpResponse.json({ ok: true });
      }),
    );
    await apiRequest("/anon");
    expect(auth).toBeNull();
  });

  it("skipAuth=true면 토큰이 있어도 첨부하지 않는다 (예: /health)", async () => {
    registerAuthTokenProvider(() => "JWT123");
    let auth: string | null = "init";
    server.use(
      http.get(`${API}/health`, ({ request }) => {
        auth = request.headers.get("authorization");
        return HttpResponse.json({ status: "ok" });
      }),
    );
    await apiRequest("/health", { skipAuth: true });
    expect(auth).toBeNull();
  });
});

describe("apiRequest 401 처리", () => {
  it("401 응답 시 unauthorizedHandler를 호출하고 ApiError를 throw 한다", async () => {
    const handler = vi.fn();
    registerUnauthorizedHandler(handler);
    server.use(
      http.get(`${API}/private`, () =>
        HttpResponse.json(
          { type: "about:blank", title: "Unauthorized", status: 401, code: "AUTH_REQUIRED" },
          { status: 401 },
        ),
      ),
    );
    await expect(apiRequest("/private")).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("skipAuth=true면 401이어도 핸들러를 호출하지 않는다", async () => {
    const handler = vi.fn();
    registerUnauthorizedHandler(handler);
    server.use(
      http.get(`${API}/health`, () =>
        HttpResponse.json({ title: "no", status: 401 }, { status: 401 }),
      ),
    );
    await expect(
      apiRequest("/health", { skipAuth: true }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
  });
});

describe("apiRequest 에러 본문 파싱", () => {
  it("RFC 7807 Problem Details를 ApiError로 변환한다", async () => {
    server.use(
      http.get(`${API}/err`, () =>
        HttpResponse.json(
          {
            type: "https://example.com/probs/validation",
            title: "Validation Error",
            status: 422,
            detail: "field 'name' is required",
            code: "VALIDATION_ERROR",
          },
          { status: 422 },
        ),
      ),
    );
    let caught: ApiError | null = null;
    try {
      await apiRequest("/err");
    } catch (e) {
      caught = e as ApiError;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught!.status).toBe(422);
    expect(caught!.code).toBe("VALIDATION_ERROR");
    expect(caught!.detail).toBe("field 'name' is required");
    expect(caught!.message).toBe("Validation Error");
    expect(caught!.type).toBe("https://example.com/probs/validation");
  });

  it("레거시 {status:'error', error:{code, message}} 본문을 변환한다", async () => {
    server.use(
      http.get(`${API}/legacy`, () =>
        HttpResponse.json(
          {
            status: "error",
            error: { code: "PROMPT_NOT_FOUND", message: "프롬프트 없음" },
          },
          { status: 404 },
        ),
      ),
    );
    let caught: ApiError | null = null;
    try {
      await apiRequest("/legacy");
    } catch (e) {
      caught = e as ApiError;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught!.status).toBe(404);
    expect(caught!.code).toBe("PROMPT_NOT_FOUND");
    expect(caught!.message).toBe("프롬프트 없음");
  });

  it("JSON이 아닌 에러 응답도 ApiError로 변환한다", async () => {
    server.use(
      http.get(`${API}/text-err`, () =>
        new HttpResponse("plain text body", {
          status: 500,
          headers: { "content-type": "text/plain" },
        }),
      ),
    );
    let caught: ApiError | null = null;
    try {
      await apiRequest("/text-err");
    } catch (e) {
      caught = e as ApiError;
    }
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught!.status).toBe(500);
    expect(caught!.detail).toContain("plain text body");
  });
});

describe("apiRequest 헤더 처리", () => {
  it("Idempotency-Key 헤더를 전달한다", async () => {
    let key = "";
    server.use(
      http.post(`${API}/exp`, async ({ request }) => {
        key = request.headers.get("idempotency-key") ?? "";
        return HttpResponse.json({ id: "x" });
      }),
    );
    await apiRequest("/exp", {
      method: "POST",
      body: { foo: 1 },
      idempotencyKey: "uuid-1234",
    });
    expect(key).toBe("uuid-1234");
  });

  it("If-Match 헤더를 전달한다", async () => {
    let etag = "";
    server.use(
      http.patch(`${API}/labels`, async ({ request }) => {
        etag = request.headers.get("if-match") ?? "";
        return HttpResponse.json({ ok: true });
      }),
    );
    await apiRequest("/labels", {
      method: "PATCH",
      body: { labels: ["production"] },
      ifMatch: "abc123",
    });
    expect(etag).toBe("abc123");
  });

  it("응답 ETag를 raw 응답에서 노출한다", async () => {
    server.use(
      http.get(`${API}/etagged`, () =>
        HttpResponse.json(
          { foo: 1 },
          { headers: { etag: "xyz789" } },
        ),
      ),
    );
    const res = await apiRequestRaw<{ foo: number }>("/etagged");
    expect(res.etag).toBe("xyz789");
    expect(res.data.foo).toBe(1);
  });
});

describe("apiRequest body 처리", () => {
  it("객체 body는 JSON.stringify로 직렬화하고 Content-Type을 자동 설정한다", async () => {
    let ct = "";
    let parsed: unknown = null;
    server.use(
      http.post(`${API}/json`, async ({ request }) => {
        ct = request.headers.get("content-type") ?? "";
        parsed = await request.json();
        return HttpResponse.json({ ok: true });
      }),
    );
    await apiRequest("/json", { method: "POST", body: { hello: "world" } });
    expect(ct).toContain("application/json");
    expect(parsed).toEqual({ hello: "world" });
  });

  it("FormData body는 JSON.stringify하지 않으며 application/json을 강제하지 않는다", async () => {
    // 참고: jsdom + Node 네이티브 fetch는 FormData multipart 직렬화를 완전히 지원하지 않으므로
    // 본 테스트는 "Content-Type이 application/json으로 설정되지 않는다"만 검증한다.
    // 실제 브라우저에서는 multipart/form-data; boundary=... 가 자동 설정된다.
    let ct = "";
    server.use(
      http.post(`${API}/upload`, async ({ request }) => {
        ct = request.headers.get("content-type") ?? "";
        return HttpResponse.json({ uploaded: true });
      }),
    );
    const fd = new FormData();
    fd.append("file", new Blob(["abc"]), "x.txt");
    await apiUpload("/upload", fd);
    expect(ct).not.toContain("application/json");
  });
});

describe("apiRequest 204 / 304", () => {
  it("204 No Content는 undefined data를 반환한다", async () => {
    server.use(
      http.delete(`${API}/x`, () => new HttpResponse(null, { status: 204 })),
    );
    const res = await apiRequestRaw("/x", { method: "DELETE" });
    expect(res.status).toBe(204);
    expect(res.data).toBeUndefined();
  });
});
