"use client";

import { ImageIcon, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

interface AttachedImage {
  id: string;
  name: string;
  sizeBytes: number;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function ImageAttachmentPanel() {
  const [items, setItems] = useState<AttachedImage[]>([]);
  const [hover, setHover] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = (files: FileList | File[]) => {
    const next: AttachedImage[] = [];
    Array.from(files).forEach((f) => {
      next.push({
        id: `${f.name}-${f.size}-${Date.now()}-${Math.random()
          .toString(36)
          .slice(2, 6)}`,
        name: f.name,
        sizeBytes: f.size,
      });
    });
    if (next.length > 0) setItems((prev) => [...prev, ...next]);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setHover(false);
    if (e.dataTransfer?.files?.length) addFiles(e.dataTransfer.files);
  };

  return (
    <div className="flex flex-col gap-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setHover(true);
        }}
        onDragLeave={() => setHover(false)}
        onDrop={onDrop}
        className={cn(
          "flex flex-col items-center justify-center gap-2 rounded-md border border-dashed",
          "px-4 py-6 text-center transition-colors",
          hover
            ? "border-indigo-400/60 bg-indigo-500/5"
            : "border-zinc-700 bg-zinc-900/40"
        )}
      >
        <ImageIcon className="h-5 w-5 text-zinc-500" aria-hidden />
        <p className="text-xs text-zinc-400">
          이미지를 드래그하거나{" "}
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="text-indigo-300 hover:text-indigo-200 hover:underline"
          >
            파일 선택
          </button>
        </p>
        <p className="text-[11px] text-zinc-500">
          mock 환경 — 실제 업로드는 발생하지 않습니다
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {items.length > 0 && (
        <ul className="flex flex-col gap-1">
          {items.map((it) => (
            <li
              key={it.id}
              className={cn(
                "flex items-center justify-between gap-2 rounded-md border border-zinc-800",
                "bg-zinc-900 px-3 py-1.5"
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                <ImageIcon className="h-3.5 w-3.5 shrink-0 text-zinc-500" />
                <span className="truncate text-xs text-zinc-200">{it.name}</span>
                <span className="shrink-0 font-mono text-[11px] text-zinc-500">
                  {formatBytes(it.sizeBytes)}
                </span>
              </span>
              <Button
                type="button"
                size="iconSm"
                variant="ghost"
                aria-label={`${it.name} 제거`}
                onClick={() =>
                  setItems((prev) => prev.filter((i) => i.id !== it.id))
                }
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
