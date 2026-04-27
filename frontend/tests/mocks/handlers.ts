/**
 * MSW 핸들러 — Backend API placeholder 응답.
 *
 * Phase 2 이후 실제 API 스키마가 확정되면 본 파일을 갱신한다.
 * 현재는 frontend가 API에 의존하지 않으므로 최소 placeholder만 제공.
 *
 * 참조: BUILD_ORDER.md 작업 0-3, API_DESIGN.md
 */
import { http, HttpResponse } from "msw";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export const handlers = [
  // ── Health check ────────────────────────────────────────────────
  http.get(`${API_BASE}/health`, () =>
    HttpResponse.json({
      status: "ok",
      services: {
        langfuse: "ok",
        litellm: "ok",
        clickhouse: "ok",
        redis: "ok",
        prometheus: "ok",
        otel: "ok",
        loki: "ok",
      },
    })
  ),

  // ── Prompts list (placeholder) ──────────────────────────────────
  http.get(`${API_BASE}/prompts`, () =>
    HttpResponse.json({
      items: [],
      total: 0,
    })
  ),

  // ── Experiments list (placeholder) ──────────────────────────────
  http.get(`${API_BASE}/experiments`, () =>
    HttpResponse.json({
      items: [],
      total: 0,
    })
  ),
];
