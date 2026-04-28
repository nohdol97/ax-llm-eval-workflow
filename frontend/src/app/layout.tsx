import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/layout/AppShell";
import { BasketProvider } from "@/lib/basket";
import { QueryProvider } from "@/lib/queryClient";
import { AuthProvider } from "@/lib/auth";

export const metadata: Metadata = {
  title: "GenAI Labs",
  description: "Langfuse v3 기반 LLM 프롬프트 실험/평가 워크플로우",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#09090b",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko" data-theme="dark" className="dark">
      <head>
        {/* Pretendard / Inter / JetBrains Mono CDN 로딩.
            Next 권고는 next/font 사용이지만 본 프로젝트는 모든 페이지에서
            동일 폰트를 쓰고, Pretendard variable subset이 SDK에 미수록되어
            CDN 직접 link를 의도적으로 유지한다 (UI_UX_DESIGN.md §3.2 준수). */}
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"
        />
        {/* eslint-disable-next-line @next/next/no-page-custom-font */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
        />
      </head>
      <body className="bg-zinc-950 text-zinc-100 antialiased">
        <QueryProvider>
          <AuthProvider>
            <BasketProvider>
              <AppShell>{children}</AppShell>
            </BasketProvider>
          </AuthProvider>
        </QueryProvider>
      </body>
    </html>
  );
}
