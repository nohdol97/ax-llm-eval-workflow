# Langfuse v3 연동 전략

## 1. 연동 원칙

### Labs와 Langfuse의 역할 분담

| 영역 | Langfuse (데이터 레이어) | Labs (실행/UI 레이어) |
|------|------------------------|---------------------|
| 프롬프트 | 저장, 버전 관리, 라벨링 | 조회, 편집 UI, 변수 바인딩 |
| 데이터셋 | 저장, 아이템 관리 | 업로드 UI, 파일 파싱, 매핑 |
| 실험 실행 | trace/generation 기록 | 실행 엔진, LLM 호출, 오케스트레이션 |
| 평가 | score 저장 | 평가 함수 실행, 스코어 산출 |
| 분석 | ClickHouse 데이터 제공 | 쿼리 실행, 차트 렌더링 |

**핵심 원칙**: 데이터의 원본(source of truth)은 항상 Langfuse다. Labs는 자체 DB에 실험 데이터를 중복 저장하지 않는다.

---

## 2. Langfuse SDK 사용

### 2.1 초기화 및 라이프사이클

```python
from langfuse import Langfuse, observe

langfuse = Langfuse(
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    secret_key=settings.LANGFUSE_SECRET_KEY,
    base_url=settings.LANGFUSE_HOST,   # v3: host가 아닌 base_url
    environment=settings.ENV,           # "production" | "staging" | "development"
    release=settings.GIT_SHA,           # 배포 버전 추적
    sample_rate=1.0,                    # 프로덕션 트래픽 과부하 시 샘플링
    tracing_enabled=True,
)
```

환경 변수:
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`
- 프로젝트별 키 격리 (멀티 프로젝트):
  - `get_client(public_key=...)`로 public_key를 캐시 키 삼아 프로젝트별 클라이언트 싱글톤을 분리 (v3 권장). 동일 프로세스 내 여러 프로젝트를 섞어 쓸 때 secret_key가 교차 오염되지 않도록 한다.
  - FastAPI에서는 요청 의존성(`Depends(get_project_langfuse)`)으로 JWT의 project_id → (public_key, secret_key) 매핑을 조회해 주입. 전역 `langfuse` 변수 사용 금지.
  - 키 매핑은 Vault/Secrets Manager에서 로드, 프로세스 메모리에 LRU(TTL 5분)로 캐시. 키 순환 시 캐시 무효화 엔드포인트 제공.
  - 멀티 프로젝트 환경에서는 환경변수 기반 전역 초기화(`LANGFUSE_PUBLIC_KEY`)를 비활성화하고 명시적 생성자 주입만 허용한다.

**플러시/셧다운 정책 (필수)**:
- v3 SDK는 OTel BatchSpanProcessor 기반으로 백그라운드 비동기 전송 → 프로세스 종료 시 데이터 유실 위험
- FastAPI: `lifespan` 이벤트의 shutdown 단계에서 `langfuse.flush()` 후 `langfuse.shutdown()` 호출 필수
- 배치 실험 워커: 각 배치 완료 시점에 `flush()`, 워커 종료 전 `shutdown()` 호출
- Uvicorn/Gunicorn 멀티워커: 워커 프로세스별로 독립 flush/shutdown (fork 후 재초기화)
- 단일 동기 요청 완료 대기가 필요한 경우에만 명시적 `flush()` (성능 비용 있음)

### 2.2 Prompt Management API 활용

#### 프롬프트 조회
```
GET /api/public/v2/prompts/{name}
- query params: version (int), label (string)
- 응답: name, version, prompt (text/chat), config, labels, tags
```

#### SDK 사용 패턴 (v3 권장)
```python
# v3: label 우선 조회 + 클라이언트 측 캐시 + fallback 필수
prompt = langfuse.get_prompt(
    name="qa-system",
    label="production",          # version 대신 label 우선 (production/staging/latest)
    cache_ttl_seconds=300,       # 클라이언트 측 캐시 TTL (기본 60s, 0이면 매번 fetch)
    max_retries=2,
    fetch_timeout_seconds=5,
    fallback=[                   # Langfuse API 장애 시 사용할 fallback 메시지
        {"role": "system", "content": "You are a helpful QA assistant."},
        {"role": "user", "content": "{{question}}"},
    ],
    type="chat",                 # "chat" | "text"
)

# 변수 바인딩 후 LLM에 전달
compiled_messages = prompt.compile(question=item.input["question"])

# generation에 prompt 객체를 직접 연결 → 프롬프트-실험 역추적
with root_span.start_as_current_observation(
    name="llm-call", as_type="generation",
    model="gpt-4o", input=compiled_messages,
    prompt=prompt,               # ★ Langfuse가 trace ↔ prompt version을 자동 link
) as gen:
    ...
```

> **캐시/Fallback 운용 원칙**:
> - **label 기반 조회 강제**: `version=N` 하드코딩 금지. production 승격은 label 이동으로만 수행하여 코드 배포 없이 롤백 가능.
> - **cache_ttl_seconds**: 배치 실험 워커는 300s 이상(트래픽 적음), 온라인 추론은 60~120s. TTL 만료 전 stale-while-revalidate로 latency 영향 없음.
> - **fallback 필수**: 모든 `get_prompt()` 호출에 `fallback`을 지정해야 한다. Langfuse API 장애 시 fallback이 없으면 예외 발생 → 서비스 중단. fallback은 코드 리포지토리에 인라인으로 보관하여 SPOF 제거.
> - **캐시 무효화**: 긴급 프롬프트 교체 시 `langfuse.get_prompt(..., cache_ttl_seconds=0)`로 1회 강제 fetch 후 TTL 복원. 또는 워커 재시작.

#### 프롬프트 목록 조회
```
GET /api/public/v2/prompts
- 전체 프롬프트 목록 (이름, 최신 버전, 라벨)
- UI 드롭다운에 사용
```

#### 프롬프트 생성/업데이트
```
POST /api/public/v2/prompts
- Labs에서 직접 프롬프트를 생성하거나 버전을 올릴 수 있음
- 실험 결과가 좋은 프롬프트를 "production" 라벨로 승격
```

### 2.3 Dataset API 활용

#### 데이터셋 생성
```
langfuse.create_dataset(name, description, metadata)
  → Dataset 객체 반환
