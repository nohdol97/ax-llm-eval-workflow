# Agent Trace Evaluation · Online Auto-Eval · Review Queue 설계

> 본 문서는 사내 production에서 운영되는 LLM agent의 품질을 **agent endpoint 호출 없이** Langfuse trace 데이터만으로 평가하고, 정기·이벤트 기반으로 자동 실행하며, 인간 검토 큐를 통해 자동 평가의 신뢰성을 보장하는 시스템의 통합 설계.
>
> 본 문서는 Phase 0~7(현재 768 backend tests + 49 frontend tests + 12 commits 푸시 완료) 위에 추가될 **Phase 8** 작업의 단일 진실 원본이다. 구현은 본 문서가 합의된 후 시작한다.

**문서 상태**: Draft (v1) · **작성일**: 2026-04-28 · **Owner**: _(TBD)_

**참조 (Canonical)**:
- [`BUILD_ORDER.md`](BUILD_ORDER.md) Phase 0~7 — 기존 인프라
- [`API_DESIGN.md`](API_DESIGN.md) §1.1 공통 규칙 / §7 분석 / §8 평가
- [`EVALUATION.md`](EVALUATION.md) §3 LLM Judge / §4 Custom Code / §5.4 weighted_score
- [`LANGFUSE.md`](LANGFUSE.md) §3 ClickHouse 직접 쿼리 + 폴백
- [`UI_UX_DESIGN.md`](UI_UX_DESIGN.md) §4 페이지 / §16 에러 매핑
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md) §1.5 Redis 키 / §2 Sandbox / §4.3 RBAC

---

## 0. 개요 (Overview)

### 0.1 목적

기존 시스템(Phase 0~7)은 다음을 지원한다:
- **Live 모드**: 골든 데이터셋 기반 LLM 호출 + evaluator 평가 (배포 전 회귀 검증)
- **단일 테스트 / 배치 실험**: prompt + model + dataset 조합 비교
- **거버넌스**: Custom Code Evaluator 제출-승인 워크플로우

본 Phase 8은 다음을 추가한다:
1. **Agent Trace Evaluation**: 사내 agent가 실행한 결과(Langfuse trace)를 endpoint 호출 없이 평가
2. **Online Auto-Eval**: 위 평가를 정기·이벤트 기반으로 자동 실행 + 회귀 감지
3. **Review Queue**: 자동 평가가 의심스러운 케이스를 인간이 검토하여 신뢰성 보장 + 골든셋 보강

### 0.2 핵심 아키텍처

```
[사내 Production Agent]
       │
       │ 실행 + Langfuse SDK 기록 (LLM call → generation, tool call → span)
       ▼
[사내 Langfuse]  ◄─────────────── trace 영속화
       │
       │ 본 프로젝트 Backend가 ClickHouse readonly로 조회
       ▼
[Phase 8-A: Trace Evaluation Engine]
       │  ├─ TraceFetcher (필터 → trace tree 로드)
       │  ├─ TraceEvaluator 카탈로그 (tool_called, no_error_spans 등)
       │  └─ 기존 EvaluationPipeline 재활용 (trace.output에 13 built-in + LLM Judge 적용)
       ▼
[Phase 8-B: Auto-Eval Scheduler]
       │  ├─ AutoEvalPolicy 엔티티 (필터 + evaluator + 스케줄)
       │  ├─ Worker (cron 또는 polling) → Trace Evaluation Engine 호출
       │  ├─ 결과 → Langfuse score (해당 trace_id에 기록)
       │  ├─ 시계열 추세 → Prometheus 메트릭
       │  └─ 회귀 감지 → 알림 (notification + Alertmanager)
       │       │
       │       └─ 임계 미달 / 의심 케이스
       ▼
[Phase 8-C: Review Queue]
       │  ├─ ReviewItem 엔티티 (자동 진입 / 수동 추가)
       │  ├─ Reviewer 페이지 (/review) — 큐 + 상세 + 결정 폼
       │  └─ 결정 결과:
       │       ├─ Approve → 자동 score 확정
       │       ├─ Override → 수동 score로 Langfuse 갱신
       │       ├─ Dismiss → 큐에서 제거
       │       └─ Add to dataset → 골든셋에 추가 (Live 회귀 검증용)
       │              │
       │              └─ 다음 회귀 검증 / Auto-Eval rubric 학습
       ▼
[피드백 루프]
```

### 0.3 핵심 가치

| 항목 | 효과 |
|---|---|
| Agent endpoint 호출 0번 | 사내 agent 운영팀과의 통합 협상 불필요 |
| Production 트래픽 기반 평가 | synthetic 골든셋의 한계(분포 편향) 극복 |
| 자동 정기 실행 | 사람 개입 없이 매일 회귀 모니터링 |
| 인간 검토 보장 | LLM Judge 오평가 / 엣지케이스 보정 |
| 골든셋 자동 보강 | reviewer 결정이 다음 Live 회귀 데이터셋으로 환원 |

### 0.4 비-목표 (out of scope, v1)

- Agent endpoint 직접 호출 (이전 논의의 "Live mode for agents", "Replay mode") — Phase 9 후보
- Trace의 generation에 대한 prompt 수정 후 재실행 — Phase 9 (Replay)
- 프롬프트 자동 개선 (auto-tuning) — 별도 RFC
- 다중 시스템 trace 통합 (예: Datadog + Langfuse) — Phase 10+

---

## 1. 기존 구조와의 연속성 [Spec]

### 1.1 재활용 컴포넌트

| 기존 컴포넌트 | 재활용 방식 |
|---|---|
| `app/services/clickhouse_client.py` (Phase 6) | TraceFetcher가 직접 사용 — 이미 readonly 강제, parameterized query, LIMIT 강제 |
| `app/services/langfuse_client.py` (Phase 2) | trace 단건 조회 (`/api/public/traces/{id}`), score 기록 (Auto-Eval 결과), public API 폴백 |
| `app/evaluators/pipeline.py` (Phase 5) | EvaluationPipeline.evaluate_item을 trace.output에도 적용 (13 built-in + LLM Judge 그대로) |
| `app/services/batch_runner.py` (Phase 4) | mode 분기에 `trace_eval` 추가 — 같은 evaluator pipeline 호출, 입력 source만 다름 |
| `app/services/redis_client.py` (Phase 2) | AutoEvalPolicy / ReviewItem 영속화 (Hash + ZSet 인덱스) |
| `app/services/notification_service.py` (Phase 3) | 회귀 감지 알림, review queue 할당 알림 |
| `app/core/security.py` (Phase 2) | Reviewer RBAC (`reviewer` role 신규 또는 `admin` 활용) |
| `app/core/observability.py` (Phase 2) | Auto-Eval 메트릭 (`ax_auto_eval_*`), trace evaluator 실행 trace |

### 1.2 신규 컴포넌트

| 신규 모듈 | 책임 |
|---|---|
| `app/services/trace_fetcher.py` | Langfuse ClickHouse/public API에서 trace tree 조회 |
| `app/models/trace.py` | TraceTree, TraceObservation, TraceFilter Pydantic 모델 |
| `app/evaluators/trace_base.py` | TraceEvaluator Protocol + Adapter (기존 evaluator → trace.output) |
| `app/evaluators/trace_built_in.py` | 신규 trace evaluator 8~10종 |
| `app/services/auto_eval_engine.py` | AutoEvalPolicy 실행 엔진 (스케줄러 + worker) |
| `app/services/auto_eval_scheduler.py` | APScheduler 또는 asyncio cron worker |
| `app/models/auto_eval.py` | AutoEvalPolicy, AutoEvalRun, RegressionAlert 모델 |
| `app/api/v1/traces.py` | trace 검색·조회 API |
| `app/api/v1/auto_eval.py` | 정책 CRUD + manual run + run history |
| `app/services/review_queue.py` | ReviewItem 큐 관리 |
| `app/models/review.py` | ReviewItem, ReviewDecision, ReviewerStats 모델 |
| `app/api/v1/reviews.py` | 큐 listing + 결정 + 통계 |

