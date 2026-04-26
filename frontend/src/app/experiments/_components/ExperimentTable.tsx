"use client";

import { useRouter } from "next/navigation";
import { ArrowDown, ArrowUp, MoreVertical } from "lucide-react";
import type { Experiment } from "@/lib/mock/types";
import { StatusDot } from "@/components/ui/StatusDot";
import { ScoreBadge } from "@/components/ui/ScoreBadge";
import { Button } from "@/components/ui/Button";
import { cn, formatCurrency, formatRelativeDate } from "@/lib/utils";

export type SortKey = "createdAt" | "totalCostUsd" | "avgScore";
export type SortDir = "asc" | "desc";

interface ExperimentTableProps {
  experiments: Experiment[];
  sortKey: SortKey;
  sortDir: SortDir;
  onSortChange: (key: SortKey) => void;
}

const COLUMNS: Array<{
  key: string;
  label: string;
  sortKey?: SortKey;
  className?: string;
  align?: "left" | "right" | "center";
}> = [
  { key: "name", label: "실험명", className: "min-w-[220px]" },
  { key: "status", label: "상태", className: "w-[96px]" },
  { key: "prompt", label: "프롬프트", className: "min-w-[180px]" },
  { key: "dataset", label: "데이터셋", className: "min-w-[160px]" },
  { key: "models", label: "모델", className: "w-[80px]", align: "right" },
  { key: "runs", label: "Runs", className: "w-[96px]", align: "right" },
  {
    key: "score",
    label: "평균 스코어",
    sortKey: "avgScore",
    className: "w-[120px]",
    align: "right",
  },
  {
    key: "cost",
    label: "총 비용",
    sortKey: "totalCostUsd",
    className: "w-[100px]",
    align: "right",
  },
  {
    key: "created",
    label: "생성일",
    sortKey: "createdAt",
    className: "w-[120px]",
    align: "right",
  },
  { key: "actions", label: "", className: "w-[40px]" },
];

export function ExperimentTable({
  experiments,
  sortKey,
  sortDir,
  onSortChange,
}: ExperimentTableProps) {
  const router = useRouter();

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900">
      <table className="w-full table-fixed border-collapse text-sm">
        <thead>
          <tr className="border-b border-zinc-800 bg-zinc-950/40">
            {COLUMNS.map((col) => {
              const isSortable = !!col.sortKey;
              const isSorted = isSortable && sortKey === col.sortKey;
              const ariaSort: "ascending" | "descending" | "none" | undefined =
                isSortable
                  ? isSorted
                    ? sortDir === "asc"
                      ? "ascending"
                      : "descending"
                    : "none"
                  : undefined;
              return (
                <th
                  key={col.key}
                  scope="col"
                  aria-sort={ariaSort}
                  className={cn(
                    "px-3 py-2 text-xs font-medium uppercase tracking-wide text-zinc-500",
                    col.align === "right" && "text-right",
                    col.align === "center" && "text-center",
                    col.align !== "right" &&
                      col.align !== "center" &&
                      "text-left",
                    col.className
                  )}
                >
                  {isSortable ? (
                    <button
                      type="button"
                      onClick={() => col.sortKey && onSortChange(col.sortKey)}
                      className={cn(
                        "inline-flex items-center gap-1 transition-colors hover:text-zinc-200",
                        isSorted && "text-zinc-200"
                      )}
                    >
                      {col.label}
                      {isSorted &&
                        (sortDir === "asc" ? (
                          <ArrowUp className="h-3 w-3" aria-hidden />
                        ) : (
                          <ArrowDown className="h-3 w-3" aria-hidden />
                        ))}
                    </button>
                  ) : (
                    col.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {experiments.map((exp) => (
            <tr
              key={exp.id}
              tabIndex={0}
              onClick={() => router.push(`/experiments/${exp.id}`)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  router.push(`/experiments/${exp.id}`);
                }
              }}
              className="cursor-pointer border-b border-zinc-900 transition-colors last:border-b-0 hover:bg-zinc-800/50 focus:bg-zinc-800/50 focus:outline-none"
            >
              <td className="px-3 py-2.5 align-middle">
                <div className="truncate font-medium text-zinc-100">
                  {exp.name}
                </div>
                {exp.description && (
                  <div className="mt-0.5 truncate text-xs text-zinc-500">
                    {exp.description}
                  </div>
                )}
              </td>
              <td className="px-3 py-2.5 align-middle">
                <StatusDot status={exp.status} />
              </td>
              <td className="px-3 py-2.5 align-middle">
                <div className="truncate text-zinc-200">{exp.promptName}</div>
                <div className="mt-0.5 truncate font-mono text-[11px] text-zinc-500">
                  {exp.promptVersions.map((v) => `v${v}`).join(" + ")}
                </div>
              </td>
              <td className="px-3 py-2.5 align-middle">
                <div className="truncate text-zinc-300">{exp.datasetName}</div>
                <div className="mt-0.5 text-[11px] text-zinc-500">
                  {exp.itemCount} items
                </div>
              </td>
              <td className="px-3 py-2.5 text-right align-middle font-mono tabular-nums text-zinc-200">
                {exp.modelIds.length}
              </td>
              <td className="px-3 py-2.5 text-right align-middle font-mono tabular-nums text-zinc-200">
                <span className="text-zinc-400">{exp.completedRuns}</span>
                <span className="text-zinc-600"> / </span>
                <span>{exp.runCount}</span>
              </td>
              <td className="px-3 py-2.5 text-right align-middle">
                <div className="inline-flex justify-end">
                  <ScoreBadge value={exp.avgScore} />
                </div>
              </td>
              <td className="px-3 py-2.5 text-right align-middle font-mono tabular-nums text-zinc-200">
                {formatCurrency(exp.totalCostUsd, 2)}
              </td>
              <td className="px-3 py-2.5 text-right align-middle text-xs text-zinc-400">
                {formatRelativeDate(exp.createdAt)}
              </td>
              <td className="px-3 py-2.5 text-right align-middle">
                <Button
                  variant="ghost"
                  size="iconSm"
                  aria-label={`${exp.name} 액션`}
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreVertical className="h-4 w-4 text-zinc-500" />
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
