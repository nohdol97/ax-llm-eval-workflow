"use client";

import { ChangeEvent, useMemo, useRef, useState } from "react";
import { Check, FileUp, UploadCloud } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { cn } from "@/lib/utils";

type ColumnRole = "input" | "expected_output" | "metadata" | "ignore";

interface UploadDatasetModalProps {
  open: boolean;
  onClose: () => void;
}

const MOCK_COLUMNS = ["text", "label", "language", "domain"];

const MOCK_PREVIEW: Record<string, string>[] = [
  {
    text: "이 제품 정말 최고예요. 추천합니다!",
    label: "positive",
    language: "ko",
    domain: "review",
  },
  {
    text: "배송이 너무 늦어서 화가 나네요.",
    label: "negative",
    language: "ko",
    domain: "review",
  },
  {
    text: "그냥 평범한 수준입니다.",
    label: "neutral",
    language: "ko",
    domain: "review",
  },
  {
    text: "Worst experience ever.",
    label: "negative",
    language: "en",
    domain: "review",
  },
  {
    text: "Amazing quality, will buy again.",
    label: "positive",
    language: "en",
    domain: "review",
  },
];

const STEPS = [
  { id: 1, label: "파일 선택" },
  { id: 2, label: "매핑 설정" },
  { id: 3, label: "확인" },
] as const;