```

#### 데이터셋 아이템 생성
```
langfuse.create_dataset_item(
    dataset_name,
    input,            # dict - 프롬프트 변수에 바인딩될 값
    expected_output,  # str/dict - 기대 출력 (평가 기준)
    metadata          # dict - 카테고리, 난이도 등 부가 정보
)
```

#### 데이터셋 조회
```
langfuse.get_dataset(name)
  → Dataset 객체 (items 포함)
  → 각 item: id, input, expected_output, metadata
```

#### Dataset Run 연결 (v3 권장 패턴)

v3에서는 `item.run(run_name, run_description, run_metadata)` 컨텍스트 매니저로 dataset_run_item을 생성하고, 블록 내부에서 시작된 root observation이 자동으로 해당 run에 연결된다. `item.link()`는 레거시 보조 API로 외부에서 미리 생성된 trace를 사후 연결할 때만 사용한다.

```python
dataset = langfuse.get_dataset("eval-set-v1")

for item in dataset.items:
    # v3: item.run()이 dataset_run_item을 만들고, 블록 내 root span을 해당 run에 자동 link
    with item.run(
        run_name=f"{experiment_name}-{model}-{ts}",
        run_description="batch eval on gpt-4o temp=0.1",
        run_metadata={
            "experiment_id": experiment_id,
            "prompt_name": prompt.name,
            "prompt_version": prompt.version,
            "model": model,
        },
    ) as root_span:
        # 이 span이 dataset item의 input을 처리하는 실행 단위
        root_span.update(input=item.input)
        with langfuse.propagate_attributes(
            session_id=f"batch_{experiment_id}",
            tags=["ax-eval", "batch", experiment_name, model],
        ):
            with root_span.start_as_current_observation(
                name="llm-call",
                as_type="generation",
                model=model,
                input=compiled_messages,
                prompt=prompt,  # Langfuse prompt 객체 연결 → 프롬프트-실험 역추적
            ) as generation:
                resp = await litellm.acompletion(model=model, messages=compiled_messages)
                generation.update(
                    output=mask_pii(resp.choices[0].message.content),
                    usage_details={"input": resp.usage.prompt_tokens,
                                   "output": resp.usage.completion_tokens,
                                   "total": resp.usage.total_tokens},
                    cost_details={"total": litellm.completion_cost(resp)},
                )
            # Evaluator 실행 후 스코어를 root span이 아닌 generation/trace에 기록
            score_value = exact_match(item.expected_output, resp.choices[0].message.content)
            generation.score(name="exact_match", value=score_value, data_type=ScoreDataType.NUMERIC)
            root_span.update(output={"score": score_value})

# 배치 완료 후 명시적 flush (워커 종료 전)
langfuse.flush()
```

> **실패 처리**: item 처리 중 예외가 나더라도 `with item.run(...)`은 dataset_run_item을 남긴다. 상위에서 `except` 후 `root_span.update(metadata={"status": "failed"})` 처리 후 재-raise 하여 배치 루프가 다음 item으로 진행하도록 한다(전체 실험을 중단시키지 않음).

레거시/외부 연결이 필요한 경우:
```python
# 이미 기록된 trace를 사후에 dataset item과 연결
item.link(
    trace_or_observation,  # trace 또는 generation 객체
    run_name,              # 실험 Run 이름 (고유 식별자)
    run_description=None,
    run_metadata=None,
)
```

### 2.4 Tracing API 활용 (Langfuse Python SDK v3)

> **v3 주의**: Langfuse Python SDK v3는 OpenTelemetry 기반으로 재설계되어 v2의 `langfuse.trace()` / `trace.generation()` / `langfuse.score()` API가 제거되었다. v3에서는 `start_as_current_observation()` 컨텍스트 매니저와 `span.score()` / `langfuse.create_score()`를 사용한다. Trace는 root observation(span)으로부터 암묵적으로 생성된다.

#### Trace 생성 (실험 실행 단위)
```python
with langfuse.start_as_current_observation(
    name="experiment-run",
    as_type="span",
    input={"experiment_id": experiment_id},
    metadata={
        "experiment_name": "...",
        "prompt_name": "...",
        "prompt_version": 3,
        "model": "gpt-4o",
        "parameters": {"temperature": 0.1, ...}
    }
) as root_span:
    # trace 수준 속성 전파 (v3 canonical): propagate_attributes 컨텍스트 사용
    with langfuse.propagate_attributes(
        user_id=user_id,
        session_id=experiment_id,
        tags=["ax-eval", "batch-experiment", experiment_name, model],
        trace_name=f"experiment:{experiment_name}",
    ):
        trace_id = root_span.trace_id
        # 이 블록 안에서 시작되는 모든 하위 span은 user_id/session_id/tags를 상속
        run_experiment(root_span, ...)
```

> **session/user 식별자 전파**: `propagate_attributes()`가 v3 권장 방식이다. `as_baggage=True`로 OTel baggage 헤더에 실어 LiteLLM Proxy 등 downstream 서비스로 분산 전파할 수 있다. 단일 span만 갱신할 때는 `langfuse.update_current_trace(user_id=..., session_id=..., tags=[...])`를 사용한다.

#### `@observe` 데코레이터 패턴 (권장)
라우터/서비스 함수 단위 트레이싱은 컨텍스트 매니저 대신 `@observe`를 사용해 보일러플레이트를 줄인다.

```python
from langfuse import observe, get_client

langfuse = get_client()

@observe(name="run-single-test", capture_input=False, capture_output=False)
async def run_single_test(user_id: str, payload: SingleTestRequest) -> SingleTestResult:
    # PII 보호: capture_input/capture_output=False로 raw 프롬프트/응답 자동 캡처 비활성화
    langfuse.update_current_trace(
        user_id=user_id,
        session_id=f"single_{user_id}_{int(time.time())}",
        tags=["ax-eval", "single-test", payload.model],
        metadata={"prompt_name": payload.prompt_name, "prompt_version": payload.prompt_version},
    )
    ...
