"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { FlaskConical, Plus, Search } from "lucide-react";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { useExperimentList } from "@/lib/hooks/useExperiments";
import type { ExperimentStatus, ExperimentSummary } from "@/lib/types/api";
import { cn } from "@/lib/utils";
import {
  ExperimentTable,
  type SortDir,
  type SortKey,
} from "./_components/ExperimentTable";

const DEFAULT_PROJECT_ID = "production-api";

type StatusFilter = "all" | ExperimentStatus;

const STATUS_FILTERS: Array<{ id: StatusFilter; label: string }> = [
  { id: "all", label: "전체" },
  { id: "running", label: "진행중" },
  { id: "completed", label: "완료" },
  { id: "failed", label: "실패" },
  { id: "paused", label: "일시정지" },
];

type SortOption = {
  id: string;
  label: string;
  key: SortKey;
  dir: SortDir;
};

const SORT_OPTIONS: SortOption[] = [
  { id: "created_desc", label: "최신순", key: "createdAt", dir: "desc" },
  { id: "cost_desc", label: "비용 높은순", key: "totalCostUsd", dir: "desc" },
  { id: "score_desc", label: "스코어 높은순", key: "avgScore", dir: "desc" },
];

export default function ExperimentsPage() {
  const projectId = DEFAULT_PROJECT_ID;

  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortId, setSortId] = useState<string>(SORT_OPTIONS[0].id);
  const [page, setPage] = useState(1);

  const sortOption = useMemo(
    () => SORT_OPTIONS.find((o) => o.id === sortId) ?? SORT_OPTIONS[0],
    [sortId]
  );

  // Backend supports status + page; search/sort handled client-side.
  const { data, isLoading, error, refetch } = useExperimentList(projectId, {
    status: statusFilter === "all" ? undefined : statusFilter,
    page,
    pageSize: 50,
  });

  const items = useMemo(() => data?.items ?? [], [data]);
  const total = data?.total ?? items.length;

  const filteredSorted = useMemo<ExperimentSummary[]>(() => {
    const q = query.trim().toLowerCase();
    let rows = items.slice();
    if (q) {
      rows = rows.filter((e) => e.name.toLowerCase().includes(q));
    }
    rows.sort((a, b) => {
      switch (sortOption.key) {
        case "createdAt": {
          const at = new Date(a.created_at).getTime();
          const bt = new Date(b.created_at).getTime();
          return sortOption.dir === "asc" ? at - bt : bt - at;
        }
        case "totalCostUsd": {
          const av = a.total_cost ?? 0;
          const bv = b.total_cost ?? 0;
          return sortOption.dir === "asc" ? av - bv : bv - av;
        }
        case "avgScore": {
          const av = a.avg_score ?? 0;
          const bv = b.avg_score ?? 0;
          return sortOption.dir === "asc" ? av - bv : bv - av;
        }
        default:
          return 0;
      }
    });
    return rows;
  }, [items, query, sortOption]);

  const handleSortChange = (key: SortKey) => {
    const current = SORT_OPTIONS.find((o) => o.key === key);
    if (current) {
      setSortId(current.id);
    }
  };

  return (
    <div className="px-6 py-6">
      <PageHeader
        title="배치 실험"
        description="프롬프트 버전 × 모델 × 데이터셋을 매트릭스로 실행하고 평가합니다."
        actions={
          <Link
            href="/experiments/new"
            className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-400 active:bg-indigo-600"
          >
            <Plus className="h-4 w-4" aria-hidden />새 실험
          </Link>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative min-w-[240px] flex-1">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-500"
            aria-hidden
          />
          <Input
            type="search"
            placeholder="실험명 검색"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="pl-8"
            aria-label="실험 검색"
          />
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {STATUS_FILTERS.map((f) => {
            const active = statusFilter === f.id;
            return (
              <button
                key={f.id}
                type="button"
                onClick={() => {
                  setStatusFilter(f.id);
                  setPage(1);
                }}
                aria-pressed={active}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  active
                    ? "border-indigo-500 bg-indigo-500/15 text-indigo-200"
                    : "border-zinc-800 bg-zinc-900 text-zinc-400 hover:border-zinc-700 hover:text-zinc-200"
                )}
              >
                {f.label}
              </button>
            );
          })}
        </div>

        <div className="ml-auto w-[180px]">
          <Select
            value={sortId}
            onChange={(e) => setSortId(e.target.value)}
            aria-label="정렬"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.id} value={o.id}>
                정렬: {o.label}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {error ? (
        <EmptyState
          icon={<FlaskConical className="h-8 w-8" />}
          title="실험 목록을 불러오지 못했습니다"
          description={(error as Error).message ?? "다시 시도해 주세요."}
          primaryAction={
            <button
              type="button"
              onClick={() => refetch()}
              className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white"
            >
              재시도
            </button>
          }
        />
      ) : isLoading ? (
        <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-8 text-center text-sm text-zinc-500">
          실험 목록을 불러오는 중…
        </div>
      ) : filteredSorted.length === 0 ? (
        <EmptyState
          icon={<FlaskConical className="h-8 w-8" />}
          title="조건에 맞는 실험이 없습니다"
          description="검색어나 상태 필터를 변경해보거나, 새 실험을 시작해보세요."
          primaryAction={
            <Link
              href="/experiments/new"
              className="inline-flex h-8 items-center gap-2 rounded-md bg-indigo-500 px-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-indigo-400 active:bg-indigo-600"
            >
              <Plus className="h-4 w-4" aria-hidden />새 실험 만들기
            </Link>
          }
        />
      ) : (
        <>
          <ExperimentTable
            experiments={filteredSorted}
            sortKey={sortOption.key}
            sortDir={sortOption.dir}
            onSortChange={handleSortChange}
          />
          {total > items.length && (
            <div className="mt-3 flex items-center justify-between text-xs text-zinc-500">
              <span>
                {items.length}개 표시 / 전체 {total}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="rounded-md border border-zinc-800 px-2 py-1 hover:bg-zinc-900 disabled:opacity-50"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                >
                  이전
                </button>
                <span>page {page}</span>
                <button
                  type="button"
                  className="rounded-md border border-zinc-800 px-2 py-1 hover:bg-zinc-900"
                  onClick={() => setPage((p) => p + 1)}
                >
                  다음
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