### 1.3 신규 Frontend 페이지

| 페이지 | 경로 | 설명 |
|---|---|---|
| Auto-Eval Policy 목록/생성/상세 | `/auto-eval` | 정책 CRUD, 실행 이력, 시계열 추이 차트 |
| Review Queue | `/review` | 큐 listing, 상세 검토 폼, 통계 대시보드 |
| Trace Explorer (선택) | `/traces` | trace 검색·뷰어 (Langfuse 외부 링크 fallback) |
| 기존 페이지 확장 | `/experiments/new` | mode 선택에 `trace_eval` 추가 |

---

## 2. 용어집 (Glossary)

| 용어 | 정의 |
|---|---|
| **Trace Evaluation** | Langfuse에 기록된 trace를 입력으로 받아 evaluator를 실행하는 평가 모드. agent 재실행 X. |
| **Online Auto-Eval** | 정기 또는 이벤트 기반으로 trace evaluation을 자동 실행하는 시스템. |
| **AutoEvalPolicy** | 자동 평가 정책 단위. 트레이스 필터 + evaluator + 스케줄 + 알림 임계값을 묶음. |
| **AutoEvalRun** | 정책 1회 실행 결과. 평가된 trace 수, 통과율, 비용, 시작/종료 시각. |
| **Review Queue** | 자동 평가가 의심스러운 trace 또는 결과를 인간이 검토하는 작업 큐. |
| **ReviewItem** | 큐의 한 항목. 평가 대상 trace + 자동 score + 진입 사유. |
| **Reviewer** | 큐 항목을 검토하는 인간. RBAC role `reviewer` 또는 `admin`. |
| **Regression Alert** | Auto-Eval 결과에서 점수가 임계값 이상 하락 시 발행되는 알림. |
| **Trace Evaluator** | trace tree 전체를 입력받는 evaluator (기존 evaluator는 output만 입력). |

---

## Part I. Agent Trace Evaluation

### 3. 평가 가능한 행동 항목 [Spec]

agent trace에는 다음 정보가 들어있다 (agent가 Langfuse SDK로 instrumentation했다는 전제):

```
trace
├── name: "qa-agent-v3"
├── input: 사용자 입력
├── output: 최종 응답
├── tags, user_id, session_id, metadata
├── observations (자식 spans, 시간순)
│   ├── span: "retrieve_context" (tool 호출)
│   │   ├── input, output (raw args/result)
│   │   ├── level (DEBUG / DEFAULT / WARNING / ERROR)
│   │   ├── status_message
│   │   └── start_time, end_time, latency
│   ├── generation: "llm_call_1"
│   │   ├── model, messages, completion
│   │   └── usage, cost_usd
│   └── ... (더 많은 spans/generations)
└── total_cost, total_latency
```

### 3.1 평가 차원

| 차원 | 검증 대상 | 예시 evaluator |
|---|---|---|
| **Tool 사용 정확성** | 어떤 tool을 호출했는지 | `tool_called`, `tool_called_with_args`, `tool_call_sequence` |
| **Tool 효율성** | 너무 많이/적게 호출하지 않았는지 | `tool_call_count_in_range`, `agent_loop_bounded` |
| **에러 회복** | 실패 span이 있는지, 회복했는지 | `no_error_spans`, `error_recovery_attempted` |
| **응답 정확성** | 최종 output이 맞는지 | 기존 13 built-in (`exact_match`, `llm_judge_factuality` 등) |
| **근거성** | output이 tool 결과에 기반하는지 | `tool_result_grounding` (LLM Judge 기반) |
| **할루시네이션** | tool 결과에 없는 fact를 만들지 않았는지 | `hallucination_check` (LLM Judge) |
| **비용/지연** | SLO 초과 여부 | `latency_check`, `cost_check`, `latency_breakdown` |

### 4. 데이터 모델 [Spec]

#### 4.1 `app/models/trace.py`

```python
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

ObservationType = Literal["span", "generation", "event"]
ObservationLevel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]

class TraceObservation(BaseModel):
    """trace의 단일 observation (span/generation/event)."""
    id: str
    type: ObservationType
    name: str
    parent_observation_id: str | None = None
    input: dict[str, Any] | str | None = None
    output: dict[str, Any] | str | None = None
    level: ObservationLevel = "DEFAULT"
    status_message: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    latency_ms: float | None = None
    # generation only
    model: str | None = None
    usage: dict[str, int] | None = None
    cost_usd: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class TraceTree(BaseModel):
    """trace + 모든 observations + 연결된 score."""
    id: str
    project_id: str
    name: str  # agent 식별자
    input: dict | str | None = None
    output: dict | str | None = None
    user_id: str | None = None
    session_id: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = Field(default_factory=dict)
    observations: list[TraceObservation]  # start_time asc
    scores: list[dict] = []  # 기존에 부착된 score (있다면)
    total_cost_usd: float = 0.0
    total_latency_ms: float | None = None
    timestamp: datetime
    
    def find_observations(
        self, name: str | None = None, type: ObservationType | None = None
    ) -> list[TraceObservation]:
        """이름/타입 필터로 observation 검색."""
        ...
    
    def tool_calls(self) -> list[TraceObservation]:
        """type=span 인 observation만 (tool 호출로 간주)."""
        return [o for o in self.observations if o.type == "span"]
    
    def llm_calls(self) -> list[TraceObservation]:
        return [o for o in self.observations if o.type == "generation"]

class TraceFilter(BaseModel):
    """trace 검색 필터."""
    project_id: str
    name: str | None = None  # agent 이름
    tags: list[str] | None = None
    user_ids: list[str] | None = None
    session_ids: list[str] | None = None
    from_timestamp: datetime | None = None
    to_timestamp: datetime | None = None
    sample_size: int | None = None  # None이면 전체
    sample_strategy: Literal["random", "first", "stratified"] = "random"
    # 추가: 메타데이터 키-값 필터 (선택)
    metadata_match: dict[str, Any] | None = None
```

### 5. Trace Evaluator 카탈로그 [Spec]

#### 5.1 인터페이스 — `app/evaluators/trace_base.py`

```python
from typing import Protocol, Any
from app.models.trace import TraceTree
from app.evaluators.base import Evaluator  # 기존

class TraceEvaluator(Protocol):
    """trace tree 전체를 입력받는 evaluator."""
    name: str
    
    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        """0.0 ~ 1.0 점수 또는 None (평가 불가)."""
        ...

class OutputAdapter:
    """기존 Evaluator를 trace.output에 적용하는 어댑터."""
    def __init__(self, inner: Evaluator):
        self._inner = inner
        self.name = inner.name
    
    async def evaluate_trace(self, trace, expected, config):
        return await self._inner.evaluate(
            output=trace.output,
            expected=expected.get("expected_output") if expected else None,
            metadata={
                "latency_ms": trace.total_latency_ms,
                "cost_usd": trace.total_cost_usd,
                **trace.metadata,
            },
            **config,
        )
```

#### 5.2 신규 Trace Evaluator (v1) — `app/evaluators/trace_built_in.py`