```

- `capture_input/capture_output`은 **기본 False로 강제**한다. 프롬프트/모델 출력에 PII가 포함될 수 있으므로 masking 후 `update()`로 명시 기록한다.
- 예외는 SDK가 자동으로 span status=ERROR로 마킹하고 exception 이벤트를 첨부한다. 사용자 코드는 일반적인 try/except로 도메인 처리만 담당하고 재-raise 하면 된다.

**v3 `@observe` 주의사항**:
- **Async generator / 스트리밍**: `@observe`는 async generator(`async def ... yield`)와 FastAPI `StreamingResponse`/SSE 핸들러에서 span 종료 시점을 자동 추론하지 못한다. generator 함수 자체에 데코레이터를 달지 말고, 내부에 `async with langfuse.start_as_current_observation(...) as span:` 블록을 열어 첫 yield 전에 시작하고 generator가 완전히 소진되거나 취소될 때 `finally`에서 `span.update(output=accumulated)` 후 블록 종료를 보장한다. 클라이언트 조기 disconnect 대비 `try/finally`로 `langfuse.flush()` 호출.
- **FastAPI 엔드포인트**: 응답 반환 직후 ASGI lifespan이 아닌 요청 스코프가 종료되므로 BatchSpanProcessor가 아직 전송 전일 수 있다. 장시간 실행 엔드포인트는 `BackgroundTasks`에 `langfuse.flush`를 등록하거나 미들웨어에서 응답 후 flush를 트리거. 단, 매 요청 flush는 지연을 유발하므로 배치 실험처럼 끝이 명확한 엔드포인트에만 적용.
- **동시성 컨텍스트 전파**: `asyncio.gather`, `run_in_threadpool`에서 OTel context가 자동 전파되지 않을 수 있다. `contextvars.copy_context()` 또는 `langfuse.start_as_current_observation(...).otel_context` 명시 전달로 부모-자식 관계 보존.
- **데코레이터 중첩 한계**: 동일 함수에 `@observe`와 `@functools.wraps`가 아닌 커스텀 데코레이터를 혼용하면 signature inspection이 깨져 input 캡처가 실패한다. `@observe`를 가장 바깥에 둔다.

#### 예외 처리 정책
```python
try:
    with langfuse.start_as_current_observation(name="llm-call", as_type="generation", ...) as gen:
        response = await litellm_call(...)
        gen.update(output=mask_pii(response.content), usage_details=..., cost_details=...)
except LLMProviderError as e:
    # SDK가 span에 exception 기록 → 추가로 trace 상태를 metadata에 보강
    langfuse.update_current_trace(metadata={"status": "failed", "error_class": type(e).__name__})
    raise  # 재-raise 하여 상위에서 도메인 처리
```
- 민감 정보(스택트레이스 내 토큰/키)는 절대 metadata에 직접 기록하지 않는다.
- 재시도는 상위 레벨에서 수행하고, 각 시도를 별도 generation span으로 기록해 재시도 횟수가 ClickHouse에서 집계되도록 한다.

#### Generation 기록 (LLM 호출 단위)
```python
with root_span.start_as_current_observation(
    name="llm-call",
    as_type="generation",
    model="gpt-4o",
    model_parameters={"temperature": 0.1},
    input=messages,
    prompt=prompt_client  # Langfuse prompt 객체를 직접 연결 (선택)
) as generation:
    # LLM 호출 후 update()로 output, usage, cost 기록
    generation.update(
        output=response_content,
        usage_details={
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens
        },
        cost_details={"total": total_cost_usd},
        metadata={"provider": "azure", "region": "eastus"}
    )
```

> **v3 필드 변경**: v2의 `usage={"input","output","total","unit"}`는 v3에서 `usage_details={"input","output","total"}`로 이름이 바뀌었고, 비용은 `cost_details={"total": ...}`로 분리되었다. `unit` 필드는 제거되었다(토큰 단위 기본).

#### Score 기록
활성 observation 내부에서 기록하거나, trace_id/observation_id를 알고 있으면 외부에서도 기록 가능.

```python
from langfuse.api import ScoreDataType

# (a) 활성 span에서 observation/trace 스코어
generation.score(
    name="exact_match",
    value=1.0,
    data_type=ScoreDataType.NUMERIC,      # NUMERIC | BOOLEAN | CATEGORICAL
    comment="정확히 일치"
)
generation.score_trace(name="user_satisfaction", value=5.0, data_type=ScoreDataType.NUMERIC)

# (b) 외부(비동기 평가 등)에서 기존 trace에 스코어 부여
langfuse.create_score(
    trace_id=trace_id,
    observation_id=generation_id,         # 선택: 특정 observation에만 부여
    name="exact_match",
    value=1.0,
    data_type=ScoreDataType.NUMERIC,
    comment="정확히 일치"
)
```

> **문서 간 일관성**: EVALUATION.md / FEATURES.md / BUILD_ORDER.md / TEST_SPEC_PART2.md는 가독성을 위해 `langfuse.score(trace_id, name, value)` 의사코드를 사용한다. 실제 코드에서는 위의 `create_score()` / `span.score()` 호출로 매핑된다.

#### Score Config (Schema) 사전 등록 — v3 필수

v3는 score 이름/타입/허용 범위를 사전 정의하는 **Score Config**를 지원한다. 사전 등록된 score만 기록하도록 강제하면 (a) 오타로 인한 score name 분기, (b) data_type 불일치(NUMERIC vs CATEGORICAL), (c) 허용 범위 밖의 값 기록을 차단할 수 있다.

```python
from langfuse.api import ScoreDataType

# 부팅 시 1회: Labs Backend가 evaluator 카탈로그를 순회하며 score config 등록 (idempotent)
# REST: POST /api/public/score-configs
langfuse.api.score_configs.create(
    name="exact_match",
    data_type=ScoreDataType.NUMERIC,
    min_value=0.0,
    max_value=1.0,
    description="Exact string match (1.0=hit, 0.0=miss)",
)

langfuse.api.score_configs.create(
    name="llm_judge_accuracy",
    data_type=ScoreDataType.NUMERIC,
    min_value=0.0,
    max_value=1.0,
    description="LLM-as-judge accuracy on a 5-step rubric",
)

