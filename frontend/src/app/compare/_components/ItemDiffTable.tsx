"use client";

import { motion } from "framer-motion";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/Badge";
import { ScoreBadge } from "@/components/ui/ScoreBadge";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/utils";
import type { ItemDiffRow, ItemResult, SelectedRun, SortMode } from "./types";

interface ItemDiffTableProps {
  runs: SelectedRun[];
  itemResults: ItemResult[];
}

function computeDiff(scores: Array<number | null>): number {
  const valid = scores.filter((s): s is number => s !== null);
  if (valid.length < 2) return 0;
  return Math.max(...valid) - Math.min(...valid);
}

function buildRows(
  runs: SelectedRun[],
  itemResults: ItemResult[]
): ItemDiffRow[] {
  return itemResults.map((item) => {
    const scoresByRun: Record<string, number | null> = {};
    runs.forEach((r) => {
      scoresByRun[r.id] = item.scoresByRun[r.id] ?? null;
    });
    const scores = runs.map((r) => scoresByRun[r.id]);
    const diff = computeDiff(scores);
    const hasFailure = scores.some(
      (s) => s !== null && s < 0.5
    );
    return {
      itemId: item.itemId,
      itemIndex: item.itemIndex,
      input: item.input,
      expected: item.expected,
      scoresByRun,
      outputs: item.outputs,
      diff,
      hasFailure,
    };
  });
}

const SORT_OPTIONS: Array<{ value: SortMode; label: string }> = [
  { value: "diff_desc", label: "스코어 차이 큰 순" },
  { value: "diff_asc", label: "스코어 차이 작은 순" },
  { value: "index_asc", label: "아이템 순서" },
];

