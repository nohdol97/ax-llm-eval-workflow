"use client";
/**
 * 인증 컨텍스트 (Client Component).
 *
 * 보안 원칙:
 *  - JWT는 메모리에만 보관 (localStorage / cookie 금지 — XSS 방어)
 *  - 토큰 서명 검증은 서버가 담당. 클라이언트는 payload(base64) decode만 수행
 *  - 401 응답 시 자동 logout (api.ts → registerUnauthorizedHandler)
 *
 * Mock 모드 (config.useMock=true):
 *  - 자동으로 admin 더미 토큰을 설정해 개발 편의 제공
 *
 * RBAC 위계: admin > user > viewer
 *
 * 참조: docs/UI_UX_DESIGN.md 인증, docs/API_DESIGN.md §1.1
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { config } from "./config";
import {
  registerAuthTokenProvider,
  registerUnauthorizedHandler,
} from "./api";
import type { JwtPayload, RBACRole, User } from "./types/api";

interface AuthContextValue {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  /** JWT 문자열을 등록한다. payload를 decode하여 user 정보를 추출 */
  login: (token: string) => void;
  /** 토큰/유저 클리어. redirect=true면 로그인 페이지로 이동 */
  logout: (options?: { redirect?: boolean }) => void;
  /** 권한 위계 비교. admin > reviewer > user > viewer */
  hasRole: (role: RBACRole) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const ROLE_RANK: Record<RBACRole, number> = {
  viewer: 1,
  user: 2,
  reviewer: 3,
  admin: 4,
};

// ─────────────────────────────────────────────────────────────────────
// JWT decode (payload only — 서명 검증은 서버가 수행)
// ─────────────────────────────────────────────────────────────────────

function base64UrlDecode(input: string): string {
  let s = input.replace(/-/g, "+").replace(/_/g, "/");
  const pad = s.length % 4;
  if (pad === 2) s += "==";
  else if (pad === 3) s += "=";
  else if (pad === 1) throw new Error("Invalid base64url string");
  if (typeof atob === "function") {
    // 브라우저 / jsdom
    const binary = atob(s);
    try {
      const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
      return new TextDecoder("utf-8").decode(bytes);
    } catch {
      return binary;
    }
  }
  // Node 환경 (서버 컴포넌트 등)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const buf = (globalThis as any).Buffer?.from?.(s, "base64");
  if (buf) return buf.toString("utf-8");
  throw new Error("No base64 decoder available");
}

export function decodeJwt(token: string): JwtPayload | null {
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const json = base64UrlDecode(parts[1]);
    const obj = JSON.parse(json) as Record<string, unknown>;
    const role = (obj.role as RBACRole) ?? "viewer";
    const sub =
      typeof obj.sub === "string"
        ? obj.sub
        : typeof obj.user_id === "string"
          ? (obj.user_id as string)
          : "";
    return {
      sub,
      email: typeof obj.email === "string" ? obj.email : undefined,
      name: typeof obj.name === "string" ? obj.name : undefined,
      role,
      groups: Array.isArray(obj.groups)
        ? (obj.groups as string[])
        : undefined,
      exp: typeof obj.exp === "number" ? (obj.exp as number) : undefined,
      iat: typeof obj.iat === "number" ? (obj.iat as number) : undefined,
    };
  } catch {
    return null;
  }
}

function payloadToUser(payload: JwtPayload): User {
  return {
    id: payload.sub,
    email: payload.email,
    name: payload.name,
    role: payload.role,
    groups: payload.groups ?? [],
  };
}

// ─────────────────────────────────────────────────────────────────────
// Mock 모드용 더미 JWT (admin)
// ─────────────────────────────────────────────────────────────────────

function buildMockToken(): string {
  const header = { alg: "none", typ: "JWT" };
  const payload: JwtPayload = {
    sub: "mock-admin",
    email: "mock-admin@example.com",
    name: "Mock Admin",
    role: "admin",
    groups: ["mock"],
  };
  const enc = (obj: unknown): string => {
    const str = JSON.stringify(obj);
    if (typeof btoa === "function") {
      return btoa(unescape(encodeURIComponent(str)))
        .replace(/=+$/, "")
        .replace(/\+/g, "-")
        .replace(/\//g, "_");
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const buf = (globalThis as any).Buffer?.from?.(str, "utf-8");
    return buf
      ? buf
          .toString("base64")
          .replace(/=+$/, "")
          .replace(/\+/g, "-")
          .replace(/\//g, "_")
      : str;
  };
  return `${enc(header)}.${enc(payload)}.mock-signature`;
}

// ─────────────────────────────────────────────────────────────────────
// Provider
// ─────────────────────────────────────────────────────────────────────

export interface AuthProviderProps {
  children: ReactNode;
  /** 테스트/SSR에서 토큰 사전 주입 */
  initialToken?: string | null;
  /** logout 시 호출되는 redirect 핸들러 (기본: window.location 이동) */
  onLogoutRedirect?: () => void;
}

export function AuthProvider({
  children,
  initialToken = null,
  onLogoutRedirect,
}: AuthProviderProps) {
  const [token, setToken] = useState<string | null>(() => {
    if (initialToken) return initialToken;
    if (config.useMock) return buildMockToken();
    return null;
  });

  const user = useMemo<User | null>(() => {
    if (!token) return null;
    const payload = decodeJwt(token);
    return payload ? payloadToUser(payload) : null;
  }, [token]);

  const login = useCallback((nextToken: string) => {
    setToken(nextToken);
  }, []);

  const logout = useCallback(
    (options?: { redirect?: boolean }) => {
      setToken(null);
      if (options?.redirect === false) return;
      if (onLogoutRedirect) {
        onLogoutRedirect();
        return;
      }
      if (typeof window !== "undefined") {
        // 로그인 페이지가 별도로 없으면 홈으로 이동
        window.location.href = "/";
      }
    },
    [onLogoutRedirect],
  );

  const hasRole = useCallback(
    (role: RBACRole) => {
      if (!user) return false;
      return ROLE_RANK[user.role] >= ROLE_RANK[role];
    },
    [user],
  );

  // api.ts에 토큰 / 401 핸들러 등록
  useEffect(() => {
    registerAuthTokenProvider(() => token);
  }, [token]);

  useEffect(() => {
    registerUnauthorizedHandler(() => {
      // 401 수신 시 로그아웃 + redirect
      logout({ redirect: true });
    });
    return () => {
      registerUnauthorizedHandler(() => {});
    };
  }, [logout]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      isAuthenticated: token !== null && user !== null,
      login,
      logout,
      hasRole,
    }),
    [user, token, login, logout, hasRole],
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}

// ─────────────────────────────────────────────────────────────────────
// RequireRole — 권한 부족 시 fallback 렌더
// ─────────────────────────────────────────────────────────────────────

export interface RequireRoleProps {
  role: RBACRole;
  children: ReactNode;
  fallback?: ReactNode;
}

export function RequireRole({
  role,
  children,
  fallback = null,
}: RequireRoleProps) {
  const { hasRole } = useAuth();
  if (!hasRole(role)) return <>{fallback}</>;
  return <>{children}</>;
}
