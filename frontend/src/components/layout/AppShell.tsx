import type { ReactNode } from "react";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-zinc-950 text-zinc-100">
      <TopBar />
      <div className="flex">
        <SideNav />
        <main className="min-h-[calc(100dvh-3rem)] flex-1 overflow-x-auto">
          {children}
        </main>
      </div>
    </div>
  );
}