export function ItemDiffTable({ runs, itemResults }: ItemDiffTableProps) {
  const [sortMode, setSortMode] = useState<SortMode>("diff_desc");
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const allRows = useMemo(
    () => buildRows(runs, itemResults),
    [runs, itemResults]
  );

  const filteredSorted = useMemo(() => {
    let rows = allRows;
    if (failuresOnly) {
      rows = rows.filter((r) => r.hasFailure);
    }
    const sorted = [...rows];
    if (sortMode === "diff_desc") {
      sorted.sort((a, b) => b.diff - a.diff);
    } else if (sortMode === "diff_asc") {
      sorted.sort((a, b) => a.diff - b.diff);
    } else {
      sorted.sort((a, b) => a.itemIndex - b.itemIndex);
    }
    return sorted;
  }, [allRows, sortMode, failuresOnly]);

  const ariaSort: "ascending" | "descending" | "none" =
    sortMode === "diff_desc"
      ? "descending"
      : sortMode === "diff_asc"
      ? "ascending"
      : "none";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <div className="w-[180px]">
            <label className="sr-only" htmlFor="diff-sort">
              정렬
            </label>
            <Select
              id="diff-sort"
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as SortMode)}
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-zinc-300">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 cursor-pointer rounded border-zinc-700 bg-zinc-800 text-indigo-400 focus:ring-1 focus:ring-indigo-400"
              checked={failuresOnly}
              onChange={(e) => setFailuresOnly(e.target.checked)}
            />
            실패만 (스코어 &lt; 0.5)
          </label>
        </div>
        <span className="text-xs text-zinc-500">
          {filteredSorted.length} / {allRows.length} 행
        </span>
      </div>

      <div className="overflow-hidden rounded-lg border border-zinc-800">
        <div className="max-h-[520px] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-zinc-900">
              <tr className="border-b border-zinc-800 text-left text-[11px] uppercase tracking-wide text-zinc-500">
                <th scope="col" className="w-12 px-3 py-2 font-medium">
                  #
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Input
                </th>
                {runs.map((r) => (
                  <th
                    key={r.id}
                    scope="col"
                    className="px-3 py-2 font-medium"
                    style={{ minWidth: "100px" }}
                  >
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{ backgroundColor: r.color }}
                        aria-hidden
                      />
                      {r.shortLabel}
                    </span>
                  </th>
                ))}
                <th
                  scope="col"
                  className="px-3 py-2 font-medium"
                  aria-sort={ariaSort}
                  style={{ minWidth: "120px" }}
                >
                  Diff
                </th>
                <th scope="col" className="w-8 px-2 py-2" aria-label="펼치기" />
              </tr>
            </thead>
            <tbody>
              {filteredSorted.length === 0 && (
                <tr>
                  <td
                    colSpan={runs.length + 4}
                    className="px-3 py-12 text-center text-sm text-zinc-500"
                  >
                    표시할 행이 없습니다.
                  </td>
                </tr>
              )}
              {filteredSorted.map((row) => {
                const isExpanded = expandedId === row.itemId;
                return (
                  <ItemDiffRowView
                    key={row.itemId}
                    row={row}
                    runs={runs}
                    isExpanded={isExpanded}
                    onToggle={() =>
                      setExpandedId(isExpanded ? null : row.itemId)
                    }
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function ItemDiffRowView({
  row,
  runs,
  isExpanded,
  onToggle,
}: {
  row: ItemDiffRow;
  runs: SelectedRun[];
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const diffPct = Math.min(100, Math.abs(row.diff) * 100);
  const diffColor =
    row.diff >= 0.4
      ? "bg-rose-400"
      : row.diff >= 0.2
      ? "bg-amber-400"
      : "bg-emerald-400";

  return (
    <>
      <tr
        className={cn(
          "border-b border-zinc-900 transition-colors hover:bg-zinc-800/40",
          isExpanded && "bg-zinc-800/30",
          row.hasFailure && "bg-rose-950/10"
        )}
        style={{ height: "40px" }}
      >
        <td className="px-3 py-1.5 align-middle font-mono text-xs text-zinc-500 tabular-nums">
          {row.itemIndex}
        </td>
        <td className="px-3 py-1.5 align-middle">
          <div
            className="max-w-[300px] truncate text-xs text-zinc-300"
            title={row.input}
          >
            {row.input}
          </div>
        </td>
        {runs.map((r) => (
          <td key={r.id} className="px-3 py-1.5 align-middle">
            <ScoreBadge value={row.scoresByRun[r.id] ?? null} size="sm" />
          </td>
        ))}
        <td className="px-3 py-1.5 align-middle">
          <div className="flex items-center gap-2">
            <div
              className="h-1.5 w-20 overflow-hidden rounded-full bg-zinc-800"
              aria-hidden
            >
              <div
                className={cn("h-full transition-all", diffColor)}
                style={{ width: `${diffPct}%` }}
              />
            </div>
            <span className="font-mono text-[11px] text-zinc-400 tabular-nums">
              {row.diff.toFixed(2)}
            </span>
          </div>
        </td>
        <td className="px-2 py-1.5 align-middle">
          <button
            type="button"
            onClick={onToggle}
            aria-label={isExpanded ? "닫기" : "상세 보기"}
            aria-expanded={isExpanded}
            className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
          >
            {isExpanded ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
        </td>
      </tr>
      {isExpanded && (
        <tr className="border-b border-zinc-900 bg-zinc-950/40">
          <td colSpan={runs.length + 4} className="px-3 py-3">
            <motion.div
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.18 }}
              className="flex flex-col gap-3"
            >
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div>
                  <div className="mb-1 text-[11px] uppercase tracking-wide text-zinc-500">
                    Input
                  </div>
                  <div className="rounded-md border border-zinc-800 bg-zinc-900/60 p-2 text-xs text-zinc-200">
                    {row.input}
                  </div>
                </div>
                <div>
                  <div className="mb-1 flex items-center gap-2 text-[11px] uppercase tracking-wide text-zinc-500">
                    Expected
                    <Badge tone="muted">정답</Badge>
                  </div>
                  <div className="rounded-md border border-zinc-800 bg-zinc-900/60 p-2 font-mono text-xs text-emerald-300">
                    {row.expected}
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {runs.map((r) => {
                  const score = row.scoresByRun[r.id];
                  const output = row.outputs[r.id] ?? "—";
                  return (
                    <div key={r.id}>
                      <div className="mb-1 flex items-center justify-between text-[11px] uppercase tracking-wide text-zinc-500">
                        <span className="inline-flex items-center gap-1.5">
                          <span
                            className="inline-block h-2 w-2 rounded-full"
                            style={{ backgroundColor: r.color }}
                            aria-hidden
                          />
                          {r.shortLabel}
                        </span>
                        <ScoreBadge value={score ?? null} size="sm" />
                      </div>
                      <div className="rounded-md border border-zinc-800 bg-zinc-900/60 p-2 font-mono text-xs text-zinc-200">
                        {output}
                      </div>
                    </div>
                  );
                })}
              </div>
            </motion.div>
          </td>
        </tr>
      )}
    </>
  );
}
