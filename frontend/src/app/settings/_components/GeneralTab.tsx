"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { Badge } from "@/components/ui/Badge";
import { HealthDot } from "@/components/ui/StatusDot";
import {
  connectionHealth,
  currentProject,
  currentUser,
  projects,
} from "@/lib/mock/data";

const HEALTH_ITEMS: {
  key: keyof typeof connectionHealth;
  label: string;
  lastCheck: string;
}[] = [
  { key: "langfuse", label: "Langfuse", lastCheck: "2분 전" },
  { key: "litellm", label: "LiteLLM", lastCheck: "1분 전" },
  { key: "clickhouse", label: "ClickHouse", lastCheck: "3분 전" },
  { key: "redis", label: "Redis", lastCheck: "방금" },
];

export function GeneralTab() {
  const [projectId, setProjectId] = useState(currentProject.id);
  const project = projects.find((p) => p.id === projectId) ?? currentProject;

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
              onChange={(e) => setProjectId(e.target.value)}
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </div>
          <p className="text-xs text-zinc-400">{project.description}</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>연결 상태</CardTitle>
          <CardDescription>
            Labs가 의존하는 외부 서비스의 상태입니다.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-zinc-800 rounded-md border border-zinc-800">
            {HEALTH_ITEMS.map((it) => (
              <li
                key={it.key}
                className="flex items-center justify-between px-4 py-3"
              >
                <div className="flex flex-col">
                  <span className="text-sm text-zinc-100">{it.label}</span>
                  <span className="text-xs text-zinc-500">
                    마지막 헬스체크: {it.lastCheck}
                  </span>
                </div>
                <HealthDot state={connectionHealth[it.key]} />
              </li>
            ))}
          </ul>
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
              {currentUser.initials}
            </span>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-zinc-100">
                  {currentUser.name}
                </span>
                <Badge tone="accent">{currentUser.role}</Badge>
              </div>
              <p className="mt-0.5 text-xs text-zinc-400">
                {currentUser.email}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
