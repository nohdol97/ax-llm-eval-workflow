"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Database,
  Download,
  FlaskConical,
  Trash2,
} from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { RequireRole, useAuth } from "@/lib/auth";
import {
  useDatasetItems,
  useDatasetList,
  useDeleteDataset,
} from "@/lib/hooks/useDatasets";
import type { DatasetItem, DatasetSummary } from "@/lib/types/api";
import { formatNumber, formatRelativeDate } from "@/lib/utils";

const PAGE_SIZE = 25;
const DEFAULT_PROJECT_ID = "production-api";

export default function DatasetDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: rawId } = use(params);
  const name = decodeURIComponent(rawId);
  const router = useRouter();
  const { user } = useAuth();
  const projectId =
    (user as { currentProjectId?: string } | null)?.currentProjectId ??
    DEFAULT_PROJECT_ID;

  const [page, setPage] = useState(1);
  const listQuery = useDatasetList(projectId);
  const itemsQuery = useDatasetItems(projectId, name, page, PAGE_SIZE);
  const deleteMutation = useDeleteDataset();

  const dataset: DatasetSummary | null = useMemo(() => {
    const raw = listQuery.data;
    if (!raw || typeof raw !== "object") return null;
    const r = raw as Record<string, unknown>;
    const list: DatasetSummary[] = Array.isArray(r.datasets)
      ? (r.datasets as DatasetSummary[])
      : Array.isArray(r.items)
        ? (r.items as DatasetSummary[])
        : [];
    return list.find((d) => d.name === name) ?? null;
  }, [listQuery.data, name]);

  const items: DatasetItem[] = (itemsQuery.data?.items ?? []) as DatasetItem[];
  const totalItems =
    itemsQuery.data?.total ?? dataset?.item_count ?? items.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / PAGE_SIZE));

  const handleDelete = async () => {
    if (!dataset) return;
    if (!confirm(`'${dataset.name}'을(를) 삭제하시겠습니까? 되돌릴 수 없습니다.`)) {
      return;
    }
    try {
      await deleteMutation.mutateAsync({ name: dataset.name });
      router.push("/datasets");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      alert(`삭제 실패: ${msg}`);
    }
  };

  if (listQuery.isLoading) {
    return (
      <div className="px-8 py-6">
        <div className="h-6 w-48 animate-pulse rounded bg-zinc-800" />
        <div className="mt-4 h-32 animate-pulse rounded bg-zinc-900" />
      </div>
    );
  }

  if (listQuery.isError || !dataset) {
    return (
      <div className="px-8 py-6">
        <Link
          href="/datasets"
          className="mb-3 inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          데이터셋 목록
        </Link>
        <EmptyState
          icon={<Database className="h-8 w-8" />}
          title="데이터셋을 불러오지 못했습니다"
          description="존재하지 않거나 권한이 없을 수 있습니다."
        />
      </div>
    );
  }

  return (
    <div className="px-8 py-6">
      <Link
        href="/datasets"
        className="mb-3 inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-zinc-200"
      >
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        데이터셋 목록
      </Link>

      <PageHeader
        title={dataset.name}
        description={dataset.description}
        actions={
          <>
            <Link
              href={`/playground?dataset=${encodeURIComponent(dataset.name)}`}
              className="inline-flex h-8 items-center gap-2 rounded-md border border-zinc-700 bg-transparent px-3 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
            >
              <FlaskConical className="h-4 w-4" aria-hidden />
              실험에서 사용
            </Link>
            <Button variant="ghost">
              <Download className="h-4 w-4" aria-hidden />
              다운로드
            </Button>
            <RequireRole role="admin">
              <Button
                variant="ghost"
                className="text-rose-300 hover:text-rose-200"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
              >
                <Trash2 className="h-4 w-4" aria-hidden />
                {deleteMutation.isPending ? "삭제 중..." : "삭제"}
              </Button>
            </RequireRole>
          </>
        }
      />

      {/* Meta cards */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetaCard
          label="아이템 수"
          value={formatNumber(dataset.item_count ?? 0)}
          icon={<Database className="h-4 w-4" />}
        />
        <MetaCard
          label="생성일"
          value={dataset.created_at ? formatRelativeDate(dataset.created_at) : "—"}
        />
        <MetaCard
          label="최근 사용"
          value={
            dataset.last_used_at ? formatRelativeDate(dataset.last_used_at) : "—"
          }
        />
        <MetaCard
          label="페이지"
          value={`${page} / ${totalPages}`}
        />
      </div>

      {/* Items table */}
      <Card>
        {itemsQuery.isLoading ? (
          <CardContent>
            <div className="space-y-2">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-10 animate-pulse rounded bg-zinc-900/50" />
              ))}
            </div>
          </CardContent>
        ) : items.length === 0 ? (
          <CardContent>
            <EmptyState
              icon={<Database className="h-8 w-8" />}
              title="아이템이 없습니다"
              description="이 데이터셋에 아직 데이터가 등록되지 않았습니다."
            />
          </CardContent>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-zinc-800 bg-zinc-950/40 text-xs text-zinc-400">
                  <tr>
                    <th scope="col" className="w-10 px-3 py-2 text-left font-medium">
                      #
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      Input
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      Expected Output
                    </th>
                    <th scope="col" className="px-3 py-2 text-left font-medium">
                      Metadata
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it, i) => (
                    <tr
                      key={it.id}
                      className="border-t border-zinc-800 align-top hover:bg-zinc-900/40"
                    >
                      <td className="px-3 py-2 font-mono text-xs text-zinc-500">
                        {(page - 1) * PAGE_SIZE + i + 1}
                      </td>
                      <td className="max-w-md px-3 py-2">
                        <pre className="line-clamp-3 whitespace-pre-wrap break-all rounded bg-zinc-950/60 p-2 font-mono text-xs text-zinc-200">
                          {JSON.stringify(it.input, null, 0)}
                        </pre>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-zinc-100">
                        {typeof it.expected_output === "string"
                          ? it.expected_output
                          : JSON.stringify(it.expected_output ?? "")}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {Object.entries(it.metadata ?? {}).map(([k, v]) => (
                            <Badge key={k} tone="muted">
                              {k}: {String(v)}
                            </Badge>
                          ))}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {totalPages > 1 && (
              <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-2.5 text-xs text-zinc-400">
                <span>
                  {(page - 1) * PAGE_SIZE + 1}–
                  {Math.min(page * PAGE_SIZE, totalItems)} / {formatNumber(totalItems)}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page === 1}
                    aria-label="이전 페이지"
                    className="grid h-7 w-7 place-items-center rounded border border-zinc-800 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                  </button>
                  <span className="px-2 font-mono">
                    {page} / {totalPages}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    aria-label="다음 페이지"
                    className="grid h-7 w-7 place-items-center rounded border border-zinc-800 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <ChevronRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

function MetaCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs text-zinc-400">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-base font-semibold text-zinc-100">{value}</div>
    </div>
  );
}
