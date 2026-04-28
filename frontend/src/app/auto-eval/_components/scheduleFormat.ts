/**
 * Auto-Eval 스케줄 표현 → 한국어 요약.
 *
 * UI 라벨에서만 사용. 정확한 cron 파싱은 backend가 담당.
 *
 * 지원 패턴 (best-effort):
 *  - ``0 N * * *`` → "매일 N:00 KST"
 *  - ``0 0 * * 1`` → "매주 월요일 00:00"
 *  - 기타 → 원본 cron 문자열 그대로 노출
 */
import type { AutoEvalSchedule } from "@/lib/types/api";

const WEEKDAY_KOR: Record<string, string> = {
  "0": "일요일",
  "1": "월요일",
  "2": "화요일",
  "3": "수요일",
  "4": "목요일",
  "5": "금요일",
  "6": "토요일",
};

export function formatSchedule(schedule: AutoEvalSchedule): string {
  if (schedule.type === "cron") {
    return formatCron(schedule.cron_expression, schedule.timezone);
  }
  if (schedule.type === "interval") {
    const sec = schedule.interval_seconds ?? 0;
    if (sec >= 86_400) return `${Math.round(sec / 86_400)}일마다`;
    if (sec >= 3_600) return `${Math.round(sec / 3_600)}시간마다`;
    if (sec >= 60) return `${Math.round(sec / 60)}분마다`;
    return `${sec}초마다`;
  }
  if (schedule.type === "event") {
    const trigger =
      schedule.event_trigger === "scheduled_dataset_run"
        ? "데이터셋 실행 후"
        : "신규 trace 누적";
    if (schedule.event_threshold) {
      return `${trigger} ${schedule.event_threshold}건 이상`;
    }
    return trigger;
  }
  return "—";
}

export function formatCron(
  expression: string | undefined,
  timezone: string | undefined,
): string {
  if (!expression) return "—";
  const tzSuffix = timezone ? ` ${timezone}` : "";
  const parts = expression.trim().split(/\s+/);
  if (parts.length !== 5) return `${expression}${tzSuffix}`;
  const [minute, hour, dom, month, dow] = parts;

  // 매일 N:MM
  if (dom === "*" && month === "*" && dow === "*") {
    if (/^\d+$/.test(hour) && /^\d+$/.test(minute)) {
      return `매일 ${pad(hour)}:${pad(minute)}${tzSuffix}`;
    }
  }
  // 매주 요일 N:MM
  if (dom === "*" && month === "*" && /^\d$/.test(dow)) {
    const day = WEEKDAY_KOR[dow] ?? `요일${dow}`;
    if (/^\d+$/.test(hour) && /^\d+$/.test(minute)) {
      return `매주 ${day} ${pad(hour)}:${pad(minute)}${tzSuffix}`;
    }
  }
  return `${expression}${tzSuffix}`;
}

function pad(s: string): string {
  return s.length < 2 ? `0${s}` : s;
}
