import { cn, scoreColor } from "@/lib/utils";

export function ScoreBadge({
  value,
  size = "md",
  showDot = true,
}: {
  value: number | null;
  size?: "sm" | "md";
  showDot?: boolean;
}) {
  const c = scoreColor(value);
  const display = value === null ? "—" : value.toFixed(2);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 font-mono tabular-nums",
        c.bg,
        c.fg,
        size === "sm" ? "text-[11px]" : "text-xs"
      )}
    >
      {showDot && (
        <span className={cn("inline-block h-1.5 w-1.5 rounded-full", c.dot)} />
      )}
      {display}
    </span>
  );
}

export function ScoreBar({
  value,
  className,
}: {
  value: number | null;
  className?: string;
}) {
  const c = scoreColor(value);
  const pct = value === null ? 0 : Math.max(0, Math.min(100, value * 100));
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={cn("h-full transition-all duration-500", c.dot)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={cn("font-mono text-xs tabular-nums", c.fg)}>
        {value === null ? "—" : value.toFixed(2)}
      </span>
    </div>
  );
}
