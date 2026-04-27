/**
 * Backend REST API fetch 래퍼.
 *
 * 주요 기능:
 *  - URL 자동 조립: `${apiBaseUrl}/api/v1${path}`
 *  - JWT Bearer 자동 첨부 (`auth.tsx`의 `getAuthToken()` 콜백 사용)
 *  - Idempotency-Key, If-Match 헤더 지원
 *  - 401 응답 시 등록된 `onUnauthorized` 핸들러 호출 (logout/redirect)
 *  - 에러 응답을 RFC 7807 / 레거시 `{status:"error",error:{...}}`에서 추출하여 `ApiError`로 throw
 *  - 응답 ETag 보존 (call-site 에서 옵션으로 받음)
 *  - JSON / FormData / Blob body 자동 처리
 *
 * 본 모듈은 React에 의존하지 않는다 (auth context는 등록형 콜백으로 분리).
 *
 * 참조: docs/API_DESIGN.md §1, BUILD_ORDER.md 작업 7-0
 */
import { config } from "./config";
import type { ProblemDetails } from "./types/api";

// ─────────────────────────────────────────────────────────────────────
// 인증 토큰 / 401 핸들러 등록 (auth.tsx에서 호출)
// ─────────────────────────────────────────────────────────────────────

let tokenProvider: () => string | null = () => null;
let unauthorizedHandler: (() => void) | null = null;

export function registerAuthTokenProvider(provider: () => string | null): void {
  tokenProvider = provider;
}

export function registerUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler;
}

export function getAuthToken(): string | null {
  return tokenProvider();
}

// ─────────────────────────────────────────────────────────────────────
// ApiError
// ─────────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly detail?: string;
  readonly type?: string;
  readonly problem?: ProblemDetails;

  constructor(params: {
    status: number;
    title: string;
    code?: string;
    detail?: string;
    type?: string;
    problem?: ProblemDetails;
  }) {
    super(params.title);
    this.name = "ApiError";
    this.status = params.status;
    this.code = params.code;
    this.detail = params.detail;
    this.type = params.type;
    this.problem = params.problem;
  }
}

// ─────────────────────────────────────────────────────────────────────
// 공개 타입
// ─────────────────────────────────────────────────────────────────────

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  query?: Record<
    string,
    string | number | boolean | string[] | null | undefined
  >;
  idempotencyKey?: string;
  ifMatch?: string;
  ifNoneMatch?: string;
  /** Authorization 헤더 강제 비활성 (예: /health) */
  skipAuth?: boolean;
  signal?: AbortSignal;
}

export interface ApiResponse<T> {
  data: T;
  etag: string | null;
  status: number;
  headers: Headers;
}

// ─────────────────────────────────────────────────────────────────────
// 내부 헬퍼
// ─────────────────────────────────────────────────────────────────────

function buildUrl(
  path: string,
  query?: RequestOptions["query"],
): string {
  // path는 `/`로 시작해야 한다. 절대 URL이면 그대로 사용 (테스트 편의).
  let url: string;
  if (/^https?:\/\//i.test(path)) {
    url = path;
  } else {
    const base = config.apiBaseUrl.replace(/\/$/, "");
    const p = path.startsWith("/") ? path : `/${path}`;
    // path가 이미 /api/v1 로 시작하지 않으면 추가
    const withPrefix = p.startsWith("/api/v1") ? p : `/api/v1${p}`;
    url = `${base}${withPrefix}`;
  }

  if (!query) return url;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      for (const v of value) {
        if (v !== null && v !== undefined) params.append(key, String(v));
      }
    } else {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  if (!qs) return url;
  return url.includes("?") ? `${url}&${qs}` : `${url}?${qs}`;
}

function isFormDataLike(body: unknown): body is FormData {
  return typeof FormData !== "undefined" && body instanceof FormData;
}

function isBlobLike(body: unknown): body is Blob {
  return typeof Blob !== "undefined" && body instanceof Blob;
}

