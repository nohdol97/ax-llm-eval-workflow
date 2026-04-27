"use client";

import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { HealthDot } from "@/components/ui/StatusDot";
import { useAuth } from "@/lib/auth";
import { useHealth } from "@/lib/hooks/useHealth";
import { useProjectList, useSwitchProject } from "@/lib/hooks/useProjects";
import type { HealthResponse, ServiceHealth } from "@/lib/types/api";

const HEALTH_LABELS: Record<string, string> = {
  langfuse: "Langfuse",
  litellm: "LiteLLM",
  clickhouse: "ClickHouse",
  postgres: "PostgreSQL",
  redis: "Redis (사내)",
  auth: "사내 Auth",
  vault: "Vault",
  labs_redis: "Redis (Labs 전용)",
};

interface HealthEntry {
  key: string;
  label: string;
  state: "ok" | "warn" | "error";
  responseTimeMs?: number;
  endpoint?: string;
  checkedAt?: string;
}

function normalizeServices(
  raw: HealthResponse | undefined
): { entries: HealthEntry[]; latestCheck?: string } {
  if (!raw?.services) return { entries: [] };
  const entries: HealthEntry[] = Object.entries(raw.services).map(
    ([key, val]: [string, ServiceHealth]) => ({
      key,
      label: HEALTH_LABELS[key] ?? key,
      state: val.status,
      responseTimeMs: val.latency_ms,
      endpoint: val.endpoint,
      checkedAt: val.checked_at,
    })
  );
  const latest = entries
    .map((e) => e.checkedAt)
    .filter((x): x is string => !!x)
    .sort()
    .pop();
  return { entries, latestCheck: latest };
}

function formatLastCheck(iso?: string): string {
  if (!iso) return "—";
  try {
    const t = new Date(iso).getTime();
    const diff = Date.now() - t;
    const sec = Math.max(0, Math.floor(diff / 1000));
    if (sec < 5) return "방금";
    if (sec < 60) return `${sec}초 전`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}분 전`;
    const hr = Math.floor(min / 60);
    return `${hr}시간 전`;
  } catch {
    return "—";
  }
}

export function GeneralTab() {
  const { user } = useAuth();
  const projectsQuery = useProjectList();
  const switchProject = useSwitchProject();
  const healthQuery = useHealth();

  const projects = projectsQuery.data?.projects ?? [];
  const initialProjectId =
    (user as { currentProjectId?: string } | null)?.currentProjectId ??
    projects[0]?.id ??
    "";
  const [projectId, setProjectId] = useState(initialProjectId);

  // 사용자 currentProjectId 변경 또는 프로젝트 목록 로드 시 동기화
  useEffect(() => {
    if (projects.length > 0 && !projects.find((p) => p.id === projectId)) {
      setProjectId(projects[0].id);
    }
  }, [projects, projectId]);

  const handleSwitch = (id: string) => {
    if (!id || id === projectId) return;
    setProjectId(id);
    switchProject.mutate?.(id);
  };

  const { entries, latestCheck } = useMemo(
    () => normalizeServices(healthQuery.data),
    [healthQuery.data]
  );

  const description =
    projects.find((p) => p.id === projectId)?.description ?? "";

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>현재 프로젝트</CardTitle>
          <CardDescription>
            작업 컨텍스트로 사용되는 프로젝트입니다.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-2 sm:max-w-sm">
            <label
              htmlFor="project-select"
              className="text-xs font-medium text-zinc-300"
            >
              프로젝트
            </label>
            <Select
              id="project-select"
              value={projectId}
              onChange={(e) => handleSwitch(e.target.value)}
              disabled={projectsQuery.isLoading || switchProject.isPending}
            >
              {projects.length === 0 ? (
                <option value="">프로젝트 없음</option>
              ) : (
                projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))
              )}
            </Select>
          </div>
          {description && <p className="text-xs text-zinc-400">{description}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>연결 상태</CardTitle>
          <CardDescription>
            Labs가 의존하는 외부 서비스의 헬스 상태입니다 (60초 polling).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {healthQuery.isLoading && entries.length === 0 ? (
            <div className="space-y-2">
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="h-10 animate-pulse rounded bg-zinc-900/50" />
              ))}
            </div>
          ) : entries.length === 0 ? (
            <p className="text-xs text-zinc-500">헬스 정보를 가져올 수 없습니다.</p>
          ) : (
            <ul className="divide-y divide-zinc-800 rounded-md border border-zinc-800">
              {entries.map((it) => (
                <li
                  key={it.key}
                  className="flex items-center justify-between px-4 py-3"
                >
                  <div className="flex flex-col">
                    <span className="text-sm text-zinc-100">{it.label}</span>
                    <span className="text-xs text-zinc-500">
                      마지막 헬스체크: {formatLastCheck(it.checkedAt ?? latestCheck)}
                      {typeof it.responseTimeMs === "number" && (
                        <>
                          {" · "}
                          <span className="font-mono">{it.responseTimeMs}ms</span>
                        </>
                      )}
                    </span>
                  </div>
                  <HealthDot state={it.state} />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>사용자 정보</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3">
            <span
              aria-hidden
              className="grid h-10 w-10 place-items-center rounded-full bg-indigo-500 text-sm font-semibold text-white"
            >
              {user?.name?.charAt(0) ?? "?"}
            </span>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-zinc-100">
                  {user?.name ?? "—"}
                </span>
                {user?.role && <Badge tone="accent">{user.role}</Badge>}
              </div>
              <p className="mt-0.5 text-xs text-zinc-400">
                {user?.email ?? "—"}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
