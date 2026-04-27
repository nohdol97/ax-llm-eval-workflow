"use client";

import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Check, FileUp, UploadCloud } from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import {
  useUploadDataset,
  useUploadPreview,
} from "@/lib/hooks/useDatasets";
import { useDatasetUploadStream } from "@/lib/hooks/useSSE";
import type { UploadProgress, UploadResponse } from "@/lib/types/api";
import { cn } from "@/lib/utils";

type ColumnRole = "input" | "expected_output" | "metadata" | "ignore";

interface UploadDatasetModalProps {
  open: boolean;
  onClose: () => void;
  projectId: string;
}

const STEPS = [
  { id: 1, label: "파일 선택" },
  { id: 2, label: "매핑 설정" },
  { id: 3, label: "확인" },
] as const;

function computePercent(p: UploadProgress | null): number {
  if (!p) return 0;
  if (p.total > 0) return Math.round((p.processed / p.total) * 100);
  if (p.status === "completed") return 100;
  return 0;
}

export function UploadDatasetModal({
  open,
  onClose,
  projectId,
}: UploadDatasetModalProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [file, setFile] = useState<File | null>(null);
  const [datasetName, setDatasetName] = useState("");
  const [mapping, setMapping] = useState<Record<string, ColumnRole>>({});
  const [dragActive, setDragActive] = useState(false);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const previewMutation = useUploadPreview();
  const uploadMutation = useUploadDataset();

  // SSE: uploadId가 있는 동안 자동 구독
  const stream = useDatasetUploadStream({
    uploadId: uploadId,
    enabled: !!uploadId,
  });

  // Step 2 진입 시 미리보기 트리거
  useEffect(() => {
    if (step === 2 && file && !previewMutation.isPending && !previewMutation.data) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("project_id", projectId);
      previewMutation.mutate(fd);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, file]);

  // 파일이 바뀌면 매핑 초기화
  useEffect(() => {
    if (!file) {
      setMapping({});
      previewMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file]);

  const previewColumns: string[] = useMemo(() => {
    return previewMutation.data?.columns ?? [];
  }, [previewMutation.data]);

  const previewRows = useMemo(() => {
    return (previewMutation.data?.preview ?? []).slice(0, 5);
  }, [previewMutation.data]);

  // 컬럼이 새로 들어오면 기본 매핑 부여
  useEffect(() => {
    if (previewColumns.length === 0) return;
    setMapping((prev) => {
      const next: Record<string, ColumnRole> = { ...prev };
      let inputAssigned = Object.values(next).includes("input");
      let outputAssigned = Object.values(next).includes("expected_output");
      for (const col of previewColumns) {
        if (next[col]) continue;
        if (!inputAssigned) {
          next[col] = "input";
          inputAssigned = true;
        } else if (!outputAssigned) {
          next[col] = "expected_output";
          outputAssigned = true;
        } else {
          next[col] = "metadata";
        }
      }
      return next;
    });
  }, [previewColumns]);

  const reset = () => {
    setStep(1);
    setFile(null);
    setDatasetName("");
    setMapping({});
    setDragActive(false);
    setUploadId(null);
    setSubmitError(null);
    previewMutation.reset();
    uploadMutation.reset();
  };

  const isUploading =
    uploadMutation.isPending || (!!uploadId && stream.isStreaming && !stream.error);
  const isDone = stream.progress?.status === "completed";

  const handleClose = () => {
    if (isUploading) return;
    onClose();
    setTimeout(reset, 250);
  };

  const onPickFile = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      setFile(f);
      if (!datasetName) {
        setDatasetName(f.name.replace(/\.(csv|jsonl|json)$/i, ""));
      }
    }
  };

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f) {
      setFile(f);
      if (!datasetName) {
        setDatasetName(f.name.replace(/\.(csv|jsonl|json)$/i, ""));
      }
    }
  };

  const mappingValid = useMemo(() => {
    const roles = Object.values(mapping);
    return roles.includes("input") && roles.includes("expected_output");
  }, [mapping]);

  const setRole = (col: string, role: ColumnRole) =>
    setMapping((m) => ({ ...m, [col]: role }));

  const handleUpload = async () => {
    if (!file || !mappingValid || !datasetName.trim()) return;
    setSubmitError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("project_id", projectId);
      formData.append("dataset_name", datasetName.trim());
      formData.append("mapping", JSON.stringify(mapping));

      const result: UploadResponse = await uploadMutation.mutateAsync({
        formData,
      });
      const id = result?.upload_id;
      if (id) setUploadId(id);
      // 동기 응답에서 즉시 완료된 경우는 SSE 없이 자동 닫힘
      if (result.status === "completed" && !id) {
        setTimeout(() => {
          onClose();
          setTimeout(reset, 250);
        }, 600);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setSubmitError(msg);
    }
  };

  // 완료 시 자동 닫기
  useEffect(() => {
    if (isDone) {
      const t = setTimeout(() => {
        onClose();
        setTimeout(reset, 250);
      }, 600);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDone]);

  const percent = computePercent(stream.progress);

  return (
    <Modal
      open={open}
      onClose={handleClose}
      size="lg"
      title="데이터셋 업로드"
      description="CSV 또는 JSONL 파일을 업로드하고 컬럼을 매핑하세요"
      footer={
        <>
          <Button variant="ghost" onClick={handleClose} disabled={isUploading}>
            취소
          </Button>
          {step > 1 && !uploadId && (
            <Button
              variant="outline"
              onClick={() => setStep((s) => (s - 1) as 1 | 2 | 3)}
              disabled={isUploading}
            >
              이전
            </Button>
          )}
          {step < 3 && (
            <Button
              variant="primary"
              disabled={
                (step === 1 && !file) ||
                (step === 2 && (!mappingValid || previewMutation.isPending))
              }
              onClick={() => setStep((s) => (s + 1) as 1 | 2 | 3)}
            >
              다음
            </Button>
          )}
          {step === 3 && !uploadId && (
            <Button
              variant="primary"
              onClick={handleUpload}
              disabled={isUploading || !mappingValid || !datasetName.trim()}
            >
              {isUploading ? "업로드 중..." : "업로드"}
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
            {file && (
              <div className="mt-2 inline-flex items-center gap-2 rounded-md border border-emerald-800 bg-emerald-950/40 px-3 py-1.5 text-xs text-emerald-300">
                <FileUp className="h-3.5 w-3.5" aria-hidden />
                {file.name} ({Math.round(file.size / 1024)} KB)
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

          {previewMutation.isError && (
            <div className="flex items-start gap-2 rounded-md border border-rose-900/60 bg-rose-950/30 p-3 text-xs text-rose-200">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              미리보기 생성에 실패했습니다. 파일 형식을 확인해 주세요.
            </div>
          )}

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
                {previewColumns.length === 0 ? (
                  <tr>
                    <td colSpan={2} className="px-3 py-6 text-center text-xs text-zinc-500">
                      {previewMutation.isPending
                        ? "미리보기 생성 중…"
                        : "표시할 컬럼이 없습니다."}
                    </td>
                  </tr>
                ) : (
                  previewColumns.map((col) => (
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
                  ))
                )}
              </tbody>
            </table>
          </div>

          {previewRows.length > 0 && (
            <div>
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-400">
                미리보기 ({previewRows.length}건)
              </h4>
              <div className="overflow-x-auto rounded-md border border-zinc-800">
                <table className="w-full text-xs">
                  <thead className="bg-zinc-950/40 text-zinc-400">
                    <tr>
                      {previewColumns.map((c) => (
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
                    {previewRows.map((row, i) => {
                      const flat: Record<string, unknown> = {
                        ...(row.input ?? {}),
                        ...(typeof row.expected_output === "object" && row.expected_output
                          ? (row.expected_output as Record<string, unknown>)
                          : {}),
                        ...(row.metadata ?? {}),
                      };
                      // expected_output이 문자열이면 별도 컬럼은 input/metadata만 채워짐
                      return (
                        <tr
                          key={i}
                          className="border-t border-zinc-800 even:bg-zinc-950/20"
                        >
                          {previewColumns.map((c) => (
                            <td
                              key={c}
                              className="whitespace-nowrap px-3 py-2 font-mono text-zinc-300"
                            >
                              {String(flat[c] ?? "")}
                            </td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
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
              <dd className="text-sm text-zinc-100">
                {file ? `${file.name} (${Math.round(file.size / 1024)} KB)` : "—"}
              </dd>
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

          {(submitError || stream.error) && (
            <div className="flex items-start gap-2 rounded-md border border-rose-900/60 bg-rose-950/30 p-3 text-xs text-rose-200">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {submitError ?? stream.error}
            </div>
          )}

          {uploadId && (
            <div className="space-y-2 rounded-md border border-zinc-800 bg-zinc-950/40 p-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-zinc-300">
                  {isDone
                    ? "업로드 완료"
                    : stream.error
                      ? "업로드 오류"
                      : `업로드 진행 중… (${stream.progress?.processed ?? 0}/${stream.progress?.total ?? 0})`}
                </span>
                <span className="font-mono tabular-nums text-zinc-400">
                  {percent}%
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
                <div
                  className={cn(
                    "h-full transition-[width] duration-200",
                    stream.error ? "bg-rose-500" : "bg-indigo-500"
                  )}
                  style={{ width: `${percent}%` }}
                />
              </div>
            </div>
          )}

          {!uploadId && !submitError && (
            <p className="text-xs text-zinc-500">
              업로드 후에는 데이터셋 목록에서 확인할 수 있습니다.
            </p>
          )}
        </div>
      )}
    </Modal>
  );
}
