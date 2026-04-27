"use client";

import { Play, Save, Square } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { useAuth } from "@/lib/auth";
import { useModelList } from "@/lib/hooks/useModels";
import {
  usePromptDetail,
  usePromptList,
  usePromptVersions,
} from "@/lib/hooks/usePrompts";
import { useSingleTestRun } from "@/lib/hooks/useExperiments";
import type {
  ChatMessage,
  ModelInfo,
  PromptDetail,
  SingleTestRequest,
} from "@/lib/types/api";
import { cn } from "@/lib/utils";
import { CollapsiblePanel } from "./_components/CollapsiblePanel";
import { ImageAttachmentPanel } from "./_components/ImageAttachmentPanel";
import { ModelSelector } from "./_components/ModelSelector";
import {
  DEFAULT_PARAMETERS,
  ParameterPanel,
  type ModelParameters,
} from "./_components/ParameterPanel";
import { PromptEditor, extractVariables } from "./_components/PromptEditor";
import { ResponseStream, type StreamStatus } from "./_components/ResponseStream";
import { RunHistory } from "./_components/RunHistory";
import { VariableForm, type VariableFormHandle } from "./_components/VariableForm";
import type {
  MockResponseMeta,
  RunHistoryEntry,
} from "./_components/mockResponse";

const HISTORY_LIMIT = 5;
const DEFAULT_PROJECT_ID = "production-api";

interface StreamState {
  status: StreamStatus;
  text: string;
  meta: MockResponseMeta | null;
  modelName: string;
  promptName?: string;
  traceId?: string | null;
  errorMessage?: string | null;
  scores?: Record<string, number | null> | null;
}

type StreamAction =
  | { type: "start"; modelName: string; promptName: string }
  | { type: "complete"; meta: MockResponseMeta; output: string; traceId: string; scores: Record<string, number | null> | null }
  | { type: "stop" }
  | { type: "error"; message: string }
  | { type: "replay"; entry: RunHistoryEntry };

function streamReducer(state: StreamState, action: StreamAction): StreamState {
  switch (action.type) {
    case "start":
      return {
        status: "streaming",
        text: "",
        meta: null,
        modelName: action.modelName,
        promptName: action.promptName,
        traceId: null,
        errorMessage: null,
        scores: null,
      };
    case "complete":
      return {
        ...state,
        status: "completed",
        text: action.output,
        meta: action.meta,
        traceId: action.traceId,
        scores: action.scores,
      };
    case "stop":
      return { ...state, status: "stopped" };
    case "error":
      return { ...state, status: "stopped", errorMessage: action.message };
    case "replay":
      return {
        status: "completed",
        text: action.entry.response,
        meta: action.entry.meta,
        modelName: action.entry.modelName,
        promptName: action.entry.promptName,
        traceId: null,
        errorMessage: null,
        scores: null,
      };
    default:
      return state;
  }
}

function extractBodyText(detail: PromptDetail | undefined): string {
  if (!detail) return "";
  if (typeof detail.prompt === "string") return detail.prompt;
  // chat format → join user-content
  return (detail.prompt as ChatMessage[])
    .map((m) => `[${m.role}] ${m.content}`)
    .join("\n\n");
}

function extractSystemPrompt(detail: PromptDetail | undefined): string {
  if (!detail) return "";
  const sys = (detail.config as Record<string, unknown>)?.system_prompt;
  return typeof sys === "string" ? sys : "";
}