langfuse.api.score_configs.create(
    name="answer_category",
    data_type=ScoreDataType.CATEGORICAL,
    categories=[
        {"label": "correct", "value": 1},
        {"label": "partial", "value": 0.5},
        {"label": "wrong",   "value": 0},
    ],
    description="Categorical answer grading",
)

# 조회 (UI 드롭다운 / evaluator 검증용)
configs = langfuse.api.score_configs.get()
allowed_score_names = {c.name: c for c in configs.data}
```

**Labs Backend 강제 정책**:
- `services/score_registry.py`에 evaluator 카탈로그(`{name, data_type, range/categories}`)를 단일 소스로 정의하고, 부팅 시 Langfuse score config와 비교 → 누락 시 자동 등록, 불일치 시 startup 실패.
- `create_score()` 호출 직전에 `score_registry.validate(name, value, data_type)`를 통과시켜 미등록/범위 초과 score를 차단.
- 새 evaluator 추가 PR에는 score config 등록 마이그레이션을 함께 포함, 운영 환경에 dry-run으로 적용 후 머지.

### 2.5 LiteLLM + Langfuse 자동 연동

**LiteLLM의 Langfuse callback은 비활성화한다.**

Labs Backend가 trace/generation 기록을 전담하므로 LiteLLM의 자동 callback을 사용하면 중복 기록이 발생한다.

```
LiteLLM Proxy 설정:
  litellm_settings:
    success_callback: []    # Langfuse callback 비활성화
```

**비용/토큰 추적 방법**:
- LiteLLM 응답의 `usage` 필드에서 input_tokens, output_tokens 추출
- `litellm.completion_cost(response)` 함수로 비용 계산
- Labs Backend가 이 값을 Langfuse generation의 `usage_details`/`cost_details`에 기록 (v3 필드명)
- 토큰/비용 계산 실패 시에도 generation은 남기되 `metadata.usage_missing=true`로 마킹하여 ClickHouse 집계에서 제외 가능하도록 한다

**PII/민감정보 마스킹**:
- 프롬프트 입력(`messages`)과 모델 출력(`response.content`)은 PII 포함 가능 → Backend에서 `mask_pii()` 적용 후 `input`/`output`에 기록
- API 키, Authorization 헤더, 시스템 프롬프트 내 시크릿은 기록 금지
- 민감 데이터셋의 경우 `update_current_trace(metadata={"pii_masked": True})`로 감사 추적

---

## 3. ClickHouse 직접 쿼리

> ⚠ 아래 쿼리의 테이블명과 컬럼명은 Langfuse v3의 ClickHouse 스키마 예시이다. 실제 배포 후 `SHOW CREATE TABLE traces`, `SHOW CREATE TABLE observations` 등으로 스키마를 확인하고 쿼리를 조정해야 한다.

### 3.1 사용 목적
Langfuse SDK/API로는 복잡한 집계 쿼리가 불가능하거나 비효율적인 경우, ClickHouse에 직접 쿼리한다.

### 3.2 주요 쿼리 패턴

#### 실험 간 요약 비교
```sql
SELECT
    JSONExtractString(t.metadata, 'run_name') AS run_name,
    JSONExtractString(t.metadata, 'model') AS model,
    JSONExtractInt(t.metadata, 'prompt_version') AS prompt_version,
    count(*) AS sample_count,
    avg(o.latency_ms) AS avg_latency_ms,
    quantile(0.5)(o.latency_ms) AS p50_latency,
    quantile(0.9)(o.latency_ms) AS p90_latency,
    quantile(0.99)(o.latency_ms) AS p99_latency,
    sum(o.cost_usd) AS total_cost,
    avg(o.usage_input_tokens) AS avg_input_tokens,
    avg(o.usage_output_tokens) AS avg_output_tokens
FROM traces t
JOIN observations o ON t.id = o.trace_id
WHERE t.project_id = {project_id:String}
  AND has(t.tags, 'batch-experiment')
  AND JSONExtractString(t.metadata, 'experiment_name') = {experiment_name:String}
GROUP BY run_name, model, prompt_version
ORDER BY avg_latency_ms ASC
```

#### 평가 스코어 비교
```sql
SELECT
    JSONExtractString(t.metadata, 'run_name') AS run_name,
    s.name AS score_name,
    avg(s.value) AS avg_score,
    min(s.value) AS min_score,
    max(s.value) AS max_score,
    stddevPop(s.value) AS score_stddev,
    count(*) AS scored_count
FROM traces t
JOIN scores s ON t.id = s.trace_id
WHERE t.project_id = {project_id:String}
  AND JSONExtractString(t.metadata, 'experiment_name') = {experiment_name:String}
GROUP BY run_name, score_name
ORDER BY run_name, score_name
```

#### 스코어 차이가 큰 아이템 (Outlier 감지)
```sql
WITH run_scores AS (
    SELECT
        dri.dataset_item_id,
        JSONExtractString(t.metadata, 'run_name') AS run_name,
        s.value AS score
    FROM dataset_run_items dri
    JOIN traces t ON dri.trace_id = t.id
    JOIN scores s ON t.id = s.trace_id
    WHERE s.name = {score_name:String}
      AND JSONExtractString(t.metadata, 'experiment_name') = {experiment_name:String}
)
SELECT
    dataset_item_id,
    max(score) - min(score) AS score_range,
    groupArray(run_name) AS runs,
    groupArray(score) AS scores
FROM run_scores
GROUP BY dataset_item_id
HAVING score_range > 0.3
ORDER BY score_range DESC
LIMIT 20
```

#### 비용 효율 분석
```sql
SELECT
    JSONExtractString(t.metadata, 'run_name') AS run_name,
    JSONExtractString(t.metadata, 'model') AS model,
    avg(s.value) AS avg_score,
    sum(o.cost_usd) AS total_cost,
    avg(s.value) / nullIf(sum(o.cost_usd), 0) AS score_per_dollar
FROM traces t
JOIN observations o ON t.id = o.trace_id
JOIN scores s ON t.id = s.trace_id
WHERE JSONExtractString(t.metadata, 'experiment_name') = {experiment_name:String}
  AND s.name = {primary_score_name:String}