export function UploadDatasetModal({ open, onClose }: UploadDatasetModalProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [fileName, setFileName] = useState<string | null>(null);
  const [datasetName, setDatasetName] = useState("");
  const [mapping, setMapping] = useState<Record<string, ColumnRole>>({
    text: "input",
    label: "expected_output",
    language: "metadata",
    domain: "metadata",
  });
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setStep(1);
    setFileName(null);
    setDatasetName("");
    setDragActive(false);
  };

  const handleClose = () => {
    onClose();
    // delay reset until after exit animation
    setTimeout(reset, 250);
  };

  const onPickFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      setFileName(f.name);
      setDatasetName(f.name.replace(/\.(csv|jsonl|json)$/i, ""));
    }
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f) {
      setFileName(f.name);
      setDatasetName(f.name.replace(/\.(csv|jsonl|json)$/i, ""));
    }
  };

  const mappingValid = useMemo(() => {
    const roles = Object.values(mapping);
    return roles.includes("input") && roles.includes("expected_output");
  }, [mapping]);

  const setRole = (col: string, role: ColumnRole) =>
    setMapping((m) => ({ ...m, [col]: role }));

  return (
    <Modal
      open={open}
      onClose={handleClose}
      size="lg"
      title="데이터셋 업로드"
      description="CSV 또는 JSONL 파일을 업로드하고 컬럼을 매핑하세요"
      footer={
        <>
          <Button variant="ghost" onClick={handleClose}>
            취소
          </Button>
          {step > 1 && (
            <Button
              variant="outline"
              onClick={() => setStep((s) => (s - 1) as 1 | 2 | 3)}
            >
              이전
            </Button>
          )}
          {step < 3 && (
            <Button
              variant="primary"
              disabled={
                (step === 1 && !fileName) || (step === 2 && !mappingValid)
              }
              onClick={() => setStep((s) => (s + 1) as 1 | 2 | 3)}
            >
              다음
            </Button>
          )}
          {step === 3 && (
            <Button variant="primary" onClick={handleClose}>
              업로드
            </Button>
          )}
        </>
      }
    >
      {/* Stepper */}
      <ol className="mb-6 flex items-center justify-between">
        {STEPS.map((s, i) => {
          const active = step === s.id;
          const done = step > s.id;
          return (
            <li
              key={s.id}
              className="flex flex-1 items-center gap-2"
              aria-current={active ? "step" : undefined}
            >
              <span
                className={cn(
                  "grid h-7 w-7 place-items-center rounded-full border text-xs font-semibold",
                  active && "border-indigo-400 bg-indigo-500/15 text-indigo-200",
                  done && "border-emerald-700 bg-emerald-500/10 text-emerald-300",
                  !active && !done && "border-zinc-700 bg-zinc-900 text-zinc-400"
                )}
              >
                {done ? <Check className="h-3.5 w-3.5" /> : s.id}
              </span>
              <span
                className={cn(
                  "text-xs font-medium",
                  active ? "text-zinc-100" : "text-zinc-500"
                )}
              >
                {s.label}
              </span>
              {i < STEPS.length - 1 && (
                <div
                  aria-hidden
                  className={cn(
                    "ml-1 h-px flex-1",
                    done ? "bg-emerald-700/60" : "bg-zinc-800"
                  )}
                />
              )}
            </li>
          );
        })}
      </ol>

      {step === 1 && (
        <div className="space-y-4">
          <div
            role="button"
            tabIndex={0}
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                fileInputRef.current?.click();
              }
            }}
            onDragOver={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={onDrop}
            className={cn(
              "flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors",
              dragActive
                ? "border-indigo-500/60 bg-indigo-500/5"
                : "border-zinc-700 bg-zinc-950/50 hover:border-indigo-500/50 hover:bg-zinc-900"
            )}
          >
            <UploadCloud className="h-8 w-8 text-zinc-500" aria-hidden />
            <div>
              <p className="text-sm font-medium text-zinc-100">
                파일을 드래그하거나 클릭하여 선택
              </p>
              <p className="mt-1 text-xs text-zinc-500">
                CSV, JSONL · 최대 50MB
              </p>
            </div>
            {fileName && (
              <div className="mt-2 inline-flex items-center gap-2 rounded-md border border-emerald-800 bg-emerald-950/40 px-3 py-1.5 text-xs text-emerald-300">
                <FileUp className="h-3.5 w-3.5" aria-hidden />
                {fileName}
              </div>
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.jsonl,.json"
              className="hidden"
              onChange={onPickFile}
            />
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="dataset-name"
              className="text-xs font-medium text-zinc-300"
            >
              데이터셋 이름
            </label>
            <Input
              id="dataset-name"
              placeholder="예: my-eval-set-100"
              value={datasetName}
              onChange={(e) => setDatasetName(e.target.value)}
            />
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-5">
          <div className="rounded-md border border-zinc-800 bg-zinc-950/40 p-3 text-xs text-zinc-400">
            각 컬럼을 <strong className="text-zinc-200">input</strong>,{" "}
            <strong className="text-zinc-200">expected_output</strong>,{" "}
            <strong className="text-zinc-200">metadata</strong> 중 하나로
            매핑하세요. input과 expected_output은 최소 1개 이상 필요합니다.
          </div>

          <div className="overflow-hidden rounded-md border border-zinc-800">
            <table className="w-full text-sm">
              <thead className="bg-zinc-950/40 text-xs text-zinc-400">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    원본 컬럼
                  </th>
                  <th scope="col" className="px-3 py-2 text-left font-medium">
                    역할 매핑
                  </th>
                </tr>
              </thead>
              <tbody>
                {MOCK_COLUMNS.map((col) => (
                  <tr
                    key={col}
                    className="border-t border-zinc-800 even:bg-zinc-950/20"
                  >
                    <td className="px-3 py-2 font-mono text-xs text-zinc-200">
                      {col}
                    </td>
                    <td className="px-3 py-2">
                      <Select
                        value={mapping[col] ?? "ignore"}
                        onChange={(e) =>
                          setRole(col, e.target.value as ColumnRole)
                        }
                        aria-label={`${col} 매핑`}
                        className="max-w-[220px]"
                      >
                        <option value="input">input</option>
                        <option value="expected_output">expected_output</option>
                        <option value="metadata">metadata</option>
                        <option value="ignore">무시</option>
                      </Select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">
              미리보기 (5건)
            </h4>
            <div className="overflow-x-auto rounded-md border border-zinc-800">
              <table className="w-full text-xs">
                <thead className="bg-zinc-950/40 text-zinc-400">
                  <tr>
                    {MOCK_COLUMNS.map((c) => (
                      <th
                        key={c}
                        scope="col"
                        className="whitespace-nowrap px-3 py-2 text-left font-medium"
                      >
                        {c}
                        <span className="ml-1.5 rounded bg-zinc-800 px-1 py-0.5 text-[10px] text-zinc-400">
                          {mapping[c] ?? "ignore"}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {MOCK_PREVIEW.map((row, i) => (
                    <tr
                      key={i}
                      className="border-t border-zinc-800 even:bg-zinc-950/20"
                    >
                      {MOCK_COLUMNS.map((c) => (
                        <td
                          key={c}
                          className="whitespace-nowrap px-3 py-2 font-mono text-zinc-300"
                        >
                          {row[c]}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          <h3 className="text-sm font-semibold text-zinc-100">업로드 확인</h3>
          <dl className="divide-y divide-zinc-800 rounded-md border border-zinc-800">
            <div className="flex items-center justify-between px-4 py-2.5">
              <dt className="text-xs text-zinc-400">데이터셋 이름</dt>
              <dd className="text-sm text-zinc-100">{datasetName || "—"}</dd>
            </div>
            <div className="flex items-center justify-between px-4 py-2.5">
              <dt className="text-xs text-zinc-400">파일</dt>
              <dd className="text-sm text-zinc-100">{fileName || "—"}</dd>
            </div>
            <div className="flex items-center justify-between px-4 py-2.5">
              <dt className="text-xs text-zinc-400">아이템 수 (예상)</dt>
              <dd className="text-sm text-zinc-100">100</dd>
            </div>
            <div className="px-4 py-2.5">
              <dt className="mb-1.5 text-xs text-zinc-400">매핑</dt>
              <dd className="flex flex-wrap gap-1.5">
                {Object.entries(mapping).map(([col, role]) => (
                  <span
                    key={col}
                    className="inline-flex items-center gap-1 rounded border border-zinc-700 bg-zinc-950 px-2 py-0.5 font-mono text-[11px] text-zinc-300"
                  >
                    {col}
                    <span className="text-zinc-500">→</span>
                    <span className="text-indigo-300">{role}</span>
                  </span>
                ))}
              </dd>
            </div>
          </dl>
          <p className="text-xs text-zinc-500">
            업로드 후에는 데이터셋 목록에서 확인할 수 있습니다.
          </p>
        </div>
      )}
    </Modal>
  );
}
