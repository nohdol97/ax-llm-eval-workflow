"use client";

import { use, useMemo } from "react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, Database, Download, FlaskConical, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import {
  Card,
  CardContent,
} from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { datasetItems, datasets } from "@/lib/mock/data";
import type { DatasetItem } from "@/lib/mock/types";
import { formatNumber, formatRelativeDate } from "@/lib/utils";

const FALLBACK_INPUTS = [
  "이 제품 정말 최고예요. 추천합니다!",
  "배송이 너무 늦어서 화가 나네요.",
  "그냥 평범한 수준입니다.",
  "Worst experience ever.",
  "Amazing quality, will buy again.",
  "디자인은 마음에 드는데 가격이 비싸요.",
  "기대보다 훨씬 좋네요. 강추!",
];
const FALLBACK_LABELS = ["positive", "negative", "neutral"];
const FALLBACK_LANGS = ["ko", "en"];

function generateFallbackItems(seed: string, count: number): DatasetItem[] {
  // Deterministic generator based on seed to keep server/client output stable
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  const next = () => {
    hash = (hash * 9301 + 49297) >>> 0;
    return (hash % 1000) / 1000;
  };
  return Array.from({ length: count }).map((_, i) => ({
    id: `item_seed_${i + 1}`,
    input: { text: FALLBACK_INPUTS[i % FALLBACK_INPUTS.length] },
    expectedOutput:
      FALLBACK_LABELS[Math.floor(next() * FALLBACK_LABELS.length)] ??
      "neutral",
    metadata: {
      language: FALLBACK_LANGS[Math.floor(next() * FALLBACK_LANGS.length)] ?? "ko",
      domain: "review",
    },
  }));
}

export default function DatasetDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const dataset = datasets.find((d) => d.id === id);
  if (!dataset) notFound();

  const items = useMemo<DatasetItem[]>(() => {
    const real = datasetItems[id];
    if (real && real.length > 0) return real;
    return generateFallbackItems(id, Math.min(8, dataset.itemCount));
  }, [id, dataset.itemCount]);

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
              href="/playground"
              className="inline-flex h-8 items-center gap-2 rounded-md border border-zinc-700 bg-transparent px-3 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
            >
              <FlaskConical className="h-4 w-4" aria-hidden />
              실험에서 사용
            </Link>
            <Button variant="ghost">
              <Download className="h-4 w-4" aria-hidden />
              다운로드
            </Button>
            <Button variant="ghost" className="text-rose-300 hover:text-rose-200">
              <Trash2 className="h-4 w-4" aria-hidden />
              삭제
            </Button>
          </>
        }
      />

      {/* Meta cards */}
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetaCard
          label="아이템 수"
          value={formatNumber(dataset.itemCount)}
          icon={<Database className="h-4 w-4" />}
        />
        <MetaCard
          label="생성일"
          value={formatRelativeDate(dataset.createdAt)}
        />
        <MetaCard
          label="최근 사용"
          value={dataset.lastUsed ? formatRelativeDate(dataset.lastUsed) : "—"}
        />
        <MetaCard
          label="최근 실험 수"
          value={`${dataset.recentExperimentCount}건`}
        />
      </div>

      {/* Items table */}
      <Card>
        {items.length === 0 ? (
          <CardContent>
            <EmptyState
              icon={<Database className="h-8 w-8" />}
              title="아이템이 없습니다"
              description="이 데이터셋에 아직 데이터가 등록되지 않았습니다."
            />
          </CardContent>
        ) : (
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
                      {i + 1}
                    </td>
                    <td className="max-w-md px-3 py-2">
                      <pre className="line-clamp-3 whitespace-pre-wrap break-all rounded bg-zinc-950/60 p-2 font-mono text-xs text-zinc-200">
                        {JSON.stringify(it.input, null, 0)}
                      </pre>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-zinc-100">
                      {it.expectedOutput}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(it.metadata).map(([k, v]) => (
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
