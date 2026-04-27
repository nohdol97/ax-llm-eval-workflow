/**
 * src/components/ui/Button.tsx 컴포넌트 렌더링 smoke 테스트.
 *
 * Codex가 추후 variant/size/disabled 등 전체 case를 추가한다.
 *
 * 참조: BUILD_ORDER.md 작업 0-3
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/Button";

describe("Button", () => {
  it("children을 렌더링한다", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("variant=primary 시 indigo 배경 클래스가 적용된다", () => {
    render(<Button variant="primary">Run</Button>);
    const btn = screen.getByRole("button", { name: "Run" });
    // cva가 생성하는 클래스에 'bg-indigo-500'이 포함되어야 함.
    expect(btn.className).toMatch(/bg-indigo-500/);
  });
});
