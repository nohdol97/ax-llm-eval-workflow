"use client";

import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import type { AutoEvalSchedule, ScheduleType } from "@/lib/types/api";
import { formatSchedule } from "./scheduleFormat";

interface ScheduleInputProps {
  value: AutoEvalSchedule;
  onChange: (next: AutoEvalSchedule) => void;
}

const SCHEDULE_TYPE_OPTIONS: Array<{ id: ScheduleType; label: string }> = [
  { id: "cron", label: "Cron — 정해진 시각에 실행" },
  { id: "interval", label: "Interval — N초마다 실행" },
  { id: "event", label: "Event — 특정 사건 발생 시" },
];

const TIMEZONE_OPTIONS = ["Asia/Seoul", "UTC", "America/Los_Angeles"];

const EVENT_TRIGGER_OPTIONS: Array<{
  id: NonNullable<AutoEvalSchedule["event_trigger"]>;
  label: string;
}> = [
  { id: "new_traces", label: "신규 trace 누적" },
  { id: "scheduled_dataset_run", label: "예약 데이터셋 실행 후" },
];

export function ScheduleInput({ value, onChange }: ScheduleInputProps) {
  const handleTypeChange = (type: ScheduleType) => {
    if (type === "cron") {
      onChange({
        type: "cron",
        cron_expression: value.cron_expression ?? "0 3 * * *",
        timezone: value.timezone ?? "Asia/Seoul",
      });
    } else if (type === "interval") {
      onChange({
        type: "interval",
        interval_seconds: value.interval_seconds ?? 3600,
      });
    } else {
      onChange({
        type: "event",
        event_trigger: value.event_trigger ?? "new_traces",
        event_threshold: value.event_threshold ?? 100,
      });
    }
  };

  return (
    <div className="space-y-4 rounded-md border border-zinc-800 bg-zinc-900/50 p-4">
      <div className="space-y-1.5">
        <label
          htmlFor="schedule-type"
          className="block text-sm font-medium text-zinc-200"
        >
          스케줄 종류
        </label>
        <Select
          id="schedule-type"
          value={value.type}
          onChange={(e) => handleTypeChange(e.target.value as ScheduleType)}
        >
          {SCHEDULE_TYPE_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </Select>
      </div>

      {value.type === "cron" && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label
              htmlFor="schedule-cron"
              className="block text-sm font-medium text-zinc-200"
            >
              Cron expression
            </label>
            <Input
              id="schedule-cron"
              placeholder="0 3 * * *"
              value={value.cron_expression ?? ""}
              onChange={(e) =>
                onChange({ ...value, cron_expression: e.target.value })
              }
            />
            <p className="text-[11px] text-zinc-500">
              5필드 표준 cron (분 시 일 월 요일)
            </p>
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="schedule-timezone"
              className="block text-sm font-medium text-zinc-200"
            >
              Timezone
            </label>
            <Select
              id="schedule-timezone"
              value={value.timezone ?? "Asia/Seoul"}
              onChange={(e) => onChange({ ...value, timezone: e.target.value })}
            >
              {TIMEZONE_OPTIONS.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </Select>
          </div>
        </div>
      )}

      {value.type === "interval" && (
        <div className="space-y-1.5">
          <label
            htmlFor="schedule-interval"
            className="block text-sm font-medium text-zinc-200"
          >
            실행 간격 (초)
          </label>
          <Input
            id="schedule-interval"
            type="number"
            min={60}
            value={value.interval_seconds ?? 3600}
            onChange={(e) =>
              onChange({
                ...value,
                interval_seconds: Number(e.target.value),
              })
            }
          />
          <p className="text-[11px] text-zinc-500">
            최소 60초. 1시간 = 3600, 1일 = 86400.
          </p>
        </div>
      )}

      {value.type === "event" && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label
              htmlFor="schedule-trigger"
              className="block text-sm font-medium text-zinc-200"
            >
              트리거
            </label>
            <Select
              id="schedule-trigger"
              value={value.event_trigger ?? "new_traces"}
              onChange={(e) =>
                onChange({
                  ...value,
                  event_trigger: e.target
                    .value as AutoEvalSchedule["event_trigger"],
                })
              }
            >
              {EVENT_TRIGGER_OPTIONS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <label
              htmlFor="schedule-threshold"
              className="block text-sm font-medium text-zinc-200"
            >
              임계 건수
            </label>
            <Input
              id="schedule-threshold"
              type="number"
              min={1}
              value={value.event_threshold ?? 100}
              onChange={(e) =>
                onChange({
                  ...value,
                  event_threshold: Number(e.target.value),
                })
              }
            />
          </div>
        </div>
      )}

      <div className="rounded-md border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-xs">
        <span className="text-zinc-500">미리보기 · </span>
        <span className="text-emerald-300">{formatSchedule(value)}</span>
      </div>
    </div>
  );
}
