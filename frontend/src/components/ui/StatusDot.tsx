import { cn } from "@/lib/utils";
import type { ExperimentStatus } from "@/lib/mock/types";

const STATUS_LABEL: Record<ExperimentStatus, string> = {
  completed: "완료",
  running: "진행중",
  paused: "일시정지",
  failed: "실패",
  cancelled: "취소됨",
};

const STATUS_COLOR: Record<ExperimentStatus, string> = {
  completed: "bg-emerald-400",
  running: "bg-amber-400 animate-pulse-dot",
  paused: "bg-sky-400",
  failed: "bg-rose-400",
  cancelled: "bg-zinc-500",
};

const STATUS_TEXT: Record<ExperimentStatus, string> = {
  completed: "text-emerald-300",
  running: "text-amber-300",
  paused: "text-sky-300",
  failed: "text-rose-300",
  cancelled: "text-zinc-400 line-through",
};

export function StatusDot({
  status,
  showLabel = true,
  className,
}: {
  status: ExperimentStatus;
  showLabel?: boolean;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span
        aria-hidden
        className={cn("inline-block h-2 w-2 rounded-full", STATUS_COLOR[status])}
      />
      {showLabel && (
        <span className={cn("text-xs font-medium", STATUS_TEXT[status])}>
          {STATUS_LABEL[status]}
        </span>
      )}
    </span>
  );
}

export function HealthDot({
  state,
  className,
}: {
  state: "ok" | "warn" | "error";
  className?: string;
}) {
  const color =
    state === "ok"
      ? "bg-emerald-400"
      : state === "warn"
      ? "bg-amber-400"
      : "bg-rose-400";
  const label = state === "ok" ? "정상" : state === "warn" ? "경고" : "오류";
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className={cn("inline-block h-2 w-2 rounded-full", color)} />
      <span className="text-xs text-zinc-400">{label}</span>
    </span>
  );
}
