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
import { PageHeader } from "@/components/ui/PageHeader";
import { Select } from "@/components/ui/Select";
import { models, prompts } from "@/lib/mock/data";
import type { Model, Prompt, PromptVersion } from "@/lib/mock/types";
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
import {
  generateMockMeta,
  generateMockScore,
  pickMockResponse,
  randomTypingIntervalMs,
  type MockResponseMeta,
  type RunHistoryEntry,
} from "./_components/mockResponse";

const HISTORY_LIMIT = 5;

interface StreamState {
  status: StreamStatus;
  text: string;
  meta: MockResponseMeta | null;
  modelName: string;
  promptName?: string;
}

type StreamAction =
  | { type: "start"; modelName: string; promptName: string }
  | { type: "appendChar"; char: string }
  | { type: "complete"; meta: MockResponseMeta }
  | { type: "stop" }
  | {
      type: "replay";
      entry: RunHistoryEntry;
    };

function streamReducer(state: StreamState, action: StreamAction): StreamState {
  switch (action.type) {
    case "start":
      return {
        status: "streaming",
        text: "",
        meta: null,
        modelName: action.modelName,
        promptName: action.promptName,
      };
    case "appendChar":
      return { ...state, text: state.text + action.char };
    case "complete":
      return { ...state, status: "completed", meta: action.meta };
    case "stop":
      return { ...state, status: "stopped" };
    case "replay":
      return {
        status: "completed",
        text: action.entry.response,
        meta: action.entry.meta,
        modelName: action.entry.modelName,
        promptName: action.entry.promptName,
      };
    default:
      return state;
  }
}

function findPrompt(id: string): Prompt {
  return prompts.find((p) => p.id === id) ?? prompts[0];
}

function findVersion(p: Prompt, version: number): PromptVersion {
  return (
    p.versions.find((v) => v.version === version) ?? p.versions[0]
  );
}

function findModel(id: string): Model {
  return models.find((m) => m.id === id) ?? models[0];
}