| 이름 | 입력 (config) | 반환 | 설명 |
|---|---|---|---|
| `tool_called` | `{tool_name: str}` | 0/1 | 해당 이름의 span이 trace에 존재하는지 |
| `tool_called_with_args` | `{tool_name, args_match: dict[str, regex \| value]}` | 0/1 | tool 호출의 input이 패턴과 일치하는지 |
| `tool_call_sequence` | `{sequence: list[str], strict: bool=false}` | 0/1 | 정해진 순서대로 호출됐는지. strict=true면 정확 일치, false면 subsequence |
| `tool_call_count_in_range` | `{tool_name?: str, min: int, max: int}` | 0/1 | 호출 횟수가 [min, max] 범위인지. tool_name 없으면 전체 tool |
| `no_error_spans` | `{}` | 0/1 | level=ERROR span이 0개 |
| `error_recovery_attempted` | `{}` | 0/1 | error span 이후 재시도 (같은 tool 다시 호출) 발생 여부 |
| `agent_loop_bounded` | `{max_generations: int=10}` | 0/1 | generation 수가 임계 이하 |
| `latency_breakdown_healthy` | `{tool_max_ms?: int, llm_max_ms?: int}` | 0/1 | 단계별 지연이 합리적 |
| `tool_result_grounding` | `{judge_model: str="gpt-4o"}` | 0~1 | tool 결과 텍스트와 final output을 LLM Judge로 비교 (인용/근거성 평가) |
| `hallucination_check` | `{judge_model: str="gpt-4o"}` | 0~1 | tool 결과에 없는 fact가 output에 있는지 LLM Judge |

#### 5.3 expected behavior 정의

데이터셋 아이템 또는 골든셋의 `expected` 필드에 다음 구조 추가 가능:

```yaml
# 기존
expected_output: "..."

# 신규 (trace evaluator용)
expected_tool_calls:
  - tool: web_search
    args_match:
      query: ".+"  # regex
expected_tool_count:
  min: 1
  max: 3
expected_no_errors: true
```

### 6. Pipeline 통합 [Spec]

#### 6.1 EvaluationPipeline 확장

```python
# app/evaluators/pipeline.py 확장 (기존 메서드 보존)
class EvaluationPipeline:
    async def evaluate_trace(
        self,
        evaluators: list[EvaluatorConfig],
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> dict[str, float | None]:
        """trace 단위 평가 — trace evaluator + (어댑터로) 기존 evaluator 모두 실행."""
        # config.type 분기:
        #   "trace_builtin" → TraceEvaluator 직접 호출
        #   "builtin" → OutputAdapter로 감싸서 호출
        #   "judge" / "approved" → 기존 분기 유지
        # asyncio.gather 병렬, evaluator당 5초 timeout
        # weighted_score 자동 계산
        # Langfuse score(trace_id, name, value) 기록
```

#### 6.2 Experiment mode 확장

기존 `ExperimentCreate`에 mode 추가:

```python
class ExperimentCreate(BaseModel):
    mode: Literal["live", "trace_eval"] = "live"
    # live: 기존 — 데이터셋 아이템마다 LLM 호출
    # trace_eval: trace_filter로 trace fetch → evaluator만 적용
    
    # mode=live (기존)
    prompt_configs: list[PromptConfig] | None = None
    dataset_name: str | None = None
    model_configs: list[ModelConfig] | None = None
    
    # mode=trace_eval (신규)
    trace_filter: TraceFilter | None = None
    expected_dataset_name: str | None = None
    # expected가 골든셋에 있으면 매칭 (input 기준 또는 metadata.dataset_item_id로)
    
    evaluators: list[EvaluatorConfig]  # 양 모드 공통
    
    @model_validator(mode="after")
    def validate_mode_fields(self) -> Self:
        if self.mode == "live":
            assert self.prompt_configs and self.dataset_name and self.model_configs
        elif self.mode == "trace_eval":
            assert self.trace_filter
        return self
```

### 7. API 설계 [Spec]

#### 7.1 Trace API — `/api/v1/traces` (신규 라우터)

| 엔드포인트 | 동작 | 권한 |
|---|---|---|
| `POST /api/v1/traces/search` | TraceFilter로 trace 목록 (페이지네이션, 메타만) | viewer+ |
| `GET /api/v1/traces/{id}` | trace tree 단건 (모든 observations) | viewer+ |
| `POST /api/v1/traces/{id}/score` | trace에 score 추가 (수동 — review queue 결과 등) | user+, ETag/If-Match |

내부 동작:
- ClickHouse 직접 모드: 본 프로젝트 Phase 6의 ClickHouseClient 재사용 + `clickhouse_queries.py`에 trace 검색 쿼리 추가
- 폴백 모드: Langfuse `/api/public/traces?...` 프록시

#### 7.2 Experiment API 확장

기존 `POST /api/v1/experiments`에 mode/trace_filter 필드 추가 (5.6 위 모델 참조). `GET /api/v1/experiments/{id}`의 `config_snapshot`은 mode별로 다른 형태를 보존.

---

## Part II. Online Auto-Eval

### 8. AutoEvalPolicy 엔티티 [Spec]

#### 8.1 데이터 모델 — `app/models/auto_eval.py`

```python
from datetime import datetime, timedelta
from typing import Any, Literal
from pydantic import BaseModel, Field
from app.models.trace import TraceFilter
from app.models.evaluator import EvaluatorConfig

ScheduleType = Literal["cron", "interval", "event"]

class AutoEvalSchedule(BaseModel):
    type: ScheduleType
    # type="cron"
    cron_expression: str | None = None  # 예: "0 */1 * * *" (매시간)
    timezone: str = "Asia/Seoul"
    # type="interval"
    interval_seconds: int | None = None  # 예: 3600
    # type="event"
    event_trigger: Literal["new_traces", "scheduled_dataset_run"] | None = None
    event_threshold: int | None = None  # event=new_traces 시 N개 누적되면 트리거

class AlertThreshold(BaseModel):
    """회귀 감지 임계값."""
    metric: Literal["avg_score", "pass_rate", "evaluator_score"]
    evaluator_name: str | None = None  # metric=evaluator_score 시 필수
    operator: Literal["lt", "lte", "gt", "gte"]
    value: float  # 0.0~1.0 (점수) 또는 0.0~1.0 (비율)
    drop_pct: float | None = None  # 직전 run 대비 N% 하락 시 발화
    window_minutes: int = 60  # 비교 대상 직전 run의 시간 윈도우

PolicyStatus = Literal["active", "paused", "deprecated"]

class AutoEvalPolicy(BaseModel):
    id: str  # "policy_<uuid>"
    name: str
    description: str | None = None
    project_id: str
    
    # 평가 대상
    trace_filter: TraceFilter
    expected_dataset_name: str | None = None  # 골든셋 매칭 (선택)
    
    # 평가 함수
    evaluators: list[EvaluatorConfig]
    
    # 스케줄
    schedule: AutoEvalSchedule
    
    # 회귀 감지 + 알림
    alert_thresholds: list[AlertThreshold] = []
    notification_targets: list[str] = []  # user_id 또는 channel ID
    
    # 비용 관리
    daily_cost_limit_usd: float | None = None  # 정책당 일일 LLM Judge 비용 한도
    
    # 운영
    status: PolicyStatus = "active"
    owner: str  # user_id
    created_at: datetime
    updated_at: datetime
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None

class AutoEvalRunStatus(str):
    """RUNNING, COMPLETED, FAILED, SKIPPED"""

class AutoEvalRun(BaseModel):
    id: str  # "run_<uuid>"
    policy_id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["running", "completed", "failed", "skipped"]
    skip_reason: str | None = None  # daily_cost_limit_exceeded 등
    
    # 결과
    traces_evaluated: int = 0
    traces_total: int = 0  # 필터 매칭 총 수 (sample_size 적용 전)
    avg_score: float | None = None
    pass_rate: float | None = None  # weighted_score >= 0.7 비율
    cost_usd: float = 0.0  # LLM Judge 호출 비용
    duration_ms: float | None = None
    
    # 평가 함수별 평균
    scores_by_evaluator: dict[str, float | None] = {}
    
    # 회귀 감지 결과
    triggered_alerts: list[str] = []  # alert_threshold metric 식별자
    
    # 검토 큐 진입한 항목 수
    review_items_created: int = 0
    
    error_message: str | None = None
```

