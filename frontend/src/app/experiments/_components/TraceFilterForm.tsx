"use client";

import { useMemo, useState, type KeyboardEvent } from "react";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { useTraceSearch } from "@/lib/hooks/useTraces";
import type { TraceFilter, TraceSampleStrategy } from "@/lib/types/api";
import { cn } from "@/lib/utils";

interface TraceFilterFormProps {
  value: TraceFilter | null;
  onChange: (filter: TraceFilter) => void;
  projectId: string;
}

const TIME_PRESETS: Array<{ id: string; label: string; hours: number | null }> = [
  { id: "1h", label: "최근 1시간", hours: 1 },
  { id: "24h", label: "최근 24시간", hours: 24 },
  { id: "7d", label: "최근 7일", hours: 24 * 7 },
  { id: "30d", label: "최근 30일", hours: 24 * 30 },
  { id: "all", label: "전체 기간", hours: null },
];

const STRATEGY_OPTIONS: Array<{ id: TraceSampleStrategy; label: string }> = [
  { id: "random", label: "무작위" },
  { id: "first", label: "최신순" },
  { id: "stratified", label: "층화 (Stratified)" },
];

function ensureFilter(value: TraceFilter | null, projectId: string): TraceFilter {
  return (
    value ?? {
      project_id: projectId,
      sample_size: 200,
      sample_strategy: "random",
    }
  );
}

function presetIdFromFilter(filter: TraceFilter): string {
  if (!filter.from_timestamp) return "all";
  const fromMs = new Date(filter.from_timestamp).getTime();
  const diffHours = (Date.now() - fromMs) / 3_600_000;
  const matched = TIME_PRESETS.find(
    (p) => p.hours !== null && Math.abs(p.hours - diffHours) < 1,
  );
  return matched?.id ?? "custom";
}

function applyTimePreset(filter: TraceFilter, presetId: string): TraceFilter {
  const preset = TIME_PRESETS.find((p) => p.id === presetId);
  if (!preset) return filter;
  if (preset.hours === null) {
    const next = { ...filter };
    delete next.from_timestamp;
    delete next.to_timestamp;
    return next;
  }
  const from = new Date(Date.now() - preset.hours * 3_600_000);
  return {
    ...filter,
    from_timestamp: from.toISOString(),
    to_timestamp: new Date().toISOString(),
  };
}