export default function PlaygroundPage() {
  // Selection state
  const [promptId, setPromptId] = useState<string>(prompts[0].id);
  const [promptVersion, setPromptVersion] = useState<number>(
    prompts[0].latestVersion
  );
  const [modelId, setModelId] = useState<string>(models[0].id);

  // Editor state
  const initialVersion = useMemo(
    () => findVersion(findPrompt(prompts[0].id), prompts[0].latestVersion),
    []
  );
  const [systemPrompt, setSystemPrompt] = useState<string>(
    initialVersion.systemPrompt ?? ""
  );
  const [body, setBody] = useState<string>(initialVersion.body);
  const [detectedVars, setDetectedVars] = useState<string[]>(
    initialVersion.variables
  );
  const [varValues, setVarValues] = useState<Record<string, string>>({});
  const [varErrors, setVarErrors] = useState<Set<string>>(new Set());

  // Model parameters
  const [parameters, setParameters] = useState<ModelParameters>(DEFAULT_PARAMETERS);

  // Stream state
  const [stream, dispatch] = useReducer(streamReducer, {
    status: "idle" as StreamStatus,
    text: "",
    meta: null,
    modelName: findModel(models[0].id).name,
    promptName: prompts[0].name,
  });

  // History
  const [history, setHistory] = useState<RunHistoryEntry[]>([]);

  // Refs for streaming control + variable focus
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fullResponseRef = useRef<string>("");
  const streamedRef = useRef<string>("");
  const variableFormRef = useRef<VariableFormHandle | null>(null);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  // Load prompt body when prompt id changes (and reset to its latest version)
  const handlePromptChange = useCallback((id: string) => {
    const p = findPrompt(id);
    const v = findVersion(p, p.latestVersion);
    setPromptId(id);
    setPromptVersion(v.version);
    setSystemPrompt(v.systemPrompt ?? "");
    setBody(v.body);
    setDetectedVars(v.variables);
    // preserve previously typed variable values for variables that still exist
    setVarValues((prev) => {
      const next: Record<string, string> = {};
      v.variables.forEach((name) => {
        next[name] = prev[name] ?? "";
      });
      return next;
    });
    setVarErrors(new Set());
  }, []);

  const handleVersionChange = useCallback(
    (version: number) => {
      const p = findPrompt(promptId);
      const v = findVersion(p, version);
      setPromptVersion(v.version);
      setSystemPrompt(v.systemPrompt ?? "");
      setBody(v.body);
      setDetectedVars(v.variables);
      setVarValues((prev) => {
        const next: Record<string, string> = {};
        v.variables.forEach((name) => {
          next[name] = prev[name] ?? "";
        });
        return next;
      });
      setVarErrors(new Set());
    },
    [promptId]
  );

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

  const handleVariableValueChange = useCallback((name: string, value: string) => {
    setVarValues((prev) => ({ ...prev, [name]: value }));
    setVarErrors((prev) => {
      if (!prev.has(name)) return prev;
      const next = new Set(prev);
      next.delete(name);
      return next;
    });
  }, []);

  const handleVariableChipClick = useCallback((name: string) => {
    variableFormRef.current?.focusVariable(name);
  }, []);

  const stopStreaming = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  const startRun = useCallback(() => {
    // Validate variables (must use detectedVars from current body — re-extract to be safe)
    const currentVars = extractVariables(body);
    const missing = new Set<string>();
    currentVars.forEach((name) => {
      const v = varValues[name];
      if (v === undefined || v.trim().length === 0) missing.add(name);
    });
    if (missing.size > 0) {
      setVarErrors(missing);
      // focus first missing variable
      const first = currentVars.find((n) => missing.has(n));
      if (first) variableFormRef.current?.focusVariable(first);
      return;
    }

    const prompt = findPrompt(promptId);
    const model = findModel(modelId);
    const fullResponse = pickMockResponse(prompt.name);
    fullResponseRef.current = fullResponse;
    streamedRef.current = "";

    const promptCharCount = systemPrompt.length + body.length;

    dispatch({
      type: "start",
      modelName: model.name,
      promptName: `${prompt.name} v${promptVersion}`,
    });

    let i = 0;
    const tick = () => {
      if (i >= fullResponse.length) {
        timeoutRef.current = null;
        const meta = generateMockMeta(model, promptCharCount, fullResponse.length);
        dispatch({ type: "complete", meta });

        const entry: RunHistoryEntry = {
          id: `run-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          promptId: prompt.id,
          promptName: prompt.name,
          promptVersion,
          modelId: model.id,
          modelName: model.name,
          modelProvider: model.provider,
          variables: { ...varValues },
          response: fullResponse,
          partial: false,
          meta,
          createdAt: new Date().toISOString(),
          score: generateMockScore(),
        };
        setHistory((prev) => [entry, ...prev].slice(0, HISTORY_LIMIT));
        return;
      }
      const ch = fullResponse[i];
      streamedRef.current += ch;
      dispatch({ type: "appendChar", char: ch });
      i += 1;
      // schedule next tick with a randomized delay for natural feel
      timeoutRef.current = setTimeout(tick, randomTypingIntervalMs());
    };

    timeoutRef.current = setTimeout(tick, randomTypingIntervalMs());
  }, [body, modelId, promptId, promptVersion, systemPrompt, varValues]);

  const handleStop = useCallback(() => {
    stopStreaming();
    const prompt = findPrompt(promptId);
    const model = findModel(modelId);
    const partialText = streamedRef.current;
    dispatch({ type: "stop" });
    const promptCharCount = systemPrompt.length + body.length;
    const meta = generateMockMeta(model, promptCharCount, Math.max(40, partialText.length));
    const entry: RunHistoryEntry = {
      id: `run-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      promptId: prompt.id,
      promptName: prompt.name,
      promptVersion,
      modelId: model.id,
      modelName: model.name,
      modelProvider: model.provider,
      variables: { ...varValues },
      response: partialText,
      partial: true,
      meta,
      createdAt: new Date().toISOString(),
      score: generateMockScore(),
    };
    setHistory((prev) => [entry, ...prev].slice(0, HISTORY_LIMIT));
  }, [
    body,
    modelId,
    promptId,
    promptVersion,
    stopStreaming,
    systemPrompt,
    varValues,
  ]);

  // Global Cmd/Ctrl + Enter shortcut
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
    stopStreaming();
    dispatch({ type: "replay", entry });
  }, [stopStreaming]);

  const currentPrompt = findPrompt(promptId);
  const isStreaming = stream.status === "streaming";

  return (
    <div className="px-6 pb-10 pt-6">
      <PageHeader
        title="단일 테스트 (Playground)"
        description="프롬프트를 빠르게 시도하고 응답·비용·지연을 비교합니다. 모든 응답은 mock 데이터입니다."
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
                {currentPrompt.labels[0] ?? "draft"}
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
                  value={promptId}
                  onChange={(e) => handlePromptChange(e.target.value)}
                >
                  {prompts.map((p) => (
                    <option key={p.id} value={p.id}>
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
                  value={String(promptVersion)}
                  onChange={(e) => handleVersionChange(Number(e.target.value))}
                >
                  {currentPrompt.versions.map((v) => (
                    <option key={v.version} value={v.version}>
                      v{v.version}
                      {v.version === currentPrompt.latestVersion ? " (latest)" : ""}
                    </option>
                  ))}
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
              <ModelSelector value={modelId} onChange={setModelId} />
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