#### 8.2 Redis 키 스키마

```
ax:auto_eval_policy:{id}                  # Hash (정책 메타)
ax:auto_eval_policies:active              # Sorted Set (next_run_at score)
ax:auto_eval_policies:by_project:{pid}    # Sorted Set
ax:auto_eval_run:{id}                     # Hash (run 메타 + 결과)
ax:auto_eval_runs:by_policy:{pid}         # Sorted Set (started_at score)
ax:auto_eval_cost:{policy_id}:{date}      # 일일 비용 누적 카운터 (TTL 48h)
```

TTL: active 정책은 영속화 (TTL 없음), run 메타는 90일.

### 9. 트리거 메커니즘 [Spec]

#### 9.1 Cron 기반

Backend에서 APScheduler 또는 자체 asyncio worker로 `next_run_at` 임박 정책을 polling.

```python
# app/services/auto_eval_scheduler.py
class AutoEvalScheduler:
    """5초 간격으로 ax:auto_eval_policies:active ZSet 스캔.
    next_run_at <= now인 정책을 큐에 넣고 worker가 처리."""
    
    async def start(self):
        while not self._stop_event.is_set():
            policies = await self._redis.zrangebyscore(
                "ax:auto_eval_policies:active", 0, datetime.now(UTC).timestamp()
            )
            for policy_id in policies:
                await self._enqueue(policy_id)
            await asyncio.sleep(5)
```

cron 표현식 파싱은 `croniter` 사용.

#### 9.2 Event 기반 (선택, v2)

v1에서는 cron/interval만 구현. v2에서 Langfuse webhook 또는 polling으로 새 trace 카운트 → threshold 도달 시 트리거.

#### 9.3 Manual run

API로 즉시 실행:
```
POST /api/v1/auto-eval/policies/{id}/run-now
```

### 10. 실행 엔진 [Spec]

#### 10.1 `app/services/auto_eval_engine.py`

```python
class AutoEvalEngine:
    """단일 정책 실행 — Trace fetch → evaluator pipeline → 결과 저장 → 회귀 감지."""
    
    def __init__(
        self,
        trace_fetcher: TraceFetcher,
        pipeline: EvaluationPipeline,
        langfuse: LangfuseClient,
        redis: RedisClient,
        notification: NotificationService,
        review_queue: ReviewQueueService,  # Phase 8-C 의존
    ): ...
    
    async def run_policy(self, policy_id: str) -> AutoEvalRun:
        """정책 1회 실행."""
        # 1. 정책 로드
        policy = await self._load_policy(policy_id)
        
        # 2. 일일 비용 한도 체크
        if await self._cost_limit_exceeded(policy):
            return AutoEvalRun(status="skipped", skip_reason="cost_limit_exceeded")
        
        # 3. AutoEvalRun 생성 (status=running)
        run = AutoEvalRun(...)
        await self._persist_run(run)
        
        # 4. trace fetch
        traces = await self._trace_fetcher.search(policy.trace_filter)
        
        # 5. expected dataset 매칭 (선택)
        expecteds = await self._match_expected(traces, policy.expected_dataset_name)
        
        # 6. 각 trace에 대해 pipeline.evaluate_trace 병렬 실행
        results = await asyncio.gather(*[
            self._pipeline.evaluate_trace(policy.evaluators, trace, expecteds.get(trace.id))
            for trace in traces
        ])
        
        # 7. 집계 (avg_score, pass_rate, scores_by_evaluator)
        run.avg_score = ...
        run.pass_rate = ...
        run.scores_by_evaluator = ...
        
        # 8. Langfuse score 기록 (trace별)
        for trace, scores in zip(traces, results):
            for name, value in scores.items():
                if value is not None:
                    await self._langfuse.score(trace.id, name, value)
        
        # 9. 회귀 감지
        triggered = await self._check_alerts(policy, run)
        run.triggered_alerts = triggered
        
        # 10. 알림 발송
        if triggered:
            await self._notification.create(...)
        
        # 11. Review Queue 진입 (조건 충족 시 — Phase 8-C)
        review_count = await self._enqueue_for_review(traces, results, policy)
        run.review_items_created = review_count
        
        # 12. run 완료
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        await self._persist_run(run)
        
        # 13. policy.next_run_at 갱신
        await self._reschedule(policy)
        
        return run
```

#### 10.2 동시 실행 한도

- 워크스페이스당 동시 정책 실행: 5개
- Redis 분산 카운터 `ax:auto_eval:concurrency` 사용

#### 10.3 Worker 구성

Backend FastAPI lifespan에서 `AutoEvalScheduler`를 백그라운드 task로 시작. SIGTERM 수신 시 진행 중 run을 graceful 완료 후 종료.

### 11. 결과 저장 + 시계열 [Spec]

#### 11.1 Langfuse score

각 trace의 평가 결과는 해당 trace_id에 `score` 형태로 저장:
- name: evaluator 이름 (예: `tool_called__web_search`, `weighted_score`)
- value: 0.0~1.0
- comment: AutoEvalRun ID + policy 이름

#### 11.2 Prometheus 메트릭 (OBSERVABILITY.md 확장)

```
ax_auto_eval_runs_total{policy_id, status}
ax_auto_eval_run_duration_seconds{policy_id}
ax_auto_eval_traces_evaluated_total{policy_id}
ax_auto_eval_avg_score{policy_id}                          # gauge
ax_auto_eval_pass_rate{policy_id}                          # gauge
ax_auto_eval_evaluator_score{policy_id, evaluator}         # gauge per evaluator
ax_auto_eval_cost_usd_total{policy_id}                     # counter
ax_auto_eval_alerts_triggered_total{policy_id, metric}
```

Recording rules로 추세 집계:
```
ax:auto_eval:avg_score:rate_24h
ax:auto_eval:pass_rate:delta_7d  # 7일 전 대비 변화율
```

#### 11.3 시계열 차트 (Frontend)

Auto-Eval 정책 상세 페이지에 다음 차트:
- 시간별 avg_score (line chart)
- 시간별 pass_rate (line chart)
- evaluator별 score breakdown (stacked area)
- 누적 비용 (line)

데이터 소스: Prometheus query (`/api/v1/analysis/...`로 프록시).

### 12. 회귀 감지 + 알림 [Spec]

#### 12.1 임계 평가 로직

```python
def evaluate_alert(threshold: AlertThreshold, run: AutoEvalRun, baseline: AutoEvalRun | None) -> bool:
    """임계 충족 여부."""
    current = _get_metric(threshold.metric, threshold.evaluator_name, run)
    if current is None:
        return False
    
    # 절대값 임계
    if threshold.operator == "lt" and current < threshold.value:
        return True
    if threshold.operator == "lte" and current <= threshold.value:
        return True
    # ... gt, gte
    
    # 상대 변화 (drop_pct)
    if threshold.drop_pct is not None and baseline is not None:
        baseline_value = _get_metric(threshold.metric, threshold.evaluator_name, baseline)
        if baseline_value and (baseline_value - current) / baseline_value >= threshold.drop_pct:
            return True
    
    return False
```

#### 12.2 알림 전달

- **In-app**: 기존 NotificationService.create_notification (`type="auto_eval_regression"`)
- **외부**: Alertmanager 라우팅 (Phase 1-7-A에서 합의된 라우팅 키 `team=labs` 사용)
- Notification body 예: `qa-agent v3 정책에서 pass_rate 0.92 → 0.78 (15% 하락). [상세 보기 →]`

### 13. API 설계 — Auto-Eval [Spec]

