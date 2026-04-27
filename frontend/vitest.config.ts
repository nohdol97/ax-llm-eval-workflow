/// <reference types="vitest" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// ─────────────────────────────────────────────────────────────────────
// Vitest 설정
//
// 환경: jsdom (React 컴포넌트 테스트). MSW로 fetch 가로채기.
// setup: tests/setup.ts (jest-dom matcher + cleanup).
// alias: '@/*' → 'src/*' (tsconfig paths와 동일).
//
// 참조: BUILD_ORDER.md 작업 0-3, frontend/tests/**
// ─────────────────────────────────────────────────────────────────────
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.{test,spec}.{ts,tsx}"],
    // 초기에는 커버리지 임계값 0% — 점진적 향상.
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.d.ts",
        "src/app/**/layout.tsx",
        "src/app/**/page.tsx",
      ],
      thresholds: {
        lines: 0,
        functions: 0,
        branches: 0,
        statements: 0,
      },
    },
  },
});