GROUP BY run_name, model
ORDER BY score_per_dollar DESC
```

#### 스코어 분포 히스토그램
```sql
SELECT
    least(floor(s.value * {bins:UInt8}), {bins:UInt8} - 1) / {bins:UInt8} AS bin_start,
    least(floor(s.value * {bins:UInt8}), {bins:UInt8} - 1) / {bins:UInt8} + 1.0 / {bins:UInt8} AS bin_end,
    count(*) AS count
FROM traces t
JOIN scores s ON t.id = s.trace_id
WHERE t.project_id = {project_id:String}
  AND JSONExtractString(t.metadata, 'run_name') = {run_name:String}
  AND s.name = {score_name:String}
GROUP BY bin_start, bin_end
ORDER BY bin_start ASC
```

> **NOTE**: `least(..., bins-1)` 클램핑으로 score=1.0이 마지막 bin에 포함되도록 한다. bins=1이면 전체가 하나의 bin [0.0, 1.0]이 된다.

#### 지연 시간 분포 (P50/P90/P99 + histogram)
```sql
SELECT
    JSONExtractString(t.metadata, 'run_name') AS run_name,
    avg(o.latency_ms) AS avg_latency,
    quantile(0.5)(o.latency_ms) AS p50,
    quantile(0.9)(o.latency_ms) AS p90,
    quantile(0.99)(o.latency_ms) AS p99,
    min(o.latency_ms) AS min_latency,
    max(o.latency_ms) AS max_latency,
    count(*) AS sample_count
FROM traces t
JOIN observations o ON t.id = o.trace_id
WHERE t.project_id = {project_id:String}
  AND JSONExtractString(t.metadata, 'run_name') IN ({run_names:Array(String)})
GROUP BY run_name
```

히스토그램은 추가 쿼리로:
```sql
SELECT
    run_name,
    floor(latency_ms / bin_width) * bin_width AS bin_start,
    count(*) AS count
FROM (
    SELECT
        JSONExtractString(t.metadata, 'run_name') AS run_name,
        o.latency_ms,
        (max(o.latency_ms) OVER () - min(o.latency_ms) OVER ()) / {bins:UInt8} AS bin_width
    FROM traces t
    JOIN observations o ON t.id = o.trace_id
    WHERE t.project_id = {project_id:String}
      AND JSONExtractString(t.metadata, 'run_name') IN ({run_names:Array(String)})
)
GROUP BY run_name, bin_start
ORDER BY run_name, bin_start ASC
```

#### 비용 분포
```sql
SELECT
    JSONExtractString(t.metadata, 'run_name') AS run_name,
    sum(o.cost_usd) AS total_cost,
    avg(o.cost_usd) AS avg_cost,
    quantile(0.5)(o.cost_usd) AS p50_cost,
    quantile(0.9)(o.cost_usd) AS p90_cost,
    quantile(0.99)(o.cost_usd) AS p99_cost,
    count(*) AS sample_count
FROM traces t
JOIN observations o ON t.id = o.trace_id
WHERE t.project_id = {project_id:String}
  AND JSONExtractString(t.metadata, 'run_name') IN ({run_names:Array(String)})
GROUP BY run_name
```

**쿼리 캐싱**: 위 분포 쿼리(스코어/지연/비용)는 ClickHouse 집계 비용이 크므로 Redis에 5분 TTL 캐시. 캐시 키: `ax:cache:dist:{sha1(project_id+run_names+bins+score_name)}`.

### 3.3 ClickHouse 연결 설정

```
연결 정보:
- host: ClickHouse 서버 (Langfuse와 동일 인스턴스)
- port: 8123 (HTTPS) — 평문 9000/8123 금지, TLS 필수
- database: langfuse (Langfuse가 사용하는 DB)
- user: labs_readonly (전용 읽기 전용 계정, Langfuse 애플리케이션 계정 재사용 금지)
- 드라이버: clickhouse-connect (Python)
```

**읽기 전용 계정 생성 (필수)**:
```sql
-- 별도 role로 권한 최소화
CREATE USER labs_readonly IDENTIFIED WITH sha256_password BY '<secret>'
    HOST IP '10.0.0.0/8'                -- 네트워크 ACL
    DEFAULT DATABASE langfuse
    SETTINGS readonly = 2,              -- readonly=2: SELECT + SET 허용, DDL/DML 차단
             max_execution_time = 30,
             max_result_rows = 100000,
             max_memory_usage = 2000000000;

GRANT SELECT ON langfuse.traces TO labs_readonly;
GRANT SELECT ON langfuse.observations TO labs_readonly;
GRANT SELECT ON langfuse.scores TO labs_readonly;
GRANT SELECT ON langfuse.dataset_run_items TO labs_readonly;

