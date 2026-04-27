import { cn } from "@/lib/utils";

/**
 * Experiment/run status label dot.
 *
 * Accepts the broader backend `ExperimentStatus` union (which includes
 * `pending`/`queued`/`degraded` in addition to the original mock states).
 * The component is permissive on input — unknown statuses fall back to a
 * neutral grey dot — so it can be used by both Phase 1 mock pages and
 * Phase 7+ backend-wired pages without coupling.
 */
export type StatusValue =
  | "pending"
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "cancelled"
  | "degraded"
  | string;

const STATUS_LABEL: Record<string, string> = {
  pending: "대기",
  queued: "대기",
  running: "진행중",
  paused: "일시정지",
  completed: "완료",
  failed: "실패",
  cancelled: "취소됨",
  degraded: "부분 성공",
};

const STATUS_COLOR: Record<string, string> = {
  pending: "bg-zinc-500",
  queued: "bg-zinc-500",
  running: "bg-amber-400 animate-pulse-dot",
  paused: "bg-sky-400",
  completed: "bg-emerald-400",
  failed: "bg-rose-400",
  cancelled: "bg-zinc-500",
  degraded: "bg-amber-400",
};

const STATUS_TEXT: Record<string, string> = {
  pending: "text-zinc-400",
  queued: "text-zinc-400",
  running: "text-amber-300",
  paused: "text-sky-300",
  completed: "text-emerald-300",
  failed: "text-rose-300",
  cancelled: "text-zinc-400 line-through",
  degraded: "text-amber-300",
};

export function StatusDot({
  status,
  showLabel = true,
  className,
}: {
  status: StatusValue;
  showLabel?: boolean;
  className?: string;
}) {
  const color = STATUS_COLOR[status] ?? "bg-zinc-500";
  const text = STATUS_TEXT[status] ?? "text-zinc-400";
  const label = STATUS_LABEL[status] ?? status;
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span
        aria-hidden
        className={cn("inline-block h-2 w-2 rounded-full", color)}
      />
      {showLabel && (
        <span className={cn("text-xs font-medium", text)}>{label}</span>
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
