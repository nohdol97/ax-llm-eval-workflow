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
import { datasets } from "@/lib/mock/data";
import { formatNumber, formatRelativeDate } from "@/lib/utils";
import { UploadDatasetModal } from "./_components/UploadDatasetModal";

type SortKey = "recent" | "created" | "name";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "recent", label: "최근 사용순" },
  { value: "created", label: "생성일순" },
  { value: "name", label: "이름순" },
];

export default function DatasetsPage() {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("recent");
  const [uploadOpen, setUploadOpen] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let list = datasets.filter(
      (d) =>
        !q ||
        d.name.toLowerCase().includes(q) ||
        d.description?.toLowerCase().includes(q)
    );
    list = [...list].sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "created")
        return (
          new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
        );
      // recent
      const ax = a.lastUsed ? new Date(a.lastUsed).getTime() : 0;
      const bx = b.lastUsed ? new Date(b.lastUsed).getTime() : 0;
      return bx - ax;
    });
    return list;
  }, [query, sort]);

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

      {filtered.length === 0 ? (
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
              key={d.id}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.18, delay: idx * 0.02 }}
              whileHover={{ scale: 1.01 }}
            >
              <Link
                href={`/datasets/${d.id}`}
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
                  <Badge tone="accent">{formatNumber(d.itemCount)} items</Badge>
                  <span className="text-xs text-zinc-500">
                    {d.lastUsed
                      ? `최근 사용: ${formatRelativeDate(d.lastUsed)} (${d.recentExperimentCount}건)`
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
      />
    </div>
  );
}
