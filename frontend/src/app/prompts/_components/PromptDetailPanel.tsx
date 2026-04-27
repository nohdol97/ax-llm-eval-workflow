"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronDown, FlaskConical } from "lucide-react";
import { SlideOver } from "@/components/ui/SlideOver";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { RequireRole } from "@/lib/auth";
import {
  usePromoteLabel,
  usePromptDetail,
  usePromptVersions,
} from "@/lib/hooks/usePrompts";
import type { ChatMessage, PromptDetail } from "@/lib/types/api";
import { cn, formatRelativeDate } from "@/lib/utils";

type PromptLabel = "production" | "staging" | "draft";

const LABEL_TONE: Record<PromptLabel, "success" | "warning" | "neutral"> = {
  production: "success",
  staging: "warning",
  draft: "neutral",
};

interface Props {
  promptName: string | null;
  projectId: string;
  open: boolean;
  onClose: () => void;
}

function renderPromptBody(prompt: PromptDetail): string {
  if (typeof prompt.prompt === "string") return prompt.prompt;
  return (prompt.prompt as ChatMessage[])
    .map((m) => `[${m.role}]\n${m.content}`)
    .join("\n\n");
}

function getSystemPrompt(prompt: PromptDetail): string | undefined {
  const cfg = prompt.config as { system_prompt?: string } | undefined;
  return cfg?.system_prompt;
}

export function PromptDetailPanel({
  promptName,
  projectId,
  open,
  onClose,
}: Props) {
  const [activeVersion, setActiveVersion] = useState<number | null>(null);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);

  const versionsQuery = usePromptVersions(projectId, promptName);
  const detailQuery = usePromptDetail(
    projectId,
    promptName,
    activeVersion ?? undefined
  );
  const promoteMutation = usePromoteLabel();

  const versions = versionsQuery.data?.versions ?? [];
  const prompt: PromptDetail | undefined = detailQuery.data;

  // 최신 버전을 기본으로 활성화
  useEffect(() => {
    if (!promptName) {
      setActiveVersion(null);
      setPromoteOpen(false);
      setPromoteError(null);
      return;
    }
    if (versions.length > 0 && activeVersion === null) {
      const max = Math.max(...versions.map((v) => v.version));
      setActiveVersion(max);
    }
  }, [promptName, versions, activeVersion]);

  const handlePromote = async (label: PromptLabel) => {
    if (!prompt || activeVersion === null) return;
    setPromoteError(null);
    try {
      await promoteMutation.mutateAsync({
        name: prompt.name,
        version: activeVersion,
        payload: {
          project_id: projectId,
          labels: Array.from(new Set([...(prompt.labels ?? []), label])),
        },
      });
      setPromoteOpen(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setPromoteError(msg);
    }
  };

  return (
    <SlideOver
      open={open && !!promptName}
      onClose={onClose}
      width={520}
      title={prompt?.name ?? promptName ?? undefined}
    >
      {detailQuery.isLoading || !prompt ? (
        <div className="space-y-3">
          <div className="h-5 w-32 animate-pulse rounded bg-zinc-800" />
          <div className="h-32 animate-pulse rounded bg-zinc-900" />
          <div className="h-32 animate-pulse rounded bg-zinc-900" />
        </div>
      ) : (
        <div className="space-y-5">
          {/* Version selector */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              버전
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {versions.length === 0 ? (
                <span className="text-xs text-zinc-500">—</span>
              ) : (
                versions.map((v) => {
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
                })
              )}
            </div>
            <p className="mt-1.5 text-xs text-zinc-500">
              {prompt.created_at ? formatRelativeDate(prompt.created_at) : "—"}
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
                  <Badge
                    key={l}
                    tone={LABEL_TONE[l as PromptLabel] ?? "neutral"}
                  >
                    {l}
                  </Badge>
                ))
              )}
            </div>
            <RequireRole role="admin">
              <div className="relative">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPromoteOpen((o) => !o)}
                  aria-haspopup="menu"
                  aria-expanded={promoteOpen}
                  disabled={promoteMutation.isPending}
                >
                  {promoteMutation.isPending ? "승격 중..." : "승격"}
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
                        onClick={() => handlePromote(label)}
                        className="flex w-full items-center justify-between px-3 py-2 text-left text-xs text-zinc-200 hover:bg-zinc-800"
                      >
                        <span>{label}</span>
                        <Badge tone={LABEL_TONE[label]}>{label}</Badge>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </RequireRole>
          </section>

          {promoteError && (
            <div className="rounded-md border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-xs text-rose-200">
              {promoteError}
            </div>
          )}

          {/* Variables */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              변수
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {prompt.variables.length === 0 ? (
                <span className="text-xs text-zinc-500">—</span>
              ) : (
                prompt.variables.map((v) => (
                  <Badge key={v} tone="accent">
                    {`{{${v}}}`}
                  </Badge>
                ))
              )}
            </div>
          </section>

          {/* System prompt */}
          {getSystemPrompt(prompt) && (
            <section>
              <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
                System Prompt
              </h3>
              <pre className="whitespace-pre-wrap break-words rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-200">
                {getSystemPrompt(prompt)}
              </pre>
            </section>
          )}

          {/* Body */}
          <section>
            <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-500">
              본문
            </h3>
            <pre className="whitespace-pre-wrap break-words rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-200">
              {renderPromptBody(prompt)}
            </pre>
          </section>

          {/* Footer action */}
          <div className="border-t border-zinc-800 pt-4">
            <Link
              href={`/playground?prompt=${encodeURIComponent(prompt.name)}&version=${prompt.version}`}
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