async function parseErrorBody(
  response: Response,
): Promise<{
  title: string;
  detail?: string;
  code?: string;
  type?: string;
  problem?: ProblemDetails;
}> {
  const contentType = response.headers.get("content-type") ?? "";
  const fallbackTitle = `HTTP ${response.status} ${response.statusText || "Error"}`;

  if (!contentType.includes("json")) {
    let text = "";
    try {
      text = await response.text();
    } catch {
      text = "";
    }
    return { title: fallbackTitle, detail: text || undefined };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { title: fallbackTitle };
  }

  if (body && typeof body === "object") {
    const obj = body as Record<string, unknown>;
    // RFC 7807 Problem Details
    if (
      typeof obj.title === "string" ||
      typeof obj.type === "string" ||
      typeof obj.detail === "string"
    ) {
      const problem = obj as ProblemDetails;
      return {
        title: typeof obj.title === "string" ? obj.title : fallbackTitle,
        detail: typeof obj.detail === "string" ? obj.detail : undefined,
        code: typeof obj.code === "string" ? obj.code : undefined,
        type: typeof obj.type === "string" ? obj.type : undefined,
        problem,
      };
    }
    // 레거시 `{status:"error", error:{code, message}}`
    if (
      obj.status === "error" &&
      obj.error &&
      typeof obj.error === "object"
    ) {
      const err = obj.error as Record<string, unknown>;
      return {
        title:
          typeof err.message === "string" ? err.message : fallbackTitle,
        code: typeof err.code === "string" ? err.code : undefined,
      };
    }
  }
  return { title: fallbackTitle };
}

// ─────────────────────────────────────────────────────────────────────
// 메인 API 요청 함수
// ─────────────────────────────────────────────────────────────────────

export async function apiRequest<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { data } = await apiRequestRaw<T>(path, options);
  return data;
}

export async function apiRequestRaw<T = unknown>(
  path: string,
  options: RequestOptions = {},
): Promise<ApiResponse<T>> {
  const {
    body,
    query,
    idempotencyKey,
    ifMatch,
    ifNoneMatch,
    skipAuth,
    headers: optHeaders,
    method,
    signal,
    ...rest
  } = options;

  const url = buildUrl(path, query);
  const headers = new Headers(optHeaders);

  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  if (!skipAuth) {
    const token = getAuthToken();
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  if (ifMatch) headers.set("If-Match", ifMatch);
  if (ifNoneMatch) headers.set("If-None-Match", ifNoneMatch);

  let finalBody: BodyInit | null = null;
  if (body !== undefined && body !== null) {
    if (isFormDataLike(body) || isBlobLike(body)) {
      finalBody = body as BodyInit;
      // Content-Type은 브라우저가 boundary와 함께 자동 설정
    } else if (typeof body === "string") {
      finalBody = body;
      if (!headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }
    } else {
      finalBody = JSON.stringify(body);
      if (!headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }
    }
  }

  const requestMethod =
    method ?? (finalBody !== null ? "POST" : "GET");

  const response = await fetch(url, {
    ...rest,
    method: requestMethod,
    headers,
    body: finalBody,
    signal,
    credentials: rest.credentials ?? "omit",
  });

  // 304 Not Modified — 본문 없이 캐시 적중
  if (response.status === 304) {
    return {
      data: undefined as unknown as T,
      etag: response.headers.get("etag"),
      status: 304,
      headers: response.headers,
    };
  }

  if (response.status === 401 && !skipAuth) {
    if (unauthorizedHandler) unauthorizedHandler();
    const err = await parseErrorBody(response);
    throw new ApiError({
      status: 401,
      title: err.title || "Unauthorized",
      detail: err.detail,
      code: err.code ?? "AUTH_REQUIRED",
      type: err.type,
      problem: err.problem,
    });
  }

  if (!response.ok) {
    const err = await parseErrorBody(response);
    throw new ApiError({
      status: response.status,
      title: err.title,
      detail: err.detail,
      code: err.code,
      type: err.type,
      problem: err.problem,
    });
  }

  const etag = response.headers.get("etag");

  // 204 No Content
  if (response.status === 204) {
    return {
      data: undefined as unknown as T,
      etag,
      status: 204,
      headers: response.headers,
    };
  }

  const contentType = response.headers.get("content-type") ?? "";

  let parsed: unknown;
  if (contentType.includes("application/json")) {
    parsed = await response.json();
  } else if (
    contentType.startsWith("text/") ||
    contentType.includes("xml")
  ) {
    parsed = await response.text();
  } else {
    parsed = await response.blob();
  }

  return {
    data: parsed as T,
    etag,
    status: response.status,
    headers: response.headers,
  };
}

// ─────────────────────────────────────────────────────────────────────
// multipart/form-data 업로드
// ─────────────────────────────────────────────────────────────────────

export interface UploadOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
  query?: RequestOptions["query"];
}

export async function apiUpload<T = unknown>(
  path: string,
  formData: FormData,
  options: UploadOptions = {},
): Promise<T> {
  return apiRequest<T>(path, {
    method: "POST",
    body: formData,
    idempotencyKey: options.idempotencyKey,
    signal: options.signal,
    query: options.query,
  });
}
