/**
 * src/lib/utils.ts 단위 테스트 (smoke).
 *
 * Codex가 추후 본 파일을 확장하여 엣지케이스를 추가한다.
 * (CLAUDE.md TDD 정책: 테스트 작성은 Codex에 위임 — 본 파일은 셋업
 * 검증을 위한 최소 smoke 테스트.)
 *
 * 참조: BUILD_ORDER.md 작업 0-3
 */
import { describe, it, expect } from "vitest";
import {
  formatCurrency,
  formatDuration,
  scoreColor,
} from "@/lib/utils";

describe("formatCurrency", () => {
  it("기본 fractionDigits=4로 달러 표기를 반환한다", () => {
    expect(formatCurrency(1.2345)).toBe("$1.2345");
  });

  it("fractionDigits 인자를 반영한다", () => {
    expect(formatCurrency(1.2345, 2)).toBe("$1.23");
  });
});

describe("formatDuration", () => {
  it("1초 미만은 ms 단위로 반환한다", () => {
    expect(formatDuration(123)).toBe("123ms");
  });

  it("1초 이상 1분 미만은 초 단위로 반환한다", () => {
    // 1500ms = 1.50s
    expect(formatDuration(1500)).toBe("1.50s");
  });

  it("1분 이상은 '분 초' 형식으로 반환한다", () => {
    // 90000ms = 1분 30초
    expect(formatDuration(90_000)).toBe("1분 30초");
  });
});

describe("scoreColor", () => {
  it("null 입력 시 zinc 계열 색상을 반환한다", () => {
    const c = scoreColor(null);
    expect(c.fg).toContain("zinc");
  });

  it("0.95 (best) 입력 시 emerald 계열 색상을 반환한다", () => {
    const c = scoreColor(0.95);
    expect(c.fg).toContain("emerald");
  });
});