#### 13.1 Policy CRUD — `/api/v1/auto-eval/policies`

| 엔드포인트 | 동작 | 권한 |
|---|---|---|
| `POST /policies` | 정책 생성 | user+ |
| `GET /policies` | 목록 (페이지네이션, status 필터) | viewer+ |
| `GET /policies/{id}` | 상세 (정책 + 최근 N runs 요약) | viewer+ |
| `PATCH /policies/{id}` | 수정 (ETag/If-Match) | owner / admin |
| `DELETE /policies/{id}` | 삭제 (admin only — 또는 status=deprecated 권장) | admin |
| `POST /policies/{id}/pause` | 일시정지 | owner / admin |
| `POST /policies/{id}/resume` | 재개 | owner / admin |
| `POST /policies/{id}/run-now` | 즉시 실행 (manual trigger) | user+ |

#### 13.2 Run history — `/api/v1/auto-eval/runs`

| 엔드포인트 | 동작 |
|---|---|
| `GET /runs?policy_id=...` | 특정 정책의 run 이력 (페이지네이션, status 필터) |
| `GET /runs/{id}` | 단건 상세 (집계 결과 + triggered_alerts + review_items_created) |
| `GET /runs/{id}/items` | 그 run에서 평가된 trace 목록 (각 trace의 score 포함) |

#### 13.3 비용 조회

| 엔드포인트 | 동작 |
|---|---|
| `GET /policies/{id}/cost-usage?date_range=...` | 일자별 비용 + 누적 |

---

## Part III. Review Queue

### 14. 큐 진입 조건 [Spec]

ReviewItem이 큐에 들어가는 5가지 trigger:

| Trigger | 진입 조건 | 사유 (reason 필드) |
|---|---|---|
| **Auto-Eval threshold 미달** | AutoEvalRun에서 weighted_score < 0.5 또는 정책의 임계 조건 매칭 | `auto_eval_low_score` |
| **LLM Judge 저신뢰** | Judge 응답이 "score: 5/10" 정도로 중간값 + reasoning에 "uncertain" 등 키워드 | `judge_low_confidence` |
| **Evaluator 간 불일치** | exact_match=1.0 인데 llm_judge_factuality<0.3 등 큰 분산 (variance > 0.3) | `evaluator_disagreement` |
| **사용자 신고** | Frontend에서 "이 결과 잘못됐어요" 버튼 | `user_report` |
| **수동 추가** | Reviewer가 직접 trace를 큐에 넣음 | `manual_addition` |

자동 추가 조건은 AutoEvalEngine 내부 로직 (`_enqueue_for_review` 단계).

### 15. 데이터 모델 [Spec]

#### 15.1 `app/models/review.py`

```python
ReviewItemType = Literal[
    "auto_eval_flagged",   # auto_eval_low_score, evaluator_disagreement
    "judge_low_confidence",
    "user_report",
    "manual_addition",
    "evaluator_submission", # 기존 Phase 5 거버넌스도 통합 (선택)
]

ReviewStatus = Literal["open", "in_review", "resolved", "dismissed"]
ReviewDecision = Literal["approve", "override", "dismiss", "add_to_dataset"]

class ReviewItem(BaseModel):
    id: str  # "review_<uuid>"
    type: ReviewItemType
    severity: Literal["low", "medium", "high"] = "medium"
    
    # 평가 대상
    subject_type: Literal["trace", "experiment_item", "submission"]
    subject_id: str  # trace_id 또는 experiment_item_id 또는 submission_id
    project_id: str
    
    # 진입 사유
    reason: str  # "auto_eval_low_score", "evaluator_disagreement"
    reason_detail: dict[str, Any] = Field(default_factory=dict)
    # 예: {"weighted_score": 0.32, "policy_id": "...", "run_id": "..."}
    
    # 자동 평가 결과 (snapshot)
    automatic_scores: dict[str, float | None] = {}
    
    # 큐 상태
    status: ReviewStatus = "open"
    assigned_to: str | None = None  # user_id
    assigned_at: datetime | None = None
    
    # 결정 (resolve 시 채워짐)
    decision: ReviewDecision | None = None
    reviewer_score: float | None = None  # decision="override" 시
    reviewer_comment: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    
    # 관련 정책/run
    auto_eval_policy_id: str | None = None
    auto_eval_run_id: str | None = None
    
    # 메타
    created_at: datetime
    updated_at: datetime
    
class ReviewerStats(BaseModel):
    user_id: str
    open_count: int
    in_review_count: int
    resolved_today: int
    avg_resolution_time_min: float | None
    decisions_breakdown: dict[ReviewDecision, int]
```

#### 15.2 Redis 키 스키마

```
ax:review_item:{id}                       # Hash
ax:review_queue:open                      # Sorted Set (severity_score asc, then created_at)
ax:review_queue:in_review:{user_id}       # Set (해당 reviewer가 claim한 항목)
ax:review_queue:by_policy:{policy_id}     # Sorted Set
ax:review_queue:by_subject:{type}:{id}    # Set (해당 subject의 모든 review)
ax:review_stats:{user_id}:{date}          # Hash (일일 집계)
```

severity_score: low=1, medium=2, high=3 (높을수록 우선).

### 16. 워크플로우 [Spec]

#### 16.1 상태 전이

```
[자동/수동 진입]
       ↓
    open ────► dismiss ────────────────────────────► dismissed (false positive)
       │
       │ Reviewer claim
       ▼
   in_review
       │
       ├──► approve ─────────────► resolved (자동 점수 확정)
       ├──► override ────────────► resolved (수동 점수로 Langfuse score 갱신)
       ├──► dismiss ─────────────► dismissed
       └──► add_to_dataset ──────► resolved (골든셋에 trace 추가)
```

규칙:
- open → in_review: assigned_to/assigned_at 기록
- in_review → resolved/dismissed: decision/reviewer_*/resolved_* 기록
- 한 번 resolved/dismissed면 재오픈 불가 (재오픈 필요 시 새 ReviewItem 생성)
- claim 후 1시간 미해결이면 자동으로 unassign (다른 reviewer가 잡을 수 있도록)

#### 16.2 RBAC

| Role | 권한 |
|---|---|
| `viewer` | 큐 + 상세 조회만 |
| `user` | 사용자 신고 (`type=user_report`) 가능 |
| `reviewer` (신규) | claim + decide + 통계 조회 |
| `admin` | 모든 권한 + 강제 unassign + 큐 항목 삭제 |

기존 RBAC에 `reviewer` 추가 — JWT claim 매핑은 사내 Auth에서 그룹 추가 시점에 합의.

### 17. 결과 활용 — 피드백 루프 [Spec]

#### 17.1 decision별 후처리

| Decision | 동작 |
|---|---|
| **approve** | 자동 score를 그대로 확정. AutoEvalEngine의 자동 점수가 정답으로 기록. 다음 run의 baseline에 포함. |
| **override** | reviewer_score로 Langfuse score 갱신 (덮어쓰기). 자동 점수와 차이는 evaluator 정확도 메트릭 (`ax_evaluator_disagreement`)으로 기록. |
| **dismiss** | 큐에서 제거. 진입 사유가 false positive로 분류. 동일 사유로 다시 들어오지 않도록 reason_pattern 등 학습 (선택, v2). |
| **add_to_dataset** | 해당 trace의 input + reviewer가 입력한 expected_output을 골든셋에 추가. 향후 Live mode 회귀 검증 데이터로 사용. |

#### 17.2 골든셋 자동 보강

`add_to_dataset` 결정 시:
1. Reviewer가 expected_output, expected_tool_calls 등 입력 폼 채움
2. 본 프로젝트가 자동으로 dataset(`<agent_name>-reviewer-curated`)에 새 item 추가
3. 다음 Live 회귀 실험에서 자동 포함

