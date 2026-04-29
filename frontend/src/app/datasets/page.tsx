"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Database, MoreVertical, Plus, Search } from "lucide-react";
import { motion } from "framer-motion";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { useAuth } from "@/lib/auth";
import { useDatasetList } from "@/lib/hooks/useDatasets";
import type { DatasetSummary } from "@/lib/types/api";
import { formatNumber, formatRelativeDate } from "@/lib/utils";
import { UploadDatasetModal } from "./_components/UploadDatasetModal";

type SortKey = "recent" | "created" | "name";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "recent", label: "최근 사용순" },
  { value: "created", label: "생성일순" },
  { value: "name", label: "이름순" },
];

const DEFAULT_PROJECT_ID = "production-api";

export default function DatasetsPage() {
  const { user } = useAuth();
  const projectId =
    (user as { currentProjectId?: string } | null)?.currentProjectId ??
    DEFAULT_PROJECT_ID;
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("recent");
  const [uploadOpen, setUploadOpen] = useState(false);

  const { data, isLoading, isError, refetch } = useDatasetList(projectId);

  const list: DatasetSummary[] = useMemo(() => {
    const raw = data;
    // 방어: API 가 비-JSON 응답(HTML 등)을 string 으로 반환한 경우 "in" 연산자가
    // TypeError 를 던지지 않도록 object 인지 먼저 확인한다.
    if (!raw || typeof raw !== "object") return [];
    const r = raw as Record<string, unknown>;
    if (Array.isArray(r.datasets)) return r.datasets as DatasetSummary[];
    if (Array.isArray(r.items)) return r.items as DatasetSummary[];
    return [];
  }, [data]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = list;
    if (q) {
      out = out.filter(
        (d) =>
          d.name.toLowerCase().includes(q) ||
          (d.description ?? "").toLowerCase().includes(q)
      );
    }
    out = [...out].sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "created") {
        return (
          new Date(b.created_at ?? 0).getTime() -
          new Date(a.created_at ?? 0).getTime()
        );
      }
      const ax = a.last_used_at ? new Date(a.last_used_at).getTime() : 0;
      const bx = b.last_used_at ? new Date(b.last_used_at).getTime() : 0;
      return bx - ax;
    });
    return out;
  }, [list, query, sort]);

  return (
    <div className="px-8 py-6">
      <PageHeader
        title="데이터셋"
        description="실험에 사용할 Golden Dataset 관리"
        actions={
          <Button variant="primary" onClick={() => setUploadOpen(true)}>
            <Plus className="h-4 w-4" aria-hidden />
            업로드
          </Button>
        }
      />

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[260px] max-w-md">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500"
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="이름 또는 설명으로 검색"
            className="pl-8"
            aria-label="데이터셋 검색"
          />
        </div>
        <div className="w-44">
          <Select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            aria-label="정렬"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div
              key={i}
              className="h-32 animate-pulse rounded-lg border border-zinc-800 bg-zinc-900/40"
            />
          ))}
        </div>
      ) : isError ? (
        <EmptyState
          icon={<Database className="h-8 w-8" />}
          title="데이터셋을 불러오지 못했습니다"
          description="네트워크 또는 서버 오류입니다. 다시 시도해 주세요."
          primaryAction={
            <Button variant="primary" onClick={() => refetch?.()}>
              재시도
            </Button>
          }
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<Database className="h-8 w-8" />}
          title="데이터셋이 없습니다"
          description="새 골든셋을 업로드하거나 검색어를 변경해 보세요."
          primaryAction={
            <Button variant="primary" onClick={() => setUploadOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden />
              업로드
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((d, idx) => (
            <motion.div
              key={d.name}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.18, delay: idx * 0.02 }}
              whileHover={{ scale: 1.01 }}
            >
              <Link
                href={`/datasets/${encodeURIComponent(d.name)}`}
                className="group flex h-full flex-col gap-3 rounded-lg border border-zinc-800 bg-zinc-900 p-4 transition-colors hover:border-indigo-500/30 hover:bg-zinc-900/80"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span
                      aria-hidden
                      className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-indigo-500/10 text-indigo-300"
                    >
                      <Database className="h-4 w-4" />
                    </span>
                    <h3 className="truncate text-sm font-semibold text-zinc-100">
                      {d.name}
                    </h3>
                  </div>
                  <button
                    type="button"
                    aria-label={`${d.name} 메뉴`}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                    }}
                    className="grid h-7 w-7 shrink-0 place-items-center rounded text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
                  >
                    <MoreVertical className="h-4 w-4" />
                  </button>
                </div>

                <p className="line-clamp-2 min-h-[2.25rem] text-xs text-zinc-400">
                  {d.description ?? "설명이 없습니다."}
                </p>

                <div className="mt-auto flex items-center justify-between gap-2 pt-2">
                  <Badge tone="accent">
                    {formatNumber(d.item_count ?? 0)} items
                  </Badge>
                  <span className="text-xs text-zinc-500">
                    {d.last_used_at
                      ? `최근 사용: ${formatRelativeDate(d.last_used_at)}`
                      : "사용 이력 없음"}
                  </span>
                </div>
              </Link>
            </motion.div>
          ))}
        </div>
      )}

      <UploadDatasetModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        projectId={projectId}
      />
    </div>
  );
}
