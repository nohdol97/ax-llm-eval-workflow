import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatCurrency(value: number, fractionDigits = 4): string {
  return `$${value.toFixed(fractionDigits)}`;
}

export function formatNumber(value: number): string {
  return value.toLocaleString("ko-KR");
}

export function formatPercent(value: number, fractionDigits = 1): string {
  return `${(value * 100).toFixed(fractionDigits)}%`;
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return `${m}분 ${s}초`;
}

export function formatRelativeDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const day = 1000 * 60 * 60 * 24;
  if (diff < 60_000) return "방금";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}분 전`;
  if (diff < day) return `${Math.floor(diff / 3_600_000)}시간 전`;
  if (diff < day * 7) return `${Math.floor(diff / day)}일 전`;
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).format(d);
}

export type ScoreBucket = "low" | "mid" | "high" | "best" | "null";

export function scoreBucket(value: number | null): ScoreBucket {
  if (value === null || Number.isNaN(value)) return "null";
  if (value < 0.3) return "low";
  if (value < 0.7) return "mid";
  if (value < 0.9) return "high";
  return "best";
}

export function scoreColor(value: number | null): { bg: string; fg: string; dot: string } {
  const b = scoreBucket(value);
  switch (b) {
    case "low":
      return { bg: "bg-rose-950/60", fg: "text-rose-300", dot: "bg-rose-400" };
    case "mid":
      return { bg: "bg-amber-950/60", fg: "text-amber-300", dot: "bg-amber-400" };
    case "high":
      return { bg: "bg-emerald-950/60", fg: "text-emerald-300", dot: "bg-emerald-400" };
    case "best":
      return { bg: "bg-emerald-900/60", fg: "text-emerald-200", dot: "bg-emerald-300" };
    default:
      return { bg: "bg-zinc-800", fg: "text-zinc-500", dot: "bg-zinc-600" };
  }
}