이 데이터셋은 시간이 지날수록 풍부해져 Live mode의 정확도가 향상됨.

#### 17.3 Evaluator 정확도 학습

```
ax_evaluator_disagreement_total{evaluator, decision}
```

evaluator별 override 비율을 추적. override 비율이 높은 evaluator는 rubric/threshold 재조정 후보.

### 18. API 설계 — Review Queue [Spec]

#### 18.1 큐 listing — `/api/v1/reviews/items`

| 엔드포인트 | 동작 | 권한 |
|---|---|---|
| `GET /items` | 큐 목록 (status, type, severity, project_id 필터, 페이지네이션) | viewer+ |
| `GET /items/{id}` | 상세 (reason_detail + automatic_scores + 관련 trace 링크) | viewer+ |
| `POST /items` | 수동 추가 (manual_addition) | user+ |
| `PATCH /items/{id}/claim` | claim (open → in_review) | reviewer+ |
| `PATCH /items/{id}/release` | unassign (in_review → open) | reviewer (본인) / admin |
| `POST /items/{id}/resolve` | 결정 (approve/override/dismiss/add_to_dataset) — ETag/If-Match | reviewer+ |
| `DELETE /items/{id}` | 삭제 (admin only) | admin |

#### 18.2 통계 — `/api/v1/reviews/stats`

| 엔드포인트 | 동작 |
|---|---|
| `GET /stats/summary` | 전체 큐 상태 (open/in_review/resolved 수, 평균 처리 시간) |
| `GET /stats/reviewer/{user_id}` | reviewer 개인 통계 |
| `GET /stats/disagreement` | evaluator별 override 비율 (학습용) |

#### 18.3 사용자 신고 — `/api/v1/reviews/report`

```
POST /api/v1/reviews/report
{
  "trace_id": "...",
  "reason": "응답이 사실과 다름",
  "severity": "medium"
}
```
권한: user+. 자동으로 ReviewItem 생성 (`type=user_report`).

### 19. UI 설계 [Spec]

#### 19.1 새 페이지 `/review`

```
┌─────────────────────────────────────────────────────────────────┐
│ Review Queue                              [필터 ▾] [+ 수동 추가] │
├─────────────────────────────────────────────────────────────────┤
│ ┌─ KPI 카드 ──────────────────────────────────────────────────┐ │
│ │ Open: 27   In Review: 4   Resolved (오늘): 12              │ │
│ │ 평균 처리: 4분 32초                                          │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ 탭: [전체] [내가 담당] [높은 우선순위] [User Report]            │
│                                                                 │
│ ┌─ 큐 테이블 ─────────────────────────────────────────────────┐ │
│ │ 우선순위 │ 사유                │ Subject     │ 자동 점수 │  │ │
│ │ ──────────────────────────────────────────────────────────  │ │
│ │ 🔴 high │ evaluator_disagree │ trace_xxx   │ 0.5±0.4   │  │ │
│ │ 🟡 med  │ auto_eval_low     │ trace_yyy   │ 0.32      │  │ │
│ │ 🟢 low  │ user_report       │ trace_zzz   │ 0.91      │  │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

#### 19.2 상세 페이지 `/review/[id]`

```
┌─────────────────────────────────────────────────────────────────┐
│ Review #review_xxx (in_review by 노동훈)              [unassign]│
├─────────────────────────────────────────────────────────────────┤
│ ┌─ 진입 사유 ─────────────────────────────────────────────────┐ │
│ │ evaluator_disagreement                                      │ │
│ │ exact_match=1.0, llm_judge_factuality=0.2 (variance: 0.4)   │ │
│ │ Auto-Eval Policy: qa-agent-v3-daily                         │ │
│ │ Run: run_yyy (2026-04-28T03:00:00Z)                         │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ ┌─ Trace ─────────────────────────────────────────────────────┐ │
│ │ name: qa-agent-v3                                           │ │
│ │ user: user_42  · session: sess_abc                          │ │
│ │ Input:  "오늘 서울 날씨 알려줘"                              │ │
│ │ Output: "서울은 맑고 18도입니다."                            │ │
│ │                                                             │ │
│ │ Tool calls (2):                                             │ │
│ │  ✓ weather_api(city="서울") → {temp: 18, sky: "clear"} 240ms│ │
│ │  ✓ format_response(...) → ...                               │ │
│ │                                                             │ │
│ │ [Langfuse에서 전체 보기 →]                                   │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ ┌─ 자동 평가 결과 (snapshot) ────────────────────────────────┐ │
│ │ exact_match: ● 1.00                                        │ │
│ │ llm_judge_factuality: ✕ 0.20                               │ │
│ │ tool_called(weather_api): ● 1.00                           │ │
│ │ no_error_spans: ● 1.00                                     │ │
│ │ weighted_score: 0.55                                       │ │
│ └────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ ┌─ 결정 ──────────────────────────────────────────────────────┐ │
│ │ ◯ Approve (자동 점수 확정)                                  │ │
│ │ ◉ Override                                                  │ │
│ │   수정된 점수: [0.95] 0.0 ────●──── 1.0                     │ │
│ │   사유: [날씨 정보가 정확함. judge가 잘못 판단함.]           │ │
│ │ ◯ Dismiss (false positive)                                  │ │
│ │ ◯ Add to Dataset (골든셋에 추가)                            │ │
│ │                                                             │ │
│ │ [결정 저장] [취소]                                           │ │
│ └─────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

#### 19.3 Auto-Eval 정책 페이지 `/auto-eval`

```
┌─────────────────────────────────────────────────────────────────┐
│ Auto-Eval Policies                          [+ 새 정책]         │
├─────────────────────────────────────────────────────────────────┤
│ ┌─ 정책 카드 ─────────────────────────────────────────────────┐ │
│ │ qa-agent-v3-daily                              [● active]   │ │
│ │ 매일 03:00 KST · qa-agent v3 production tag · 200건 샘플    │ │
│ │ 마지막 run: 2시간 전 · pass_rate 0.92 · 비용 $0.42          │ │
│ │ [상세] [실행] [편집] [일시정지]                              │ │
│ └─────────────────────────────────────────────────────────────┘ │
│ ┌─ ... ───────────────────────────────────────────────────────┐ │
└─────────────────────────────────────────────────────────────────┘
```

정책 상세에는 시계열 차트 (avg_score / pass_rate / cost / evaluator breakdown).

#### 19.4 Top Bar 통합

- 종 아이콘에 **Review Queue 미해결 건수** 추가 표시 (기존 알림과 별도 배지)
- Side Nav에 신규 아이콘 2개:
  - `/review` (ClipboardCheck 아이콘)
  - `/auto-eval` (Activity 또는 Repeat 아이콘)

### 20. 사용자 신고 통합 (Phase 7 페이지 확장)

기존 `/compare/items` 행에 "🚩 신고" 버튼 추가:
- 클릭 → 모달: severity + 사유 → `POST /api/v1/reviews/report`
- 즉시 큐에 진입 (status=open, type=user_report)

---

## Part IV. 통합 — 피드백 루프

### 21. 전체 데이터 흐름

