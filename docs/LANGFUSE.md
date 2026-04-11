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

### 2.1 초기화

```
Langfuse Client 설정:
- LANGFUSE_SECRET_KEY: 프로젝트 시크릿 키
- LANGFUSE_PUBLIC_KEY: 프로젝트 퍼블릭 키
- LANGFUSE_HOST: Langfuse 서버 URL (셀프호스팅)
- 프로젝트별 키 관리: 멀티 프로젝트 지원 시 프로젝트 선택에 따라 키 전환
```

### 2.2 Prompt Management API 활용

#### 프롬프트 조회
```
GET /api/public/v2/prompts/{name}
- query params: version (int), label (string)
- 응답: name, version, prompt (text/chat), config, labels, tags
```

#### SDK 사용 패턴
```
langfuse.get_prompt(name, version=None, label=None)
  → ChatPromptClient 또는 TextPromptClient
  → .compile(**variables) 로 변수 바인딩
  → 바인딩된 프롬프트를 LLM에 전달
```

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

#### Dataset Run 연결
```
item.link(
    trace_or_observation,  # trace 또는 generation 객체
    run_name               # 실험 Run 이름 (고유 식별자)
)
```

### 2.4 Tracing API 활용

#### Trace 생성 (실험 실행 단위)
```
trace = langfuse.trace(
    name="experiment-run",
    user_id=user_id,
    session_id=experiment_id,
    metadata={
        "experiment_name": "...",
        "prompt_name": "...",
        "prompt_version": 3,
        "model": "gpt-4o",
        "parameters": {"temperature": 0.1, ...}
    },
    tags=["batch-experiment", "sentiment-analysis"]
)
```

#### Generation 기록 (LLM 호출 단위)
```
generation = trace.generation(
    name="llm-call",
    model="gpt-4o",
    model_parameters={"temperature": 0.1},
    input=messages,
    output=response_content,
    usage={
        "input": input_tokens,
        "output": output_tokens,
        "total": total_tokens,
        "unit": "TOKENS"
    },
    metadata={"provider": "azure", "region": "eastus"}
)
```

#### Score 기록
```
langfuse.score(
    trace_id=trace.id,
    name="exact_match",       # 평가 함수 이름
    value=1.0,                # 스코어 값
    comment="정확히 일치",     # 선택적 코멘트
    data_type="NUMERIC"       # NUMERIC 또는 CATEGORICAL
)
```

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
- Labs Backend가 이 값을 Langfuse generation의 usage 필드에 기록

---

## 3. ClickHouse 직접 쿼리

### 3.1 사용 목적
Langfuse SDK/API로는 복잡한 집계 쿼리가 불가능하거나 비효율적인 경우, ClickHouse에 직접 쿼리한다.

### 3.2 주요 쿼리 패턴

#### 실험 간 요약 비교
```sql
SELECT
    t.metadata['run_name'] AS run_name,
    t.metadata['model'] AS model,
    t.metadata['prompt_version'] AS prompt_version,
    count(*) AS sample_count,
    avg(o.latency_ms) AS avg_latency_ms,
    percentile(o.latency_ms, 0.5) AS p50_latency,
    percentile(o.latency_ms, 0.9) AS p90_latency,
    percentile(o.latency_ms, 0.99) AS p99_latency,
    sum(o.cost_usd) AS total_cost,
    avg(o.usage_input_tokens) AS avg_input_tokens,
    avg(o.usage_output_tokens) AS avg_output_tokens
FROM traces t
JOIN observations o ON t.id = o.trace_id
WHERE t.project_id = '{project_id}'
  AND has(t.tags, 'batch-experiment')
  AND JSONExtractString(t.metadata, 'experiment_name') = {experiment_name:String}
GROUP BY run_name, model, prompt_version
ORDER BY avg_latency_ms ASC
```

#### 평가 스코어 비교
```sql
SELECT
    t.metadata['run_name'] AS run_name,
    s.name AS score_name,
    avg(s.value) AS avg_score,
    min(s.value) AS min_score,
    max(s.value) AS max_score,
    stddevPop(s.value) AS score_stddev,
    count(*) AS scored_count
FROM traces t
JOIN scores s ON t.id = s.trace_id
WHERE t.project_id = '{project_id}'
  AND t.metadata['experiment_name'] = '{experiment_name}'
GROUP BY run_name, score_name
ORDER BY run_name, score_name
```

#### 스코어 차이가 큰 아이템 (Outlier 감지)
```sql
WITH run_scores AS (
    SELECT
        dri.dataset_item_id,
        t.metadata['run_name'] AS run_name,
        s.value AS score
    FROM dataset_run_items dri
    JOIN traces t ON dri.trace_id = t.id
    JOIN scores s ON t.id = s.trace_id
    WHERE s.name = '{score_name}'
      AND t.metadata['experiment_name'] = '{experiment_name}'
)
SELECT
    dataset_item_id,
    max(score) - min(score) AS score_variance,
    groupArray(run_name) AS runs,
    groupArray(score) AS scores
FROM run_scores
GROUP BY dataset_item_id
HAVING score_variance > 0.3
ORDER BY score_variance DESC
LIMIT 20
```

#### 비용 효율 분석
```sql
SELECT
    t.metadata['run_name'] AS run_name,
    t.metadata['model'] AS model,
    avg(s.value) AS avg_score,
    sum(o.cost_usd) AS total_cost,
    avg(s.value) / nullIf(sum(o.cost_usd), 0) AS score_per_dollar
FROM traces t
JOIN observations o ON t.id = o.trace_id
JOIN scores s ON t.id = s.trace_id
WHERE t.metadata['experiment_name'] = '{experiment_name}'
  AND s.name = '{primary_score_name}'
GROUP BY run_name, model
ORDER BY score_per_dollar DESC
```

### 3.3 ClickHouse 연결 설정

```
연결 정보:
- host: ClickHouse 서버 (Langfuse와 동일 인스턴스)
- port: 8123 (HTTP) 또는 9000 (Native)
- database: langfuse (Langfuse가 사용하는 DB)
- user: 읽기 전용 계정 필수 (GRANT SELECT ON langfuse.* TO labs_readonly)
- 드라이버: clickhouse-connect (Python)
```

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

---

## 5. 연동 시 고려사항

### 5.1 Rate Limiting
- Langfuse API에는 rate limit이 존재 (셀프호스팅 시 설정 가능)
- 배치 실험에서 대량의 trace/score를 빠르게 기록할 때 주의
- langfuse.flush()를 배치 단위로 호출 (아이템 10개마다 등)
- SDK의 비동기 전송 활용 (내부 큐잉)

### 5.2 데이터 정합성
- trace 생성 → LLM 호출 → generation 기록 → score 기록 순서 보장
- LLM 호출 실패 시에도 trace는 남기되, 실패 상태를 metadata에 기록
- score 기록 실패 시 재시도 로직 (최대 3회)

### 5.3 멀티 프로젝트 지원
- Langfuse 프로젝트 = 서비스/팀 단위
- Labs UI에서 프로젝트 선택 → 해당 프로젝트의 API Key로 전환
- 프로젝트별 프롬프트, 데이터셋, 실험 결과가 격리됨

### 5.4 Langfuse 버전 호환성
- v3 SDK 기준으로 개발
- ClickHouse 스키마 변경에 대비하여 쿼리를 별도 모듈로 분리
- Langfuse 업그레이드 시 쿼리 호환성 테스트 필수