REVOKE INSERT, ALTER, DROP, CREATE, TRUNCATE, OPTIMIZE ON *.* FROM labs_readonly;
```

- 계정 자격증명은 Vault/AWS Secrets Manager에서 주입, 환경변수는 컨테이너 스코프로 한정
- 정기 로테이션 (90일) 및 쿼리 감사 로그(`system.query_log`) 모니터링 필수
- 커넥션 풀 상한 설정 (기본 10) — 배치 실험이 분석 쿼리를 고사시키지 않도록 격리

### 3.4 주의사항

- ClickHouse 스키마는 Langfuse 버전에 따라 변경될 수 있음 → 마이그레이션 대응 필요
- 읽기 전용 접근만 수행, 절대 데이터를 직접 INSERT/UPDATE/DELETE 하지 않음
- 대량 쿼리 시 LIMIT 필수, 쿼리 래퍼에서 LIMIT 없는 쿼리 거부 (기본 LIMIT 10,000 자동 추가)
- 파라미터화된 쿼리(parameterized query) 필수 — 문자열 보간(f-string, .format()) 금지
- Langfuse API로 가능한 조회는 API 사용 우선, ClickHouse는 복잡한 집계에만 사용

---

## 4. Langfuse 메타데이터 전략

### 4.1 Trace Metadata 스키마

Labs에서 생성하는 모든 trace에 일관된 메타데이터를 부여하여 추후 분석에 활용한다.

```json
{
    "source": "ax-llm-eval-workflow",
    "experiment_type": "single_test | batch_experiment",
    "experiment_name": "실험 이름",
    "experiment_id": "고유 실험 ID (UUID)",
    "run_name": "Run 식별자 (experiment_name + model + timestamp)",
    "prompt_name": "Langfuse 프롬프트 이름",
    "prompt_version": 3,
    "model": "gpt-4o",
    "model_provider": "azure",
    "parameters": {
        "temperature": 0.1,
        "max_tokens": 1024
    },
    "dataset_name": "데이터셋 이름 (배치 실험만)",
    "evaluators": ["exact_match", "llm_judge_accuracy"]
}
```

### 4.2 Tags 전략

```
태그 구조:
- "ax-eval"                    → Labs에서 생성한 모든 trace
- "single-test" | "batch"     → 실험 유형
- "{experiment_name}"          → 실험별 필터링
- "{model}"                    → 모델별 필터링
```

### 4.3 Session 전략

```
session_id 구조:
- 단일 테스트: "single_{user_id}_{timestamp}"
- 배치 실험: "batch_{experiment_id}"
- 동일 세션의 trace들은 Langfuse UI에서 그룹으로 조회 가능
```

`session_id` / `user_id`는 반드시 `langfuse.propagate_attributes()` (권장) 또는 `langfuse.update_current_trace()`로 설정한다. 자식 span에 하드코딩하지 말고 부모 span 생성 직후 한 번만 설정하여 모든 하위 observation이 자동 상속하도록 한다.

---

## 5. 연동 시 고려사항

### 5.1 Rate Limiting & Flush 정책
- Langfuse API에는 rate limit이 존재 (셀프호스팅 시 설정 가능)
- 배치 실험에서 대량의 trace/score를 빠르게 기록할 때 주의
- v3 SDK는 OTel BatchSpanProcessor 기반 비동기 전송 (기본 큐잉/배치)
- `langfuse.flush()`: 배치 경계(예: 50 아이템마다), 실험 완료 시, FastAPI lifespan shutdown에서 호출
- `langfuse.shutdown()`: 프로세스 종료 직전 1회, 멀티워커 환경은 워커별로 호출
- 큐 overflow 대비: `sample_rate` 조정 또는 Langfuse 큐 크기 상향

### 5.2 데이터 정합성
- trace 생성 → LLM 호출 → generation 기록 → score 기록 순서 보장
- LLM 호출 실패 시에도 trace는 남기되, 실패 상태를 metadata에 기록
- score 기록 실패 시 재시도 로직 (최대 3회)

### 5.3 멀티 프로젝트 / 조직 / SSO / 팀 관리
- Langfuse 프로젝트 = 서비스/팀 단위, 조직(Organization) → 프로젝트 → 멤버의 3계층 구조
- Labs UI에서 프로젝트 선택 → 해당 프로젝트의 API Key로 전환
- 프로젝트별 프롬프트, 데이터셋, 실험 결과가 격리됨

**SSO/팀 관리 정책 (셀프호스트)**:
- 사내 IdP(Okta/Azure AD/Google Workspace)와 OIDC 연동: `AUTH_<PROVIDER>_CLIENT_ID/SECRET/ISSUER` 환경변수로 활성화. 비밀번호 로그인은 `AUTH_DISABLE_USERNAME_PASSWORD=true`로 차단하여 SSO만 허용.
- 자동 가입은 `AUTH_<PROVIDER>_ALLOW_ACCOUNT_LINKING=true` + 도메인 화이트리스트(`AUTH_DOMAINS_WITH_SSO_ENFORCEMENT`)로 사내 도메인만 허용. 외부 이메일은 차단.
- 조직 멤버십/역할(Owner/Admin/Member/Viewer)은 Langfuse 자체 관리. SCIM 미지원이므로 입퇴사자 동기화는 (a) IdP에서 OIDC 그룹 차단 + (b) 주 1회 cron으로 Langfuse Admin API(`/api/public/projects/{id}/memberships`)와 IdP 그룹을 비교하여 비활성 사용자 제거.
- Labs Backend는 Langfuse 사용자를 직접 관리하지 않는다. 사내 Auth JWT의 `email`/`groups` 클레임을 Langfuse 프로젝트 권한 매트릭스에 매핑하여 `(public_key, secret_key)` 쌍을 결정한다.
- 프로덕션 프로젝트의 secret_key는 admin/owner role만 접근 가능하도록 Vault ACL로 분리. 일반 개발자는 staging 프로젝트 키만 부여.
- 감사: Langfuse audit log(`audit_logs` 테이블) + 자체 Auth 로그를 30일 이상 보관, SSO 로그인 실패율과 권한 변경 이벤트를 SIEM으로 전송.

### 5.4 Ingestion 지연 & Eventual Consistency
Langfuse v3는 SDK → OTel collector → Redis 큐 → Worker → ClickHouse 파이프라인이며, **trace 기록 직후 즉시 조회되지 않는다**. Labs는 이를 명시적으로 다뤄야 한다.

**지연 특성**:
- SDK BatchSpanProcessor flush 주기: 기본 1초 (또는 큐 가득 시)
- Worker → ClickHouse insert: 보통 1~5초, 부하 시 수십 초까지 지연 가능
- ClickHouse는 ReplacingMergeTree/AggregatingMergeTree 기반 → `FINAL` 없이는 동일 trace의 최신 상태가 즉시 보이지 않을 수 있음
- Score는 trace보다 늦게 도착할 수 있어 join 결과가 일시적으로 비어 있을 수 있음

**Labs 대응 정책 (필수)**:
- **Read-after-write 금지 패턴**: 실험 실행 직후 ClickHouse에서 결과 조회 → 빈 결과/부분 결과 발생. 대신 (a) Labs Backend가 SDK 호출 시점의 응답/스코어를 메모리/Redis에 임시 보관, (b) UI는 Redis의 in-flight 데이터 + ClickHouse의 영속 데이터를 union하여 표시, (c) 백그라운드 reconciler가 ClickHouse에서 동일 trace_id를 발견하면 Redis 캐시를 만료.
- **폴링 백오프**: 실험 완료 후 결과 페이지는 `1s → 2s → 5s → 10s → 30s` 지수 백오프로 ClickHouse를 폴링. 60초 내 미발견 시 "ingestion 지연 중" 배너 표시.
- **쿼리 시 `FINAL` 선택적 사용**: 최신 상태가 중요한 단일 trace 조회에만 `SELECT ... FROM traces FINAL WHERE id = ...` 사용. 집계 쿼리는 FINAL 금지(성능 비용 큼).
- **score join NULL 허용**: `LEFT JOIN scores` 사용, NULL이면 "평가 진행 중"으로 표시. INNER JOIN은 score 누락된 trace를 통째로 숨기므로 금지.
- **flush 강제**: 배치 실험 완료 직후 `langfuse.flush()` → 5초 대기 → 결과 페이지 리다이렉트. 이 5초 sleep은 최소한이며 보장은 아니다.
- **trace_id 사전 생성**: SDK가 발급한 trace_id를 즉시 Redis에 기록하여 UI가 ClickHouse에 도달하기 전에도 "예정된" trace 목록을 보여줄 수 있게 한다.

### 5.5 Trace/Observation 크기 상한 & Attachment 정책
Langfuse는 trace/observation의 input/output을 PostgreSQL/ClickHouse에 직접 저장하므로 대용량 페이로드는 ingestion 실패와 ClickHouse 메모리 폭주를 유발한다.

**크기 상한 (Labs 강제)**:
- **단일 observation input/output**: 최대 1MB (직렬화 후). 초과 시 잘라내고 `metadata.truncated=true`, `metadata.original_size_bytes`를 기록.
- **단일 trace 누적**: 모든 observation의 input/output 합 ≤ 10MB. 초과 예상 시 input/output을 attachment(외부 blob)로 분리.
- **metadata**: ≤ 32KB. 큰 컨텍스트(retrieved documents 등)는 metadata가 아닌 attachment로.
- **tags**: 개당 ≤ 64자, trace당 ≤ 20개.
- **score comment**: ≤ 1KB.

**Labs Backend 강제 위치**:
- `services/langfuse_client.py`에 `_truncate_payload(value, max_bytes)` 헬퍼를 두고 모든 `update(input=..., output=...)` 호출 전에 통과시킨다.
- 초과 시 (a) 잘라내거나, (b) 자동으로 S3/GCS attachment로 업로드하고 trace에는 `{"_attachment_url": "...", "_size": N}` 참조만 기록.
- 직렬화 실패(circular ref, non-JSON) 대비: `default=str` + 최대 깊이 5 제한.

**Attachment(미디어/대용량 페이로드) 정책**:
- 대용량 입력(첨부 파일, 검색 결과, 이미지)은 trace 본문에 임베드하지 않고 Langfuse Media API(`/api/public/media`) 또는 Labs 자체 S3 버킷에 업로드한 후 URL/ID를 metadata에 저장.
- Langfuse Media API 사용 시: SDK가 자동 업로드하는 base64 이미지/파일도 동일 사이즈 게이트(1MB/file, trace당 5개)를 적용. 초과 시 사전 업로드 후 URL만 전달.
- Labs 자체 attachment 버킷: `s3://ax-labs-attachments/{project_id}/{trace_id}/{uuid}.{ext}`, presigned URL TTL 7일, 접근 시 Labs Backend에서 JWT 검증 후 재발급.
- PII가 포함될 수 있는 attachment는 별도 KMS 키로 SSE-KMS 암호화, 보존 기간 30일 후 자동 삭제(S3 lifecycle).
- attachment 삭제와 trace 삭제는 별개 워크플로우 → trace 삭제 시 `metadata._attachment_url`을 스캔하여 함께 삭제하는 cleanup job 운영.
- **attachment 비용 추적**: LiteLLM `completion_cost()`는 LLM 토큰 비용만 산출하므로, Media API/S3 storage·egress 비용은 별도 집계한다. 업로드 시 Backend가 `(project_id, trace_id, bytes, storage_class)`를 OBSERVABILITY 메트릭(`ax_attachment_bytes_total{project_id, storage_class}`)으로 기록하고, 월 1회 S3 Cost & Usage Report와 대조해 프로젝트별 attachment 비용을 산출, Langfuse generation의 `cost_details`와는 분리된 별도 대시보드로 노출한다.