```
┌────────────────────────────────────────────────────────────────┐
│ 사내 Production Agent (qa-agent-v3) 실행                        │
│ → Langfuse trace 1건 기록 (input + tool_calls + output)         │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ AutoEvalScheduler (5초마다 polling)                            │
│ qa-agent-v3-daily 정책의 next_run_at 도달                      │
│ → AutoEvalEngine.run_policy("policy_qa_v3_daily")              │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ TraceFetcher                                                   │
│ filter: name=qa-agent-v3, tag=production, 지난 24h, 200건 샘플   │
│ → ClickHouse 쿼리 → TraceTree[] 200개                          │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ EvaluationPipeline.evaluate_trace (200 trace 병렬, 5초 timeout)│
│ - exact_match (output)                                         │
│ - tool_called(weather_api)                                     │
│ - no_error_spans                                                │
│ - tool_result_grounding (LLM Judge — 비용 발생)                │
│ - llm_judge_factuality                                          │
│ - weighted_score (자동 계산)                                    │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────┬──────────────────┬─────────────────────┐
│ Langfuse score 기록 │ Prometheus 메트릭 │ Review Queue 진입   │
│ trace 200개 × 6개   │ ax_auto_eval_*    │ weighted_score<0.5  │
│ score = 1200 entries│ → 시계열 추적     │ 또는 disagreement   │
│                     │                   │ → 28건 ReviewItem   │
└─────────────────────┴──────────────────┴─────────────────────┘
                                                  │
                                                  ▼
┌────────────────────────────────────────────────────────────────┐
│ 회귀 감지: pass_rate 0.95 → 0.78 (지난 run 대비 18% 하락)       │
│ → AlertThreshold 매칭                                           │
│ → notification.create("auto_eval_regression", target=...)       │
│ → Alertmanager 라우팅 → Slack 알림                              │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ Reviewer (admin) 알림 수신 → /review 페이지 진입                 │
│ 28개 큐 항목 → 5개씩 claim → 검토                               │
│  ├─ 18개 approve (자동 점수 정확)                                │
│  ├─ 6개 override (자동 점수가 너무 낮게 평가 — judge 보정)       │
│  ├─ 2개 dismiss (false positive)                                │
│  └─ 2개 add_to_dataset (특이 케이스 → 골든셋 보강)              │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ 후속 효과:                                                      │
│ - override된 6개의 score → Langfuse 갱신                        │
│ - 추가된 2건 → qa-agent-v3-reviewer-curated 데이터셋             │
│ - evaluator_disagreement 메트릭 → llm_judge_factuality rubric   │
│   재조정 필요 신호                                              │
│ - 다음 Live mode 회귀 검증에 새 데이터셋 자동 포함              │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                     [다음 daily run]
                     (반복)
```

### 22. 시나리오 — 일주일 운영 [Spec]

| 일 | 이벤트 |
|---|---|
| 월 03:00 | 정책 daily 실행 → pass_rate 0.92 (정상) |
| 월 14:00 | 사용자가 "이상한 답변" 신고 → 큐에 1건 |
| 월 16:00 | Reviewer가 신고 처리 → override → score 0.0 기록 |
| 화 03:00 | 정책 실행 → 동일 패턴의 trace 5건 발견 → variance 큰 케이스로 자동 진입 → 큐 +5 |
| 화 09:00 | 추세 악화 알림 (pass_rate 0.89) |
| 수 | Researcher가 prompt 수정 → 새 v4 staging 배포 |
| 목 03:00 | 정책에 v4 tag 추가 + 카나리 (5%) → v3 vs v4 동시 평가 |
| 금 | v4가 모든 evaluator에서 우월 확인 → production 승격 |
| 토 03:00 | 정책 실행 → 새 baseline 형성 → pass_rate 0.96 |
| 일 | 안정화 |

---

## Part V. 구현 로드맵

### 23. Phase 8 분할 [Spec]

#### 23.1 Phase 8-A — Trace Evaluation Foundation (3~4일)

선결 조건: Phase 0~7 완료 (현재 상태)

작업 목록:
- 8-A-1. `app/models/trace.py` (TraceTree, TraceObservation, TraceFilter)
- 8-A-2. `app/services/trace_fetcher.py` (ClickHouse direct + 폴백)
- 8-A-3. `app/services/clickhouse_queries.py`에 trace 검색 쿼리 추가
- 8-A-4. `app/evaluators/trace_base.py` (TraceEvaluator Protocol + OutputAdapter)
- 8-A-5. `app/evaluators/trace_built_in.py` (8~10종 trace evaluator)
- 8-A-6. `app/evaluators/pipeline.py` 확장 (`evaluate_trace` 메서드)
- 8-A-7. `app/api/v1/traces.py` (검색·조회 API)
- 8-A-8. ExperimentCreate/BatchExperimentRunner에 mode=trace_eval 분기
- 8-A-9. Frontend 위저드 mode 토글 + trace_filter 입력 + trace evaluator 선택
- 8-A-10. 단위 테스트 (trace evaluator 30+ cases)

산출물:
- `POST /api/v1/experiments` mode=trace_eval로 실험 생성 가능
- 사용자가 trace 필터 + evaluator 선택 → 결과 리포트
- 768 → ~830 tests

#### 23.2 Phase 8-B — Online Auto-Eval Engine (3~5일)

선결 조건: Phase 8-A 완료

작업 목록:
- 8-B-1. `app/models/auto_eval.py` (Policy, Schedule, AlertThreshold, Run)
- 8-B-2. `app/services/auto_eval_engine.py` (정책 1회 실행)
- 8-B-3. `app/services/auto_eval_scheduler.py` (cron worker)
- 8-B-4. Backend lifespan에 scheduler 통합 + graceful shutdown
- 8-B-5. `app/api/v1/auto_eval.py` (정책 CRUD + manual run + run history)
- 8-B-6. 회귀 감지 로직 + 알림 발송
- 8-B-7. Prometheus 메트릭 (`ax_auto_eval_*`)
- 8-B-8. 일일 비용 한도 + 차단
- 8-B-9. Frontend `/auto-eval` 페이지 (정책 목록 + 생성 + 상세 + 시계열 차트)
- 8-B-10. Side Nav 아이콘 추가

산출물:
- 정책 정의 → 자동 정기 실행 → 결과 시계열 차트
- 회귀 시 알림 수신
- 일일 비용 추적

#### 23.3 Phase 8-C — Review Queue (3~4일)

선결 조건: Phase 8-A, 8-B 완료

작업 목록:
- 8-C-1. `app/models/review.py` (ReviewItem, ReviewerStats)
- 8-C-2. `app/services/review_queue.py` (CRUD + 큐 관리 + 상태 전이)
- 8-C-3. AutoEvalEngine에 `_enqueue_for_review` 통합 (자동 진입 5 trigger)
- 8-C-4. `app/api/v1/reviews.py` (CRUD + claim + resolve + 통계)
- 8-C-5. RBAC `reviewer` role 추가 (사내 Auth 그룹 매핑 합의 필요)
- 8-C-6. 결정별 후처리 로직 (approve/override/dismiss/add_to_dataset)
- 8-C-7. 골든셋 자동 보강 (`<agent>-reviewer-curated` dataset)
- 8-C-8. evaluator_disagreement 메트릭
- 8-C-9. Frontend `/review` 페이지 (큐 + 상세 + 결정 폼)
- 8-C-10. 사용자 신고 버튼 통합 (compare 페이지 등)
- 8-C-11. Top Bar 알림 배지에 review queue 카운트 추가

산출물:
- 자동 평가 결과를 reviewer가 검토 가능
- 결정 결과가 Langfuse score / 골든셋에 환원
- evaluator 정확도 학습 메트릭

#### 23.4 Phase 8 누적 산출

| 항목 | 추가 |
|---|---|
| 백엔드 모듈 | +12 |
| 백엔드 API | +25 endpoints |
| 백엔드 테스트 | +250 (예상) |
| 프론트엔드 페이지 | +2 (`/auto-eval`, `/review`) |
| 도메인 훅 | +3 (`useAutoEval`, `useReviews`, `useTraces`) |
| Pydantic 모델 | +8 |
| Redis 키 패턴 | +6 |

총 **9~13일** (3개 단계 순차 또는 일부 병렬).

### 24. 의존성 그래프

