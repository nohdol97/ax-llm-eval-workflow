/**
 * 환경 변수 / 런타임 설정 단일 소스.
 *
 * 모든 환경 의존 값은 이 모듈을 통해서만 접근한다 (테스트 시 모킹 용이).
 *
 * - apiBaseUrl: 백엔드 base URL (`/api/v1`은 클라이언트가 자동 prefix)
 * - useMock: true면 React Query 훅이 mock 데이터로 응답
 * - pollInterval: 주기적 폴링 간격 (밀리초)
 *
 * 참조: BUILD_ORDER.md 작업 7-0 / API_DESIGN.md §1
 */
export const config = {
  apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  useMock: process.env.NEXT_PUBLIC_USE_MOCK === "true",
  appName: "GenAI Labs",
  pollInterval: {
    notifications: 30_000, // 30s
    experimentList: 60_000, // 60s
  },
} as const;

export type AppConfig = typeof config;
