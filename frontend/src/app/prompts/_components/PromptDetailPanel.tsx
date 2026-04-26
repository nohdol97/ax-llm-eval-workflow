"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronDown, FlaskConical } from "lucide-react";
import { SlideOver } from "@/components/ui/SlideOver";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import type { Prompt, PromptVersion } from "@/lib/mock/types";
import { cn, formatRelativeDate } from "@/lib/utils";

const LABEL_TONE: Record<
  "production" | "staging" | "draft",
  "success" | "warning" | "neutral"
> = {
  production: "success",
  staging: "warning",
  draft: "neutral",
};

interface Props {
  prompt: Prompt | null;
  open: boolean;
  onClose: () => void;
}

export function PromptDetailPanel({ prompt, open, onClose }: Props) {
  const [activeVersion, setActiveVersion] = useState<number | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);

  useEffect(() => {
    if (prompt) {
      setActiveVersion(prompt.latestVersion);
      setPromoteOpen(false);
    }
  }, [prompt]);

  const version: PromptVersion | undefined = prompt?.versions.find(
    (v) => v.version === activeVersion
  );

  return (
    <SlideOver
      open={open && !!prompt}
      onClose={onClose}
      width={520}
      title={prompt?.name}
      description={prompt?.description}
    >
      {prompt && version && (
        <div className="space-y-5">
          {/* Version selector */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              버전
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {prompt.versions.map((v) => {
                const active = v.version === activeVersion;
                return (
                  <button
                    key={v.version}
                    type="button"
                    onClick={() => setActiveVersion(v.version)}
                    className={cn(
                      "rounded-full border px-2.5 py-1 font-mono text-xs transition-colors",
                      active
                        ? "border-indigo-400 bg-indigo-500/15 text-indigo-200"
                        : "border-zinc-700 bg-zinc-900 text-zinc-300 hover:bg-zinc-800"
                    )}
                  >
                    v{v.version}
                  </button>
                );
              })}
            </div>
            <p className="mt-1.5 text-xs text-zinc-500">
              {version.author} · {formatRelativeDate(version.createdAt)}
            </p>
          </section>

          {/* Labels + Promote */}
          <section className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                라벨
              </span>
              {prompt.labels.length === 0 ? (
                <span className="text-xs text-zinc-500">—</span>
              ) : (
                prompt.labels.map((l) => (
                  <Badge key={l} tone={LABEL_TONE[l]}>
                    {l}
                  </Badge>
                ))
              )}
            </div>
            <div className="relative">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPromoteOpen((o) => !o)}
                aria-haspopup="menu"
                aria-expanded={promoteOpen}
              >
                승격
                <ChevronDown className="h-3.5 w-3.5" aria-hidden />
              </Button>
              {promoteOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-full z-10 mt-1 w-40 overflow-hidden rounded-md border border-zinc-800 bg-zinc-900 shadow-md"
                >
                  {(["production", "staging"] as const).map((label) => (
                    <button
                      key={label}
                      type="button"
                      role="menuitem"
                      onClick={() => setPromoteOpen(false)}
                      className="flex w-full items-center justify-between px-3 py-2 text-left text-xs text-zinc-200 hover:bg-zinc-800"
                    >
                      <span>{label}</span>
                      <Badge tone={LABEL_TONE[label]}>{label}</Badge>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </section>

          {/* Variables */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              변수
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {version.variables.length === 0 ? (
                <span className="text-xs text-zinc-500">—</span>
              ) : (
                version.variables.map((v) => (
                  <Badge key={v} tone="accent">
                    {`{{${v}}}`}
                  </Badge>
                ))
              )}
            </div>
          </section>

          {/* System prompt */}
          {version.systemPrompt && (
            <section>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                System Prompt
              </h3>
              <pre className="whitespace-pre-wrap break-words rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-200">
                {version.systemPrompt}
              </pre>
            </section>
          )}

          {/* Body */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              본문
            </h3>
            <pre className="whitespace-pre-wrap break-words rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-200">
              {version.body}
            </pre>
          </section>

          {/* Footer action */}
          <div className="border-t border-zinc-800 pt-4">
            <Link
              href="/playground"
              className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white hover:bg-indigo-400"
            >
              <FlaskConical className="h-4 w-4" aria-hidden />
              단일 테스트로 열기
            </Link>
          </div>
        </div>
      )}
    </SlideOver>
  );
}