```
Phase 8-A (Trace Eval)
   │
   ├─► Phase 8-B (Auto-Eval) ─────────┐
   │                                   │
   └─► Phase 8-C (Review Queue) ◄─────┘
       (8-B의 자동 진입 사용)
```

8-B와 8-C는 8-A 완료 후 부분 병렬 가능 (auto-eval engine과 review queue 모듈은 의존성 약함, 통합 단계에서만 결합).

---

## Part VI. 비기능 요구사항

### 25. 성능 [Spec]

| NFR | 임계값 | 측정 메트릭 |
|---|---|---|
| Auto-Eval run duration (1000 traces) | p95 < 5분 | `ax_auto_eval_run_duration_seconds` (p95) |
| Trace fetch (200건) | p95 < 5초 | `ax_trace_fetcher_duration_seconds` (p95) |
| Trace evaluator 단건 | p95 < 1초 (LLM Judge 제외) | `ax_evaluator_duration_seconds{kind=trace}` |
| Review queue listing | p95 < 500ms | `ax_http_request_duration_seconds{route="/api/v1/reviews/items"}` |
| Worker 동시 정책 실행 | 워크스페이스당 ≤ 5 | `ax_auto_eval:concurrency` (gauge) |

### 26. 비용 관리 [Spec]

- AutoEvalPolicy 별 일일 비용 한도 (`daily_cost_limit_usd`)
- 한도 초과 시 run 자동 skip + 알림 (`auto_eval_cost_limit_exceeded`)
- 정책 생성 시 예상 일일 비용 표시 (sample_size × LLM Judge 비용 × schedule frequency)
- 전체 워크스페이스 일일 비용 상한 (NFR §12.1과 정합)

### 27. 보안 / PII [Spec]

- Trace fetcher는 readonly 계정만 사용 (Phase 6과 정합)
- Review Queue UI에서 trace input/output 표시 시 PII redaction 옵션
  - 주민번호/전화번호/이메일 정규식 매치 → `[REDACTED]` 마스킹
  - reviewer가 명시적으로 "원본 보기" 클릭 시 노출 (감사 로그 기록)
- Reviewer 결정 이력은 영구 보존 (감사용)
- Custom Code 거버넌스 (Phase 5)와 동일한 보안 정책 적용
- 사용자 신고 (`type=user_report`)에 신고자 user_id 보관, 단 reviewer 외에는 노출 안 함

### 28. 확장성 [Spec]

- AutoEvalScheduler는 단일 인스턴스 가정 (Redis 분산 락으로 보호)
- 정책 1만개 까지 ZSet 스캔 부하 허용 (Redis 1ms 미만)
- AutoEvalRun 결과는 90일 후 Langfuse만 보존 (Redis TTL)
- Review Queue는 1년치 history 유지 (감사). 1년 후 cold storage(S3 등) 이전

### 29. 관측성 [Spec]

신규 메트릭 (`OBSERVABILITY.md` §2.2 확장 후보):

```
ax_auto_eval_runs_total{policy_id, status}
ax_auto_eval_run_duration_seconds{policy_id} (histogram)
ax_auto_eval_traces_evaluated_total{policy_id}
ax_auto_eval_avg_score{policy_id} (gauge)
ax_auto_eval_pass_rate{policy_id} (gauge)
ax_auto_eval_evaluator_score{policy_id, evaluator} (gauge)
ax_auto_eval_cost_usd_total{policy_id} (counter)
ax_auto_eval_alerts_triggered_total{policy_id, metric}
ax_review_items_total{type, status, severity}
ax_review_items_created_total{type, source}  # source=auto/manual/user_report
ax_review_resolution_duration_seconds{decision} (histogram)
ax_review_disagreement_total{evaluator, decision}  # evaluator 정확도 학습
ax_trace_fetcher_duration_seconds (histogram)
ax_evaluator_duration_seconds{kind=trace, name}  # 신규 trace evaluator
```

신규 alert rules:
- `AutoEvalRegressionDetected` — pass_rate 7일 평균 대비 -10% 이상
- `AutoEvalCostLimitApproaching` — 일일 한도 80% 초과
- `ReviewQueueBacklog` — open + in_review 수 > 50
- `ReviewSLABreach` — 평균 처리 시간 > 24시간

---

## Part VII. 합의·결정 사항 [Spec]

본 문서 합의 단계에서 확정되어야 할 결정:

| # | 결정 | 옵션 | 추천 | 결정 |
|---|---|---|---|---|
| 1 | RBAC `reviewer` role 신설 vs `admin` 확장 | 신설 / `admin` 활용 | 신설 권장 — 권한 분리 + 전담 인력 | [ ] |
| 2 | Trace evaluator v1 카탈로그 (8~10개 중 우선 구현) | 위 §5.2의 10개 | 5개 우선 (`tool_called`, `tool_call_count_in_range`, `no_error_spans`, `tool_result_grounding`, `agent_loop_bounded`) — 나머지 v1.1 | [ ] |
| 3 | Review queue 진입 임계값 (auto_eval_low_score) | weighted_score < 0.5 / 0.6 / 0.7 | 0.5 (high recall, false positive는 dismiss로 학습) | [ ] |
| 4 | 골든셋 자동 보강 dataset 명명 | `<agent>-reviewer-curated` / `<agent>-golden-v<N>` | `<agent>-reviewer-curated` (불변 의도) | [ ] |
| 5 | LLM Judge 비용 한도 정책 | 정책당 / 워크스페이스 통합 / 둘 다 | 둘 다 (방어적) | [ ] |
| 6 | Auto-Eval 동시 실행 한도 | 워크스페이스당 5 / 10 | 5 (NFR §12.1과 정합) | [ ] |
| 7 | claim 자동 unassign 시간 | 30분 / 1시간 / 2시간 | 1시간 | [ ] |
| 8 | Phase 8-B와 8-C 병렬 진행 | 순차 / 부분 병렬 | 순차 (8-B 완료 후 8-C) — 의존성 명확 | [ ] |
| 9 | Trace UI에 mini step viewer 자체 구현 vs Langfuse 링크만 | 자체 / 링크만 | v1은 Langfuse 링크만 + summary card. v2에서 자체 viewer | [ ] |
| 10 | 사용자 신고 권한 | viewer+ / user+ | user+ (viewer는 작성 권한 없음 원칙) | [ ] |

---

## 부록 A. 참조 ADR 후보

본 Phase 8에서 새로 작성할 ADR:

| 번호 | 제목 | 트리거 |
|---|---|---|
| ADR-013 | Trace Evaluator 인터페이스 — Output 어댑터 vs 별도 Protocol | Phase 8-A 시작 시 |
| ADR-014 | Auto-Eval Scheduler 단일 인스턴스 vs 분산 워커 | Phase 8-B 시작 시 |
| ADR-015 | Review Queue 진입 자동화 정책 (recall-first vs precision-first) | Phase 8-C 시작 시 |
| ADR-016 | (조건부) Reviewer RBAC role 신설 | 결정 #1 시점 |

## 부록 B. 변경 이력

| 일자 | 변경 | 작성자 |
|---|---|---|
| 2026-04-28 | v1 초안 작성 (Phase 8 통합 설계) | _(TBD)_ |

---

## 다음 단계

본 문서가 합의되면:

1. **결정 10개 (Part VII)** 확정
2. **ADR 13~16 초안** 작성
3. **Phase 8-A 즉시 착수** — Trace Evaluation Foundation
4. (Phase 8-A 완료 후) **Phase 8-B 시작** — Auto-Eval Engine
5. (Phase 8-B 완료 후) **Phase 8-C 시작** — Review Queue
6. **`BUILD_ORDER.md` 갱신** — Phase 8 작업 단위 추가 (현재 Phase 8 운영 인계 → Phase 9로 밀기)

총 예상 9~13일 작업.
