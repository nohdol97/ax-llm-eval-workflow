// ESLint v9 flat config — Next.js 16 + TypeScript + React Hooks
//
// 본 프로젝트의 정적 분석 규칙. backend의 ruff에 대응.
// CI에서 `npx eslint src/`로 실행한다.

import js from "@eslint/js";
import tsEslint from "typescript-eslint";
import nextPlugin from "@next/eslint-plugin-next";
import reactHooksPlugin from "eslint-plugin-react-hooks";

export default [
  js.configs.recommended,
  ...tsEslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: {
      "@next/next": nextPlugin,
      "react-hooks": reactHooksPlugin,
    },
    languageOptions: {
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
      globals: {
        // 브라우저/Next 런타임
        window: "readonly",
        document: "readonly",
        console: "readonly",
        fetch: "readonly",
        AbortController: "readonly",
        AbortSignal: "readonly",
        Headers: "readonly",
        Request: "readonly",
        Response: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        FormData: "readonly",
        Blob: "readonly",
        File: "readonly",
        FileReader: "readonly",
        TextEncoder: "readonly",
        TextDecoder: "readonly",
        ReadableStream: "readonly",
        TransformStream: "readonly",
        EventSource: "readonly",
        localStorage: "readonly",
        sessionStorage: "readonly",
        navigator: "readonly",
        history: "readonly",
        location: "readonly",
        addEventListener: "readonly",
        removeEventListener: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        queueMicrotask: "readonly",
        crypto: "readonly",
        btoa: "readonly",
        atob: "readonly",
        Notification: "readonly",
        // Node 빌드 타임
        process: "readonly",
        Buffer: "readonly",
        // React 19 자동 import
        React: "readonly",
        JSX: "readonly",
      },
    },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
      ...reactHooksPlugin.configs.recommended.rules,

      // 본 프로젝트 정책
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-empty-object-type": "off",
      "no-console": ["warn", { allow: ["warn", "error"] }],
      "prefer-const": "error",
      "no-var": "error",
      "eqeqeq": ["error", "smart"],
    },
  },
  {
    // 테스트는 좀 더 느슨하게
    files: ["tests/**/*.{ts,tsx}", "**/*.test.{ts,tsx}"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
      "no-console": "off",
    },
  },
  {
    // 무시 대상
    ignores: [
      ".next/**",
      "node_modules/**",
      "out/**",
      "dist/**",
      "next-env.d.ts",
      "*.config.{js,mjs,ts}",
      "eslint.config.mjs",
      "vitest.config.ts",
      "next.config.ts",
      "postcss.config.mjs",
      "src/lib/mock/**",
    ],
  },
];
