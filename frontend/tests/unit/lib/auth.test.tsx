/**
 * auth.tsx 단위 테스트.
 *
 * 검증 항목:
 *  - decodeJwt: payload base64url decode 정확성
 *  - AuthProvider login/logout 동작
 *  - hasRole 위계 비교 (admin > user > viewer)
 *  - RequireRole 컴포넌트 분기 렌더
 *  - api.ts 토큰 provider / 401 핸들러 등록 효과
 *
 * 참조: BUILD_ORDER.md 작업 7-0
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, renderHook, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "../../mocks/server";
import {
  AuthProvider,
  RequireRole,
  decodeJwt,
  useAuth,
} from "@/lib/auth";
import { apiRequest, registerUnauthorizedHandler } from "@/lib/api";
import { config } from "@/lib/config";
import type { ReactNode } from "react";

const API = `${config.apiBaseUrl}/api/v1`;

// btoa 기반 더미 JWT 생성
function makeJwt(payload: Record<string, unknown>): string {
  const enc = (obj: unknown) =>
    btoa(JSON.stringify(obj))
      .replace(/=+$/, "")
      .replace(/\+/g, "-")
      .replace(/\//g, "_");
  return `${enc({ alg: "none" })}.${enc(payload)}.sig`;
}

beforeEach(() => {
  registerUnauthorizedHandler(() => {});
});

afterEach(() => {
  registerUnauthorizedHandler(() => {});
});

describe("decodeJwt", () => {
  it("payload 필드를 모두 추출한다", () => {
    const token = makeJwt({
      sub: "user-1",
      email: "u@example.com",
      name: "User",
      role: "user",
      groups: ["g1"],
      exp: 9999999999,
    });
    const p = decodeJwt(token);
    expect(p?.sub).toBe("user-1");
    expect(p?.email).toBe("u@example.com");
    expect(p?.name).toBe("User");
    expect(p?.role).toBe("user");
    expect(p?.groups).toEqual(["g1"]);
    expect(p?.exp).toBe(9999999999);
  });

  it("JWT 형식이 아닌 문자열은 null을 반환한다", () => {
    expect(decodeJwt("only-one-part")).toBeNull();
    expect(decodeJwt("two.parts")).toBeNull();
    // payload 부분이 invalid base64 / non-JSON이면 null
    expect(decodeJwt("aaa.bbb-not-base64-json.ccc")).toBeNull();
  });

  it("payload가 JSON이 아니면 null을 반환한다", () => {
    const token = `aaa.${btoa("not-json")}.sig`;
    expect(decodeJwt(token)).toBeNull();
  });
});

function wrap(initialToken?: string) {
  return ({ children }: { children: ReactNode }) => (
    <AuthProvider
      initialToken={initialToken ?? null}
      onLogoutRedirect={() => {}}
    >
      {children}
    </AuthProvider>
  );
}

describe("AuthProvider login/logout", () => {
  it("초기 토큰이 없으면 isAuthenticated=false, user=null", () => {
    // mock 모드 영향을 피하기 위해 명시적 null 토큰 주입 시 useMock 자동 토큰을 덮지 않음
    // (config.useMock=true일 때만 자동 admin 토큰 — 테스트 환경 기본값 false)
    const { result } = renderHook(() => useAuth(), { wrapper: wrap() });
    if (config.useMock) {
      expect(result.current.isAuthenticated).toBe(true);
    } else {
      expect(result.current.token).toBeNull();
      expect(result.current.user).toBeNull();
      expect(result.current.isAuthenticated).toBe(false);
    }
  });

  it("login(token) 호출 시 user/role이 갱신된다", () => {
    const { result } = renderHook(() => useAuth(), { wrapper: wrap() });
    const token = makeJwt({
      sub: "u1",
      email: "u@x.com",
      role: "user",
      name: "Tester",
    });
    act(() => {
      result.current.login(token);
    });
    expect(result.current.token).toBe(token);
    expect(result.current.user?.id).toBe("u1");
    expect(result.current.user?.role).toBe("user");
    expect(result.current.user?.email).toBe("u@x.com");
    expect(result.current.isAuthenticated).toBe(true);
  });

  it("logout({redirect:false}) 호출 시 토큰만 제거하고 redirect는 발생하지 않는다", () => {
    const onLogoutRedirect = vi.fn();
    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => (
        <AuthProvider
          initialToken={makeJwt({ sub: "u", role: "admin" })}
          onLogoutRedirect={onLogoutRedirect}
        >
          {children}
        </AuthProvider>
      ),
    });
    expect(result.current.isAuthenticated).toBe(true);
    act(() => {
      result.current.logout({ redirect: false });
    });
    expect(result.current.token).toBeNull();
    expect(result.current.user).toBeNull();
    expect(onLogoutRedirect).not.toHaveBeenCalled();
  });

  it("logout() 기본 호출은 onLogoutRedirect를 호출한다", () => {
    const onLogoutRedirect = vi.fn();
    const { result } = renderHook(() => useAuth(), {
      wrapper: ({ children }) => (
        <AuthProvider
          initialToken={makeJwt({ sub: "u", role: "admin" })}
          onLogoutRedirect={onLogoutRedirect}
        >
          {children}
        </AuthProvider>
      ),
    });
    act(() => {
      result.current.logout();
    });
    expect(onLogoutRedirect).toHaveBeenCalledOnce();
  });
});

describe("hasRole 위계", () => {
  it("admin > user > viewer 순서로 비교한다", () => {
    const cases: Array<{ role: "admin" | "user" | "viewer"; allows: string[] }> = [
      { role: "admin", allows: ["admin", "user", "viewer"] },
      { role: "user", allows: ["user", "viewer"] },
      { role: "viewer", allows: ["viewer"] },
    ];
    for (const tc of cases) {
      const { result } = renderHook(() => useAuth(), {
        wrapper: wrap(makeJwt({ sub: "x", role: tc.role })),
      });
      for (const role of ["admin", "user", "viewer"] as const) {
        expect(result.current.hasRole(role)).toBe(tc.allows.includes(role));
      }
    }
  });

  it("토큰이 없으면 모든 역할에 false를 반환한다", () => {
    const { result } = renderHook(() => useAuth(), { wrapper: wrap() });
    if (config.useMock) return; // mock 모드에서는 자동 admin
    expect(result.current.hasRole("viewer")).toBe(false);
    expect(result.current.hasRole("user")).toBe(false);
    expect(result.current.hasRole("admin")).toBe(false);
  });
});

describe("RequireRole", () => {
  it("권한 충분 시 children을 렌더한다", () => {
    render(
      <AuthProvider
        initialToken={makeJwt({ sub: "x", role: "admin" })}
        onLogoutRedirect={() => {}}
      >
        <RequireRole role="user">
          <div>secret content</div>
        </RequireRole>
      </AuthProvider>,
    );
    expect(screen.getByText("secret content")).toBeInTheDocument();
  });

  it("권한 부족 시 fallback을 렌더한다", () => {
    render(
      <AuthProvider
        initialToken={makeJwt({ sub: "x", role: "viewer" })}
        onLogoutRedirect={() => {}}
      >
        <RequireRole role="admin" fallback={<div>403</div>}>
          <div>secret content</div>
        </RequireRole>
      </AuthProvider>,
    );
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
    expect(screen.getByText("403")).toBeInTheDocument();
  });

  it("권한 부족 + fallback 미지정 시 아무것도 렌더하지 않는다", () => {
    const { container } = render(
      <AuthProvider
        initialToken={makeJwt({ sub: "x", role: "viewer" })}
        onLogoutRedirect={() => {}}
      >
        <RequireRole role="admin">
          <div>secret content</div>
        </RequireRole>
      </AuthProvider>,
    );
    expect(container.textContent).toBe("");
  });
});

describe("AuthProvider + apiRequest 통합", () => {
  it("AuthProvider가 apiRequest에 토큰 provider를 등록하여 헤더에 첨부된다", async () => {
    const token = makeJwt({ sub: "u", role: "user" });
    let auth = "";
    server.use(
      http.get(`${API}/me`, ({ request }) => {
        auth = request.headers.get("authorization") ?? "";
        return HttpResponse.json({ ok: true });
      }),
    );
    render(
      <AuthProvider initialToken={token} onLogoutRedirect={() => {}}>
        <div>x</div>
      </AuthProvider>,
    );
    // useEffect 후에 등록되므로 한 tick 대기
    await Promise.resolve();
    await apiRequest("/me");
    expect(auth).toBe(`Bearer ${token}`);
  });

  it("401 응답 시 등록된 unauthorized 핸들러가 호출되어 logout을 트리거한다", async () => {
    const onLogoutRedirect = vi.fn();
    let unauthorizedHandler: (() => void) | null = null;
    // AuthProvider는 effect 안에서 핸들러를 등록한다
    server.use(
      http.get(`${API}/secret`, () =>
        HttpResponse.json({ title: "no", status: 401 }, { status: 401 }),
      ),
    );
    render(
      <AuthProvider
        initialToken={makeJwt({ sub: "x", role: "user" })}
        onLogoutRedirect={onLogoutRedirect}
      >
        <div>x</div>
      </AuthProvider>,
    );
    void unauthorizedHandler;
    await Promise.resolve();
    await expect(apiRequest("/secret")).rejects.toBeTruthy();
    // logout → onLogoutRedirect
    expect(onLogoutRedirect).toHaveBeenCalled();
  });
});