### 5.6 Langfuse Blob Storage (S3/GCS/MinIO) 구성
Langfuse v3 Worker는 raw event/media를 ClickHouse 적재 전 blob storage에 임시 보관한다. 셀프호스트 운영 시 필수 설정.

**필수 환경변수 (Langfuse Worker/Web)**:
```
# Event blob (필수, 모든 ingestion 이벤트의 원본 보관)
LANGFUSE_S3_EVENT_UPLOAD_ENABLED=true
LANGFUSE_S3_EVENT_UPLOAD_BUCKET=langfuse-events-prod
LANGFUSE_S3_EVENT_UPLOAD_REGION=ap-northeast-2
LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT=https://s3.ap-northeast-2.amazonaws.com  # MinIO 시 내부 endpoint
LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE=false                            # MinIO 시 true
LANGFUSE_S3_EVENT_UPLOAD_PREFIX=events/
LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID=<from vault>
LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY=<from vault>

# Media blob (이미지/오디오/파일 attachment)
LANGFUSE_S3_MEDIA_UPLOAD_ENABLED=true
LANGFUSE_S3_MEDIA_UPLOAD_BUCKET=langfuse-media-prod
LANGFUSE_S3_MEDIA_UPLOAD_REGION=ap-northeast-2
LANGFUSE_S3_MEDIA_UPLOAD_PREFIX=media/
LANGFUSE_S3_MEDIA_MAX_CONTENT_LENGTH=10485760   # 10MB

# Batch export (UI 다운로드용)
LANGFUSE_S3_BATCH_EXPORT_ENABLED=true
LANGFUSE_S3_BATCH_EXPORT_BUCKET=langfuse-exports-prod
LANGFUSE_S3_BATCH_EXPORT_PREFIX=exports/
```

**GCS 사용 시**:
- S3 호환 인터페이스(HMAC 키)로 동일 변수 사용. `LANGFUSE_S3_*_ENDPOINT=https://storage.googleapis.com`, `FORCE_PATH_STYLE=true`.
- 또는 GCS native: 워크로드 ID(GKE) + 서비스 계정 권한 위임으로 키리스 인증 권장.

