/**
 * Vitest 글로벌 setup.
 *
 * - @testing-library/jest-dom matcher 등록 (toBeInTheDocument 등)
 * - 각 테스트 후 React Testing Library cleanup (DOM 누수 방지)
 * - MSW 서버 라이프사이클 (listen / resetHandlers / close)
 *
 * 참조: BUILD_ORDER.md 작업 0-3
 */
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import { server } from "./mocks/server";

// MSW: 모든 테스트 시작 전 1회 listen
beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

// 각 테스트 후: React DOM 정리 + MSW 핸들러 초기화
afterEach(() => {
  cleanup();
  server.resetHandlers();
});

// 모든 테스트 종료 후: MSW close
afterAll(() => {
  server.close();
});