export default function PlaygroundPage() {
  // mock 모드는 auth.tsx가 자동으로 admin 토큰 주입
  // user 객체에 currentProjectId가 없으므로 default 사용
  useAuth();
  const projectId = DEFAULT_PROJECT_ID;

  // ── Remote: prompt list, model list ─────────────────────────────────
  const { data: promptListResp } = usePromptList(projectId);
  const prompts = useMemo(
    () => promptListResp?.items ?? [],
    [promptListResp]
  );

  const { data: modelListResp } = useModelList();
  const flatModels = useMemo<ModelInfo[]>(
    () => modelListResp?.models ?? [],
    [modelListResp]
  );

  // ── Selection state ─────────────────────────────────────────────────
  const [promptName, setPromptName] = useState<string>("");
  const [promptVersion, setPromptVersion] = useState<number | null>(null);
  const [modelId, setModelId] = useState<string>("");

  // Initialize selection once data arrives
  useEffect(() => {
    if (prompts.length > 0 && !promptName) {
      const first = prompts[0];
      setPromptName(first.name);
      setPromptVersion(first.latest_version);
    }
  }, [prompts, promptName]);

  useEffect(() => {
    if (flatModels.length > 0 && !modelId) {
      setModelId(flatModels[0].id);
    }
  }, [flatModels, modelId]);

  // ── Remote: prompt detail (versioned), versions list ────────────────
  const { data: promptDetail } = usePromptDetail(
    projectId,
    promptName || null,
    promptVersion ?? undefined
  );
  const { data: versionsResp } = usePromptVersions(
    projectId,
    promptName || null
  );
  const promptVersionsList = versionsResp?.versions ?? [];

  // ── Editor state (mirrors prompt detail) ────────────────────────────
  const [systemPrompt, setSystemPrompt] = useState<string>("");
  const [body, setBody] = useState<string>("");
  const [detectedVars, setDetectedVars] = useState<string[]>([]);
  const [varValues, setVarValues] = useState<Record<string, string>>({});
  const [varErrors, setVarErrors] = useState<Set<string>>(new Set());

  // Sync editor state when prompt detail loads
  useEffect(() => {
    if (!promptDetail) return;
    const nextBody = extractBodyText(promptDetail);
    const nextSys = extractSystemPrompt(promptDetail);
    setSystemPrompt(nextSys);
    setBody(nextBody);
    setDetectedVars(promptDetail.variables);
    setVarValues((prev) => {
      const next: Record<string, string> = {};
      promptDetail.variables.forEach((name) => {
        next[name] = prev[name] ?? "";
      });
      return next;
    });
    setVarErrors(new Set());
  }, [promptDetail]);

  // ── Model parameters ────────────────────────────────────────────────
  const [parameters, setParameters] = useState<ModelParameters>(
    DEFAULT_PARAMETERS
  );

  // ── Stream state ────────────────────────────────────────────────────
  const [stream, dispatch] = useReducer(streamReducer, {
    status: "idle" as StreamStatus,
    text: "",
    meta: null,
    modelName: "",
    promptName: undefined,
  });

  // ── History ─────────────────────────────────────────────────────────
  const [history, setHistory] = useState<RunHistoryEntry[]>([]);

  // ── Refs for streaming control ──────────────────────────────────────
  const abortRef = useRef<AbortController | null>(null);
  const variableFormRef = useRef<VariableFormHandle | null>(null);

  // ── Single test mutation ────────────────────────────────────────────
  const singleTest = useSingleTestRun();

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  // ── Handlers ────────────────────────────────────────────────────────
  const handlePromptChange = useCallback(
    (name: string) => {
      const p = prompts.find((x) => x.name === name);
      setPromptName(name);
      setPromptVersion(p?.latest_version ?? null);
    },
    [prompts]
  );

  const handleVersionChange = useCallback((version: number) => {
    setPromptVersion(version);
  }, []);

  const handleBodyChange = useCallback((next: string) => {
    setBody(next);
  }, []);

  const handleVariablesDetected = useCallback((vars: string[]) => {
    setDetectedVars(vars);
    setVarValues((prev) => {
      const next: Record<string, string> = {};
      vars.forEach((name) => {
        next[name] = prev[name] ?? "";
      });
      return next;
    });
    setVarErrors((prev) => {
      const next = new Set<string>();
      prev.forEach((name) => {
        if (vars.includes(name)) next.add(name);
      });
      return next;
    });
  }, []);

  const handleVariableValueChange = useCallback(
    (name: string, value: string) => {
      setVarValues((prev) => ({ ...prev, [name]: value }));
      setVarErrors((prev) => {
        if (!prev.has(name)) return prev;
        const next = new Set(prev);
        next.delete(name);
        return next;
      });
    },
    []
  );

  const handleVariableChipClick = useCallback((name: string) => {
    variableFormRef.current?.focusVariable(name);
  }, []);

  const startRun = useCallback(async () => {
    if (!promptDetail || !modelId) return;
    const model = flatModels.find((m) => m.id === modelId);
    if (!model) return;

    // Validate variables
    const currentVars = extractVariables(body);
    const missing = new Set<string>();
    currentVars.forEach((name) => {
      const v = varValues[name];
      if (v === undefined || v.trim().length === 0) missing.add(name);
    });
    if (missing.size > 0) {
      setVarErrors(missing);
      const first = currentVars.find((n) => missing.has(n));
      if (first) variableFormRef.current?.focusVariable(first);
      return;
    }

    dispatch({
      type: "start",
      modelName: model.name,
      promptName: `${promptDetail.name} v${promptDetail.version}`,
    });

    const payload: SingleTestRequest = {
      project_id: projectId,
      prompt: {
        source: "langfuse",
        name: promptDetail.name,
        version: promptDetail.version,
      },
      variables: varValues,
      model: modelId,
      parameters: {
        temperature: parameters.temperature,
        top_p: parameters.topP,
        max_tokens: parameters.maxTokens,
      },
      stream: false,
    };

    try {
      const result = await singleTest.mutateAsync({ payload });
      const meta: MockResponseMeta = {
        latencyMs: result.latency_ms ?? 0,
        inputTokens: result.usage?.input_tokens ?? 0,
        outputTokens: result.usage?.output_tokens ?? 0,
        costUsd: result.cost_usd ?? 0,
      };
      const scores: Record<string, number | null> | null =
        result.scores && Object.keys(result.scores).length > 0
          ? Object.fromEntries(
              Object.entries(result.scores).map(([k, v]) => [
                k,
                typeof v === "number" ? v : null,
              ])
            )
          : null;

      dispatch({
        type: "complete",
        meta,
        output: result.output,
        traceId: result.trace_id,
        scores,
      });

      const entry: RunHistoryEntry = {
        id: `run-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        promptId: promptDetail.name,
        promptName: promptDetail.name,
        promptVersion: promptDetail.version,
        modelId: model.id,
        modelName: model.name,
        modelProvider: model.provider,
        variables: { ...varValues },
        response: result.output,
        partial: false,
        meta,
        createdAt: new Date().toISOString(),
        score: 0,
      };
      setHistory((prev) => [entry, ...prev].slice(0, HISTORY_LIMIT));
    } catch (err) {
      dispatch({
        type: "error",
        message: err instanceof Error ? err.message : "stream error",
      });
    }
  }, [
    promptDetail,
    modelId,
    flatModels,
    body,
    varValues,
    parameters,
    projectId,
    singleTest,
  ]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    dispatch({ type: "stop" });
  }, []);

  // Cmd/Ctrl + Enter shortcut
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (stream.status === "streaming") return;
        startRun();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [startRun, stream.status]);

  const handleReplay = useCallback((entry: RunHistoryEntry) => {
    dispatch({ type: "replay", entry });
  }, []);

  const isStreaming =
    stream.status === "streaming" || singleTest.isPending;

  const currentPromptSummary = prompts.find((p) => p.name === promptName);

  if (!promptDetail || flatModels.length === 0) {
    return (
      <div className="px-6 pb-10 pt-6">
        <PageHeader
          title="단일 테스트 (Playground)"
          description="프롬프트를 빠르게 시도하고 응답·비용·지연을 비교합니다."
        />
        <EmptyState
          title="프롬프트와 모델 정보를 불러오는 중…"
          description="잠시만 기다려 주세요."
        />
      </div>
    );
  }

  return (
    <div className="px-6 pb-10 pt-6">
      <PageHeader
        title="단일 테스트 (Playground)"
        description="프롬프트를 빠르게 시도하고 응답·비용·지연을 비교합니다."
        actions={
          <Button variant="secondary" size="md" disabled>
            <Save className="h-4 w-4" aria-hidden />
            프롬프트로 저장
          </Button>
        }
      />

      <div
        className="grid gap-4"
        style={{
          gridTemplateColumns: "minmax(480px, 45fr) minmax(540px, 55fr)",
        }}
      >
        {/* LEFT — Settings panel */}
        <section
          aria-label="프롬프트 설정"
          className="flex max-h-[calc(100dvh-9rem)] flex-col gap-3 overflow-y-auto pr-1"
        >
          <Card>
            <CardHeader className="flex items-center justify-between gap-3 py-2">
              <CardTitle>프롬프트 선택</CardTitle>
              <Badge tone="muted">
                {currentPromptSummary?.labels?.[0] ?? "draft"}
              </Badge>
            </CardHeader>
            <CardContent className="grid grid-cols-[1fr_120px] gap-2 pt-3">
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="prompt-name-select"
                  className="text-[11px] font-medium uppercase tracking-wide text-zinc-500"
                >
                  이름
                </label>
                <Select
                  id="prompt-name-select"
                  value={promptName}
                  onChange={(e) => handlePromptChange(e.target.value)}
                >
                  {prompts.map((p) => (
                    <option key={p.name} value={p.name}>
                      {p.name}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="prompt-version-select"
                  className="text-[11px] font-medium uppercase tracking-wide text-zinc-500"
                >
                  버전
                </label>
                <Select
                  id="prompt-version-select"
                  value={String(promptVersion ?? "")}
                  onChange={(e) => handleVersionChange(Number(e.target.value))}
                >
                  {promptVersionsList.map((v) => (
                    <option key={v.version} value={v.version}>
                      v{v.version}
                      {v.version === currentPromptSummary?.latest_version
                        ? " (latest)"
                        : ""}
                    </option>
                  ))}
                  {promptVersionsList.length === 0 && (
                    <option value={String(promptDetail.version)}>
                      v{promptDetail.version}
                    </option>
                  )}
                </Select>
              </div>
            </CardContent>
          </Card>

          <CollapsiblePanel
            title="System Prompt"
            description={systemPrompt ? `${systemPrompt.length}자` : "비어있음"}
            defaultOpen={Boolean(systemPrompt)}
          >
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={3}
              spellCheck={false}
              placeholder="모델의 역할과 톤을 설정하는 system 메시지"
              className={cn(
                "w-full resize-y rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2",
                "font-mono text-[13px] leading-relaxed text-zinc-100 placeholder:text-zinc-500",
                "focus-visible:border-indigo-400 focus-visible:outline-none"
              )}
              aria-label="System prompt"
            />
          </CollapsiblePanel>

          <Card>
            <CardContent className="pt-4">
              <PromptEditor
                value={body}
                onChange={handleBodyChange}
                onVariablesChange={handleVariablesDetected}
                onVariableClick={handleVariableChipClick}
                onRun={startRun}
                detectedVariables={detectedVars}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="py-2">
              <CardTitle>변수 입력</CardTitle>
            </CardHeader>
            <CardContent className="pt-3">
              <VariableForm
                ref={variableFormRef}
                variables={detectedVars}
                values={varValues}
                onChange={handleVariableValueChange}
                errors={varErrors}
              />
            </CardContent>
          </Card>

          <CollapsiblePanel
            title="파라미터"
            description={`temp ${parameters.temperature.toFixed(2)} · top_p ${parameters.topP.toFixed(2)} · max ${parameters.maxTokens}`}
          >
            <ParameterPanel value={parameters} onChange={setParameters} />
          </CollapsiblePanel>

          <CollapsiblePanel
            title="이미지 첨부"
            description="vision 모델 전용 (mock)"
          >
            <ImageAttachmentPanel />
          </CollapsiblePanel>
        </section>

        {/* RIGHT — Result panel */}
        <section
          aria-label="실행 결과"
          className="flex max-h-[calc(100dvh-9rem)] flex-col gap-3 overflow-y-auto"
        >
          <div
            className={cn(
              "sticky top-0 z-10 -mx-1 flex items-center justify-between gap-3 px-1",
              "rounded-md border border-zinc-800 bg-zinc-950/85 px-3 py-2 backdrop-blur"
            )}
          >
            <div className="flex items-center gap-2">
              {isStreaming ? (
                <Button
                  variant="destructive"
                  size="md"
                  onClick={handleStop}
                  aria-label="실행 중단"
                >
                  <Square className="h-4 w-4" aria-hidden />
                  중단
                </Button>
              ) : (
                <Button
                  variant="primary"
                  size="md"
                  onClick={startRun}
                  aria-label="실행"
                >
                  <Play className="h-4 w-4" aria-hidden />
                  실행
                </Button>
              )}
              <span className="hidden text-[11px] text-zinc-500 sm:inline">
                <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1 py-0.5 font-mono text-[10px]">
                  ⌘
                </kbd>
                +
                <kbd className="rounded border border-zinc-700 bg-zinc-800 px-1 py-0.5 font-mono text-[10px]">
                  Enter
                </kbd>
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-zinc-500">모델</span>
              <ModelSelector
                value={modelId}
                onChange={setModelId}
                models={flatModels}
              />
            </div>
          </div>

          <Card>
            <CardContent className="pt-4">
              <ResponseStream
                status={stream.status}
                text={stream.text}
                meta={stream.meta}
                modelName={stream.modelName}
                promptName={stream.promptName}
                errorMessage={stream.errorMessage}
                scores={stream.scores ?? null}
                traceId={stream.traceId ?? null}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex items-center justify-between gap-2 py-2">
              <CardTitle>이전 실행 (최근 {HISTORY_LIMIT}건)</CardTitle>
              {history.length > 0 && (
                <Badge tone="muted">{history.length}개</Badge>
              )}
            </CardHeader>
            <CardContent className="pt-3">
              <RunHistory entries={history} onReplay={handleReplay} />
            </CardContent>
          </Card>
        </section>
      </div>
    </div>
  );
}
