"use client";
/**
 * TanStack Query Provider.
 *
 * 기본 옵션:
 *  - staleTime 30s: 짧은 폴링 시 즉시 cache hit
 *  - gcTime 5min: 메모리 캐시 보관
 *  - retry 1회: 일시적 네트워크 오류 보완 (4xx는 ApiError로 throw → retry 안 됨)
 *  - refetchOnWindowFocus false: 사용자 혼란 방지 (실험 결과는 SSE로 push)
 *  - mutations retry 0: 중복 변이 방지 (서버 Idempotency-Key로 보호되더라도 클라이언트 재시도 안함)
 *
 * 참조: BUILD_ORDER.md 작업 7-0
 */
import {
  QueryClient,
  QueryClientProvider,
  type DefaultOptions,
} from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { ApiError } from "./api";

const defaultOptions: DefaultOptions = {
  queries: {
    staleTime: 30_000,
    gcTime: 5 * 60_000,
    retry: (failureCount, error) => {
      // 4xx는 재시도 의미 없음
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        return false;
      }
      return failureCount < 1;
    },
    refetchOnWindowFocus: false,
  },
  mutations: {
    retry: 0,
  },
};

export function createQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions });
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState<QueryClient>(() => createQueryClient());
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}