**버킷 정책 (필수)**:
- **암호화**: SSE-KMS 필수, 별도 CMK 사용. `aws:kms` 정책 필수, 비암호화 PUT 거부.
- **퍼블릭 차단**: Block Public Access 4종 모두 ON, presigned URL만 외부 노출.
- **버전 관리**: events 버킷은 비활성(비용), media/exports는 활성화 (실수 삭제 복구).
- **lifecycle**:
  - events: 7일 후 삭제 (ClickHouse 적재 완료 후 불필요)
  - media: 90일 후 Glacier, 1년 후 삭제
  - exports: 14일 후 삭제
- **CORS**: media/exports만 Labs 프론트 도메인 화이트리스트, events는 CORS 비허용.
- **IAM**: Langfuse Worker는 events/media에 PutObject + GetObject + DeleteObject, Web은 GetObject + presigned URL 발급만. ListBucket은 운영자 role에만.

**Labs Backend 검증**:
- 부팅 시 헬스체크에서 events/media 버킷에 테스트 객체 PUT/GET → 권한·네트워크 사전 검증.
- Worker 로그에서 `Failed to upload event to S3` 에러율을 메트릭으로 수집, 1% 초과 시 알림.
- ClickHouse 적재가 지연될 때 events 버킷의 객체 수를 모니터링(이벤트 백로그 지표).

### 5.7 Langfuse 버전 호환성 & 셀프호스트 업그레이드 정책

**API Key 로테이션 절차 (public_key/secret_key, 90일 주기)**:
1. Langfuse UI 또는 `/api/public/projects/{id}/api-keys` POST로 신규 키 발급(기존 키와 병행 활성). 2. Vault에 `langfuse/{project}/next` 경로로 신규 키 적재 → Labs Backend는 부팅 시 `current` + `next` 모두 로드. 3. 카나리 인스턴스에서 신규 키로 trace 1건 기록 → ClickHouse 도달 확인. 4. 전체 워크로드를 신규 키로 롤링 재시작(30분 간격, 워커별). 5. 24시간 bake-in 후 구 키 revoke + Vault `current` 승격. 6. 유출 의심 시 즉시 revoke → 신규 발급의 응급 경로(Runbook `INCIDENT-LF-KEY`)는 1~5단계를 30분 내 압축 실행.

**백업 / RPO·RTO 목표**:
- **PostgreSQL(메타데이터)**: WAL 아카이빙 + 매시 base backup, **RPO 5분 / RTO 30분**. 보관 30일, 주 1회 cold 사본을 별도 리전.
- **ClickHouse(traces/observations/scores)**: 매일 `BACKUP DATABASE langfuse` 풀 + 매시 incremental, **RPO 1시간 / RTO 2시간**. 이벤트 원본은 S3 events 버킷(7일)이 보조 복구원이므로 ClickHouse 손실 시 events 재처리로 최대 7일 복원 가능.
- 분기별 복구 훈련(스테이징에 실제 복원 → 쿼리 회귀 스위트 통과)을 의무화하고 결과를 `docs/runbooks/lf-restore.md`에 기록.

**SDK/서버 버전 매트릭스 갱신 정책**:
- Langfuse Python SDK v3.x ↔ Langfuse Server v3.x만 지원. 서버 v2 인스턴스에는 v3 SDK 연결 금지(OTel 엔드포인트 불일치).
- `requirements.txt`에 `langfuse>=3.0,<4.0`로 상한 고정, 마이너 업그레이드는 CI smoke test 통과 후에만 반영.
- 매트릭스는 `docs/COMPATIBILITY_MATRIX.md`에 단일 표로 유지하고, Langfuse 릴리스 감지 cron(주 1회 GitHub Releases watcher)이 신규 버전 발견 시 자동 PR로 행을 추가. PR에는 (a) 릴리스 노트 링크, (b) 스테이징 회귀 결과, (c) 영향받는 ClickHouse 쿼리 어댑터 목록을 체크리스트로 첨부해야 머지 가능.
- ClickHouse 쿼리 모듈은 `backend/app/services/clickhouse/queries/v3/`처럼 서버 메이저 버전별 디렉토리로 분리, 런타임에 `/api/public/health`의 version으로 라우팅.

**셀프호스트 업그레이드 절차 (필수)**:
1. **사전 점검**: 릴리스 노트의 breaking changes(ClickHouse 스키마/migration, OTel 스펙) 확인. 마이너 이상은 스테이징 먼저.
2. **백업**: PostgreSQL 덤프 + ClickHouse `BACKUP DATABASE langfuse TO Disk('backups', ...)`. 위 RPO/RTO 목표 충족 확인. 보관 30일.
3. **스키마 호환성 테스트**: 업그레이드 전 Labs의 ClickHouse 쿼리 회귀 스위트를 스테이징 Langfuse에 대해 실행(`pytest -m clickhouse_schema`). `SHOW CREATE TABLE traces/observations/scores/dataset_run_items` 출력 스냅샷을 이전 버전과 diff.
4. **블루/그린 업그레이드**: 새 버전 Langfuse를 별도 네임스페이스로 기동 → readonly 계정으로 Labs가 쿼리 검증 → DNS/ingress 스위치.
5. **롤백 계획**: ClickHouse 마이그레이션은 비가역일 수 있으므로, 실패 시 백업에서 복원 + 구버전 이미지 재기동 절차를 runbook에 문서화.
6. **SDK 업그레이드 게이트**: 서버 업그레이드 후 1주일 bake-in 기간을 두고 SDK를 올린다. SDK → 서버 순서의 업그레이드는 금지.

**쿼리 회귀 방지**:
- 모든 ClickHouse 쿼리는 `get_server_version()` 결과를 받아 버전별 구현을 선택하는 어댑터 패턴으로 작성.
- 스키마 변경 감지: 주 1회 cron으로 `system.columns` 해시를 수집, 이전 해시와 다르면 알림.
- Langfuse 업그레이드 PR에는 쿼리 호환성 테스트 결과 스크린샷 첨부 필수.