export function TraceFilterForm({
  value,
  onChange,
  projectId,
}: TraceFilterFormProps) {
  const filter = useMemo(() => ensureFilter(value, projectId), [value, projectId]);
  const [tagDraft, setTagDraft] = useState("");

  const presetId = presetIdFromFilter(filter);
  const tags = filter.tags ?? [];

  // 미리보기: 매칭 trace 수 — page=1, page_size=1로 total만 사용
  const previewQuery = useTraceSearch(filter, 1, 1);

  const update = (patch: Partial<TraceFilter>) => {
    onChange({ ...filter, ...patch });
  };

  const handleAddTag = (raw: string) => {
    const tag = raw.trim();
    if (!tag) return;
    if (tags.includes(tag)) return;
    update({ tags: [...tags, tag] });
    setTagDraft("");
  };

  const handleRemoveTag = (tag: string) => {
    update({ tags: tags.filter((t) => t !== tag) });
  };

  const handleTagKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      handleAddTag(tagDraft);
    } else if (e.key === "Backspace" && tagDraft === "" && tags.length > 0) {
      handleRemoveTag(tags[tags.length - 1]);
    }
  };

  return (
    <div className="space-y-5 rounded-md border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="space-y-1.5">
        <label
          htmlFor="trace-filter-name"
          className="block text-sm font-medium text-zinc-200"
        >
          Agent 이름 (trace.name)
        </label>
        <Input
          id="trace-filter-name"
          placeholder="예: qa-agent-v3"
          value={filter.name ?? ""}
          onChange={(e) =>
            update({ name: e.target.value.trim() || undefined })
          }
        />
        <p className="text-[11px] text-zinc-500">
          Langfuse trace의 ``name`` 필드와 정확히 일치하는 trace만 가져옵니다.
        </p>
      </div>

      <div className="space-y-1.5">
        <span className="block text-sm font-medium text-zinc-200">Tags</span>
        <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-zinc-800 bg-zinc-950/40 px-2 py-1.5">
          {tags.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 rounded-md bg-indigo-500/15 px-2 py-0.5 text-[11px] text-indigo-200"
            >
              {tag}
              <button
                type="button"
                onClick={() => handleRemoveTag(tag)}
                aria-label={`태그 ${tag} 제거`}
                className="text-indigo-300 hover:text-rose-300"
              >
                ×
              </button>
            </span>
          ))}
          <input
            value={tagDraft}
            onChange={(e) => setTagDraft(e.target.value)}
            onKeyDown={handleTagKey}
            onBlur={() => handleAddTag(tagDraft)}
            placeholder={tags.length === 0 ? "production, staging…" : ""}
            className="min-w-[120px] flex-1 bg-transparent text-sm text-zinc-100 outline-none placeholder:text-zinc-600"
          />
        </div>
        <p className="text-[11px] text-zinc-500">
          Enter/콤마로 추가. 모든 태그를 포함하는 trace만 매칭됩니다.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <label
            htmlFor="trace-filter-period"
            className="block text-sm font-medium text-zinc-200"
          >
            기간
          </label>
          <Select
            id="trace-filter-period"
            value={presetId === "custom" ? "all" : presetId}
            onChange={(e) => onChange(applyTimePreset(filter, e.target.value))}
          >
            {TIME_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor="trace-filter-sample-size"
            className="block text-sm font-medium text-zinc-200"
          >
            샘플 수
          </label>
          <Input
            id="trace-filter-sample-size"
            type="number"
            min={1}
            max={5000}
            value={filter.sample_size ?? ""}
            onChange={(e) => {
              const v = e.target.value;
              update({
                sample_size: v === "" ? undefined : Number(v),
              });
            }}
            placeholder="예: 200"
          />
          <p className="text-[11px] text-zinc-500">
            매칭된 trace 중 평가에 사용할 샘플 수. 비우면 전체 평가.
          </p>
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor="trace-filter-strategy"
            className="block text-sm font-medium text-zinc-200"
          >
            샘플링 전략
          </label>
          <Select
            id="trace-filter-strategy"
            value={filter.sample_strategy ?? "random"}
            onChange={(e) =>
              update({
                sample_strategy: e.target.value as TraceSampleStrategy,
              })
            }
          >
            {STRATEGY_OPTIONS.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </Select>
        </div>

        <div className="space-y-1.5">
          <span className="block text-sm font-medium text-zinc-200">
            프로젝트
          </span>
          <Input
            value={filter.project_id}
            onChange={(e) => update({ project_id: e.target.value })}
          />
        </div>
      </div>

      <div
        className={cn(
          "flex items-center justify-between rounded-md border bg-zinc-950/40 px-3 py-2.5 text-xs",
          previewQuery.isError ? "border-rose-700/40" : "border-zinc-800",
        )}
      >
        <span className="text-zinc-400">미리보기</span>
        {previewQuery.isLoading && (
          <span className="text-zinc-500">조회 중…</span>
        )}
        {previewQuery.isError && (
          <span className="text-rose-300">불러오기 실패</span>
        )}
        {previewQuery.isSuccess && (
          <span className="text-zinc-200">
            매칭 {" "}
            <span className="font-mono font-semibold text-emerald-300">
              {previewQuery.data.total.toLocaleString()}
            </span>
            건 ·{" "}
            {filter.sample_size != null ? (
              <>
                평가 대상{" "}
                <Badge tone="accent">
                  {Math.min(filter.sample_size, previewQuery.data.total)}건
                </Badge>
              </>
            ) : (
              <Badge tone="accent">전체 평가</Badge>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
