# API 설계

## 1. API 개요

### 기본 정보
- Base URL: `/api/v1`
- 인증: JWT Bearer Token
- Content-Type: `application/json`
- 스트리밍 응답: `text/event-stream` (SSE)

### 공통 응답 포맷
```json
{
    "status": "success | error",
    "data": { ... },
    "error": {
        "code": "ERROR_CODE",
        "message": "에러 메시지"
    }
}
```

### 1.1 공통 규칙 (Conventions)

**인증/권한(RBAC)**: 모든 엔드포인트는 JWT Bearer 인증을 요구한다(§1.5 `/health` 제외). 역할은 `admin | user | viewer`이며, 최소 권한 매핑의 단일 출처(single source of truth)는 **IMPLEMENTATION.md §4.3**이다. 본 문서의 개별 엔드포인트에 `권한:` 필드가 없으면 기본값은 `viewer`(조회) / `user`(쓰기)로 간주하고 §4.3 표를 우선한다.

**403 vs 404 정책 (정보 노출 방지)**:
- 인증 실패 → `401 AUTH_REQUIRED`
- 리소스 존재 + 권한 없음 → `403 FORBIDDEN`
- 리소스 없음 → `404 *_NOT_FOUND`
- **예외**: `viewer` 권한조차 없는 리소스(예: 타 사용자 알림, 타 프로젝트 실험)는 존재 여부를 드러내지 않기 위해 `404`로 통일 반환한다. `admin`만 `403`을 받을 수 있다.

**페이지네이션**: 모든 목록형 응답은 `items | <도메인명>`, `total`, `page`, `page_size` 필드를 포함한다. 쿼리 파라미터 기본값: `page=1`, `page_size=20`, 최대 `page_size=100`. 정렬은 `sort_by`, `sort_order=asc|desc`(기본 `desc`).

**Idempotency**: 부작용이 있는 POST(`/experiments`, `/tests/single`, `/datasets/upload`, `/datasets/from-items`, `/evaluators/submissions`)는 `Idempotency-Key` 헤더(UUIDv4 권장, 최대 128자)를 선택적으로 수용한다. 동일 키 + 동일 user_id 조합은 Redis(`ax:idem:{user_id}:{key}`, TTL 24h)에 최초 응답을 캐싱하여 재실행 대신 원본 응답을 반환한다. 본문이 다르면 `409 IDEMPOTENCY_CONFLICT`.

**SSE 포맷**: 스트리밍 엔드포인트(`text/event-stream`)는 다음 규약을 따른다.
- 각 이벤트에 단조 증가 `id:` 라인 포함(클라이언트 `Last-Event-ID` 재접속용)
- 초기 `retry: 3000` (ms) 권고
- 15초마다 `: heartbeat\n\n` 주석 전송
- 종결 이벤트: `event: done` 또는 `event: error` 이후 서버가 연결을 종료
- `Last-Event-ID` 헤더 수신 시 해당 id 이후 이벤트부터 재전송(upload/experiment stream)

**API 버전 관리**: URL prefix `v<major>`로 breaking change만 분기. 비파괴 변경은 기존 버전 내에서 추가. Deprecated 엔드포인트는 `Deprecation: true`, `Sunset: <RFC 3339 date>`, `Link: <...>; rel="successor-version"` 헤더를 최소 90일간 반환 후 제거한다.

**OpenAPI 스키마**: FastAPI가 `/api/v1/openapi.json`으로 자동 노출하며 `/api/v1/docs` (Swagger UI), `/api/v1/redoc` (ReDoc)에서 열람. 스펙은 CI에서 스키마 스냅샷 테스트로 고정(§TEST_SPEC 참조). 모든 Pydantic 모델은 OpenAPI 3.1 호환(nullable은 `type: [X, "null"]`로 표현)으로 생성되며, 응답 필드의 선택성은 아래 규칙을 따른다.

**필드 nullable/optional 규약**: 본 문서의 응답 스키마에서 각 필드는 다음 세 가지로 분류된다. OpenAPI 생성 시 Pydantic 모델에 동일하게 반영한다.
- **required (기본)**: 명시 없는 모든 필드는 응답에 항상 존재하며 `null` 불가
- **nullable**: 타입 옆 `| null` 또는 설명에 "nullable" 명시. 키는 항상 존재하되 값이 `null`일 수 있음 (예: `completed_at`, `rejection_reason`, `prompt_configs[].label`)
- **optional**: 설명에 "optional" 또는 "미지정 시 ..." 명시. 조건에 따라 키 자체가 생략될 수 있음 (예: `system_prompt`, `scores`, `error`)

**시간 포맷 (ISO 8601 UTC 일관)**: 모든 `*_at`, `*_time`, `sunset`, `retry_after` 등 시간 필드는 **RFC 3339 / ISO 8601 UTC**로 `Z` 접미사를 붙여 반환한다 (예: `"2026-04-12T03:14:15.123456Z"`). 로컬 타임존 및 offset(±HH:MM)은 사용하지 않는다. 클라이언트 입력 시에도 UTC `Z`를 요구하며, 오프셋 포함 입력은 `422 VALIDATION_ERROR`로 거절한다. 날짜만 필요한 필드(예: `daily_cost_limit` 집계 키)는 `YYYY-MM-DD` (UTC 기준일)를 사용한다.

**ETag / If-Match (낙관적 동시성 제어)**: 리소스 수정 경로(PATCH/PUT/DELETE 및 상태 전이 POST)는 다음 규약을 따른다.
- GET/PATCH 응답에 `ETag: "<sha256-prefix-16>"` 헤더 포함 (본문 JSON의 정규화 해시)
- 동일 리소스 수정 요청에 `If-Match: "<etag>"` 헤더 필수. 불일치 시 `412 PRECONDITION_FAILED` 반환 (신규 에러코드 `ETAG_MISMATCH`)
- `If-Match: *`는 존재 확인만 수행하며 허용
- 적용 범위: `PATCH /prompts/{name}/versions/{version}/labels`, `POST /experiments/{id}/{pause|resume|cancel|retry-failed}`, `DELETE /experiments/{id}`, `DELETE /datasets/{name}`, `POST /evaluators/submissions/{id}/{approve|reject}`, `PATCH /notifications/{id}/read`
- SSE 스트림 및 GET 목록 응답은 ETag를 요구하지 않음(캐시 힌트 용도로만 선택 제공)
- 클라이언트 GET→수정 플로우에서 304 지원: `If-None-Match` 수신 시 본문 생략(`304 Not Modified`)

**부분 응답 (`fields` 파라미터)**: 모든 GET 조회 엔드포인트는 `fields` 쿼리 파라미터를 선택적으로 지원한다.
- 문법: 쉼표 구분 최상위 필드명 (예: `fields=experiment_id,status,progress`)
- 중첩 접근: 점 표기 1단계까지 지원 (예: `fields=runs.run_name,runs.status`)
- 미지정 시 전체 필드 반환 (하위 호환)
- 존재하지 않는 필드 지정 시 `422 VALIDATION_ERROR` + `invalid_fields` 배열 반환
- 목록형 응답의 페이지네이션 메타(`total`, `page`, `page_size`)는 `fields`와 무관하게 항상 포함
- SSE 엔드포인트, `openapi.json`, `/health`는 `fields` 미지원

**알림(Notifications)**: 서버→클라이언트 이벤트 전달은 §13 Notification Inbox(REST 폴링 + SSE 옵션)로 단일화한다. 본 시스템은 외부 아웃바운드 웹훅을 제공하지 않는다.

**캐시 헤더 (Cache-Control / Vary)**: 모든 응답은 다음 기본 정책을 따른다.
- 인증 보호 GET 응답: `Cache-Control: private, max-age=0, must-revalidate` (공유 캐시 금지, 재검증 필수)
- 정적 카탈로그성 GET(`/models`, `/evaluators/built-in`): `Cache-Control: private, max-age=300`
- 변이(POST/PATCH/PUT/DELETE) 및 SSE: `Cache-Control: no-store`
- `/health`: `Cache-Control: no-store`
- 모든 응답에 `Vary: Accept, Accept-Encoding, Authorization` 포함 (사용자별/표현별 캐시 분리)
- ETag 보유 GET은 §1.1 ETag 규약에 따라 `If-None-Match` 수신 시 `304 Not Modified` (본문 생략, ETag/Cache-Control 헤더 유지)

**응답 압축**: 본문 1KB 이상 응답은 `Accept-Encoding`에 따라 `br` > `gzip` 우선순위로 압축한다. SSE(`text/event-stream`)는 청크 경계 보존을 위해 압축하지 않는다(`Content-Encoding: identity`). 압축된 응답은 `Content-Encoding` 헤더와 `Vary: Accept-Encoding`을 항상 포함한다. 압축 결과가 원본보다 크면 압축을 생략한다. CRIME/BREACH 완화: 인증 토큰/CSRF 토큰을 응답 본문에 절대 포함하지 않는다.

**파일 다운로드 / 스트리밍 응답**: 바이너리·대용량 다운로드 엔드포인트(향후 `/datasets/{name}/export` 등)는 다음 규약을 따른다.
- `Content-Type`은 정확한 MIME(`text/csv; charset=utf-8`, `application/json`, `application/x-ndjson`)
- `Content-Disposition: attachment; filename="<ascii-fallback>"; filename*=UTF-8''<percent-encoded>` (RFC 6266) — `filename`은 ASCII로 정규화하고 `"`, `\`, 제어문자, 경로 구분자(`/`, `\`)를 제거하여 path traversal/HTTP response splitting 방지
- 가능 시 `Content-Length` 명시, 불가 시 `Transfer-Encoding: chunked`
- `X-Content-Type-Options: nosniff` 강제, `Cache-Control: private, no-store`
- 대용량은 백엔드 streaming response로 전송하며 임시 파일 메모리 적재 금지(>10MB는 chunk 64KB)
- CSV export는 첫 셀이 `=`, `+`, `-`, `@`, `\t`, `\r`로 시작하면 단일 인용부호(`'`)를 prefix하여 **CSV formula injection** 방지
- 다운로드 인증은 일반 JWT를 따르며, 사전서명 URL은 사용하지 않는다(쿠키/Authorization 헤더 필수)

**배치(Bulk) 작업 규약**: 다건 변이를 한 번에 처리하는 엔드포인트는 일관된 요청/응답 구조와 부분 실패 시맨틱을 따른다.
- 요청: `{ "items": [...], "atomic": false }` — `items` 최대 100건(초과 시 `422 VALIDATION_ERROR` + `code=BULK_TOO_LARGE`)
- `atomic=true`: 단일 트랜잭션, 하나라도 실패 시 전체 롤백 → `409 STATE_CONFLICT` + 실패 인덱스/사유 반환
- `atomic=false` (기본): 항목별 독립 처리, HTTP는 `207 Multi-Status` 의미를 본문으로 표현 → `200 OK`로 응답하고 본문 `results: [{ index, status: "success|error", id?, error? }]`, `succeeded`, `failed` 카운트 포함
- Idempotency: bulk 전체에 단일 `Idempotency-Key`를 적용하며 항목별 키는 사용하지 않는다(부분 재시도는 실패 항목만 재요청)
- Rate limit: bulk 1요청 = `ceil(items/10)` 토큰 소비로 계산
- 권한 검사: 모든 항목에 대해 사전 권한 검증 후 실행. 단 한 항목이라도 권한 없으면 `403 FORBIDDEN`(atomic) 또는 해당 항목만 error 처리(non-atomic)
- 적용 대상(현시점): `POST /datasets/from-items`, 향후 `DELETE /experiments` bulk 변형. 단건 DELETE 다회 호출로 충분한 경우 bulk 엔드포인트를 도입하지 않는다.

---

### 1.5 헬스 체크
```
GET /api/v1/health

Response:
{
    "status": "ok",
    "version": "1.0.0",
    "services": {
        "langfuse": "ok | fail",
        "litellm": "ok | fail",
        "clickhouse": "ok | fail",
        "redis": "ok | fail"
    }
}

인증 불필요
```

---

## 2. 프롬프트 API

### 2.1 프롬프트 목록 조회
```
GET /api/v1/prompts

Query Parameters:
- project_id: string (required)

Response:
- prompts: [{ name, latest_version, labels, tags, created_at }]

내부 동작: Langfuse GET /api/public/v2/prompts 프록시
```

### 2.2 프롬프트 상세 조회
```
GET /api/v1/prompts/{name}

Query Parameters:
- project_id: string (required)
- version: int (optional, 미지정 시 최신)
- label: string (optional, "production" 등)

Response:
- name, version, type (text|chat), prompt, config, labels
- variables: [프롬프트에서 추출한 변수 목록]

내부 동작:
1. Langfuse GET /api/public/v2/prompts/{name} 호출
2. 프롬프트 내 {{variable}} 패턴 파싱하여 variables 필드 추가
```

### 2.3 프롬프트 버전 목록
```
GET /api/v1/prompts/{name}/versions

Query Parameters:
- project_id: string (required)

Response:
- versions: [{ version, labels, created_at, created_by }]
```

### 2.4 프롬프트 생성/업데이트
```
POST /api/v1/prompts

Request Body:
{
    "project_id": "string",
    "name": "string",
    "prompt": "프롬프트 텍스트 또는 chat messages",
    "type": "text | chat",
    "config": {},
    "labels": ["staging"]
}

Response:
{
    "name": "...",
    "version": 4,
    "labels": ["staging"]
}

내부 동작: Langfuse POST /api/public/v2/prompts 프록시
권한: user 이상
```

### 2.5 프롬프트 라벨 승격
```
PATCH /api/v1/prompts/{name}/versions/{version}/labels

Request Body:
{
    "project_id": "string",
    "labels": ["production"]
}

Response:
{
    "name": "...",
    "version": 3,
    "labels": ["production"]
}

권한: admin
```

---

## 3. 단일 테스트 API

### 3.1 단일 테스트 실행
```
POST /api/v1/tests/single

Request Body:
{
    "project_id": "string",
    "prompt": {
        "source": "langfuse | inline",
        "name": "string (langfuse일 때)",
        "version": 3,
        "content": "string (inline일 때)"
    },
    "variables": {
        "input_text": "이 서비스는 정말 만족스럽습니다",
        "analysis_rules": "{ ... }"
    },
    "model": "gpt-4o",
    "parameters": {
        "temperature": 0.1,
        "max_tokens": 1024,
        "top_p": 1.0,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0
    },
    "system_prompt": "string (optional)",
    "images": ["base64_encoded_string"],
    "evaluators": [
        { "type": "built_in", "name": "json_validity" }
    ],
    "stream": true
}

**검증 규칙**:
- prompt.source가 'langfuse'이면 name/version 필수, content 무시. 'inline'이면 content 필수, name/version 무시.
- parameters.temperature: 0.0~2.0 (기본: 1.0)
- parameters.max_tokens: 1~모델별 상한 (기본: 1024)
- parameters.top_p: 0.0~1.0 (기본: 1.0)
- parameters.frequency_penalty: -2.0~2.0 (기본: 0.0)
- parameters.presence_penalty: -2.0~2.0 (기본: 0.0)
- images: 최대 10장, 개별 이미지 최대 20MB (base64)

Response (stream=false):
{
    "trace_id": "string",
    "output": "감성 분석 결과: 긍정 (confidence: 0.95)",
    "usage": {
        "input_tokens": 150,
        "output_tokens": 25,
        "total_tokens": 175
    },
    "latency_ms": 1200,
    "cost_usd": 0.0023,
    "model": "gpt-4o",
    "scores": {
        "exact_match": 1.0
    }
}

Response (stream=true):
Content-Type: text/event-stream

event: token
data: {"content": "분류"}

event: token
data: {"content": " 결과"}

event: done
data: {"trace_id": "...", "output": "전체 응답 텍스트", "model": "gpt-4o", "usage": {...}, "latency_ms": 1200, "cost_usd": 0.0023, "scores": {"exact_match": 1.0}}

event: error
data: {"code": "LLM_ERROR", "message": "Rate limit exceeded"}
```

### 3.2 단일 테스트 중단
```
POST /api/v1/tests/single/{trace_id}/cancel

Response:
{
    "status": "success",
    "data": { "trace_id": "string", "cancelled": true }
}

에러: 404 (trace_id 없음), 409 STATE_CONFLICT (이미 완료/취소됨)
```

---

## 4. 배치 실험 API

### 4.1 실험 생성 및 실행
```
POST /api/v1/experiments

Request Body:
{
    "project_id": "string",
    "name": "감성 분석 실험 v3 vs v4",
    "description": "GPT-4o와 Gemini로 프롬프트 v3, v4 비교",
    "prompt_configs": [
        {
            "name": "sentiment-analysis",
            "version": 3,
            "label": null
        },
        {
            "name": "sentiment-analysis",
            "version": 4,
            "label": null
        }
    ],
    "dataset_name": "sentiment-analysis-golden-100",
    "model_configs": [
        {
            "model": "gpt-4o",
            "parameters": { "temperature": 0.1 }
        },
        {
            "model": "gemini-2.5-pro",
            "parameters": { "temperature": 0.1 }
        }
    ],
    "evaluators": [
        {
            "type": "built_in",
            "name": "exact_match",
            "weight": 0.5
        },
        {
            "type": "llm_judge",
            "name": "accuracy_judge",
            "config": {
                "judge_model": "gpt-4o",
                "prompt": "주어진 입력에 대해 출력이 정확한지 0-10 점수로 평가하세요..."
            },
            "weight": 0.3
        },
        {
            "type": "custom_code",
            "name": "label_f1",
            "code": "def evaluate(output, expected, metadata):\n    ...",
            "weight": 0.2
        }
    ],
    "concurrency": 5,
    "system_prompt": "string (optional)"
}

**검증 규칙**:
- prompt_configs: 최소 1개 이상 필수
- model_configs: 최소 1개 이상 필수
- evaluators: 최소 1개 이상 필수
- dataset_name: Langfuse에 존재해야 함
- concurrency: 1~20 (기본: 5)
- name: 1~100자
- evaluators[].weight: 0.0~1.0, 모두 지정 시 합계는 [1.0 - 1e-6, 1.0 + 1e-6] 범위. 미지정 시 EVALUATION.md §5.4.1 기본값 규칙 적용 (균등 분배 또는 잔여 균등 분배)
- evaluators[].type: `built_in` | `llm_judge` | `custom_code` | `approved` (submission_id 참조)

Response:
{
    "experiment_id": "uuid",
    "status": "running",
    "total_runs": 4,
    "total_items": 400,
    "runs": [
        {
            "run_name": "sentiment-analysis_v3_gpt-4o_20260411",
            "prompt_version": 3,
            "model": "gpt-4o",
            "status": "running"
        },
        ...
    ]
}
```

### 4.2 실험 상태 스트리밍
```
GET /api/v1/experiments/{experiment_id}/stream

Response: text/event-stream

event: progress
data: {
    "run_name": "...",
    "completed": 45,
    "total": 100,
    "current_item": { "id": "...", "status": "completed", "score": 0.85 }
}

event: run_complete
data: {
    "run_name": "...",
    "summary": { "avg_score": 0.87, "total_cost": 1.23, "avg_latency": 1100 }
}

event: experiment_complete
data: {
    "experiment_id": "...",
    "total_duration_sec": 340,
    "total_cost_usd": 5.67
}

event: error
data: { "run_name": "...", "item_id": "...", "error": "Timeout" }
```

### 4.3 실험 상태 조회
```
GET /api/v1/experiments/{experiment_id}

Response:
{
    "experiment_id": "uuid",
    "name": "...",
    "status": "running | paused | completed | failed | cancelled",
    "progress": {
        "completed": 350,
        "failed": 5,
        "total": 400
    },
    "runs": [
        {
            "run_name": "...",
            "status": "completed",
            "summary": { "avg_score": 0.87, ... }
        }
    ],
    "created_at": "...",
    "completed_at": "...",
    "config_snapshot": {
        "prompt_configs": [...],
        "model_configs": [...],
        "evaluators": [...],
        "dataset_name": "...",
        "concurrency": 5,
        "system_prompt": "..."
    }
}
```

`config_snapshot`은 원본 실험 생성 요청을 그대로 보존하며, UI의 "같은 설정으로 재실행" 기능에서 사용한다.

### 4.4 실험 제어
```
POST /api/v1/experiments/{experiment_id}/pause
POST /api/v1/experiments/{experiment_id}/resume
POST /api/v1/experiments/{experiment_id}/cancel
POST /api/v1/experiments/{experiment_id}/retry-failed

공통 Response:
{
    "experiment_id": "uuid",
    "status": "변경 후 상태",
    "updated_at": "2026-04-12T03:14:15.123456Z"
}

에러: 409 STATE_CONFLICT (허용되지 않는 상태 전이)
```

**상태 전이 규칙**:
```
                  ┌──────────┐
    create ──────▶│ running  │◀─── resume
                  └────┬─────┘
                       │
            ┌──────────┼──────────┐
            ▼          ▼          ▼
       ┌────────┐ ┌────────┐ ┌──────────┐
       │ paused │ │completed│ │  failed  │
       └────┬───┘ └────┬───┘ └────┬─────┘
            │          │          │
            │          └─────┬────┘
            ▼                ▼
       ┌──────────┐   retry-failed
       │cancelled │   (→ running)
       └──────────┘
```

- `pause`: running → paused
- `resume`: paused → running
- `cancel`: running 또는 paused → cancelled
- `retry-failed`: completed 또는 failed → running (실패 아이템만 재실행)
- 이미 cancelled인 실험은 재시작 불가 (409 Conflict 반환)

**완료/실패 판단 기준**:
- `completed`: 모든 아이템 처리 완료 (실패 아이템이 일부 있어도 전체 처리가 끝나면 completed)
- `failed`: 실험 레벨 오류 (예: Redis 연결 실패, Langfuse 접속 불가 등 인프라 장애)

**실험 상태 저장**: Redis에 저장 (TTL 24시간), 완료 시 Langfuse trace metadata로 영속화

### 4.5 실험 목록 조회
```
GET /api/v1/experiments

Query Parameters:
- project_id: string (required)
- status: string (optional)
- page: int (default 1)
- page_size: int (default 20, max 100)

Response:
- experiments: [{ experiment_id, name, status, total_runs, total_cost_usd, created_at }]
- total: int
- page: int
- page_size: int
```

---

## 5. 실험 비교/분석 API

### 5.1 실험 간 요약 비교
```
POST /api/v1/analysis/compare

Request Body:
{
    "project_id": "string",
    "run_names": ["run_a", "run_b", "run_c"]
}

Response:
{
    "comparison": [
        {
            "run_name": "run_a",
            "model": "gpt-4o",
            "prompt_version": 3,
            "metrics": {
                "sample_count": 100,
                "avg_latency_ms": 1100,
                "p50_latency_ms": 950,
                "p90_latency_ms": 1800,
                "p99_latency_ms": 3200,
                "total_cost_usd": 1.23,
                "avg_input_tokens": 150,
                "avg_output_tokens": 45
            },
            "scores": {
                "exact_match": { "avg": 0.87, "min": 0, "max": 1, "stddev": 0.34 },
                "accuracy_judge": { "avg": 0.82, "min": 0.3, "max": 1.0, "stddev": 0.15 }
            }
        },
        ...
    ]
}
```

### 5.2 아이템별 상세 비교
```
POST /api/v1/analysis/compare/items

Request Body:
{
    "project_id": "string",
    "run_names": ["run_a", "run_b"],
    "score_name": "exact_match",
    "sort_by": "score_range",
    "sort_order": "desc",
    "page": 1,
    "page_size": 20
}

Response:
{
    "items": [
        {
            "dataset_item_id": "...",
            "input": { ... },
            "expected_output": "...",
            "results": {
                "run_a": {
                    "output": "...",
                    "score": 1.0,
                    "latency_ms": 900,
                    "cost_usd": 0.012
                },
                "run_b": {
                    "output": "...",
                    "score": 0.0,
                    "latency_ms": 1200,
                    "cost_usd": 0.015
                }
            },
            "score_range": 1.0
        },
        ...
    ],
    "total": 100,
    "page": 1,
    "page_size": 20
}
```

### 5.2.1 아이템 필터 (스코어 범위)

`POST /api/v1/analysis/compare/items` 요청에 필터 추가:
```
"filter": {
    "score_name": "exact_match",
    "score_min": 0.0,
    "score_max": 0.3,
    "latency_min_ms": null,
    "latency_max_ms": null
}
```
차트에서 특정 bin 클릭 시 드릴다운에 사용.

### 5.3 스코어 분포 조회
```
GET /api/v1/analysis/scores/distribution

Query Parameters:
- project_id: string
- run_names: string (쉼표 구분, 복수 지원 — overlay histogram용)
- score_name: string
- bins: int (default 10)

Response:
{
    "distribution": [
        { "bin_start": 0.0, "bin_end": 0.1, "count": 5 },
        { "bin_start": 0.1, "bin_end": 0.2, "count": 3 },
        ...
    ],
    "statistics": {
        "mean": 0.87,
        "median": 0.92,
        "stddev": 0.15,
        "min": 0.0,
        "max": 1.0
    }
}
```

### 5.4 지연시간 분포 조회
```
GET /api/v1/analysis/latency/distribution

Query Parameters: project_id, run_names (쉼표 구분), bins
Response:
{
    "runs": {
        "run_a": { "distribution": [...], "statistics": {"p50": 950, "p90": 1800, "p99": 3200} },
        "run_b": { ... }
    }
}
```

### 5.5 비용 분포 조회
```
GET /api/v1/analysis/cost/distribution

Query Parameters: project_id, run_names (쉼표 구분), bins
Response: 5.4와 동일 구조 + cost_total 필드
```

### 5.6 비용 필드 분리 규약 (model_cost vs eval_cost)

§3.1, §4.2, §5.1, §5.2, §5.5의 모든 `cost_usd` / `total_cost_usd` 필드는 다음 두 항목의 **합계**이며, 응답에는 항상 분해된 형태도 함께 포함한다.

| 필드 | 의미 | 산출 |
|------|------|------|
| `model_cost_usd` | 메인 LLM 호출 비용 | LiteLLM `completion_cost()` (실험 대상 모델) |
| `eval_cost_usd` | LLM-as-Judge evaluator 호출 비용 | Judge 모델의 LiteLLM `completion_cost()` 합계 (built_in/custom_code는 0) |
| `cost_usd` (또는 `total_cost_usd`) | 합계 | `model_cost_usd + eval_cost_usd` |

`model_cost_usd`와 `eval_cost_usd`는 별도 Langfuse score(`name=model_cost`, `name=eval_cost`, DataType=NUMERIC)로 기록되어 EVALUATION §6.0의 단일 소스 원칙을 유지한다.

### 5.7 데이터 출처 명시 (Data Source)

EVALUATION §6.0 저장 위치 정책에 따라 §4 / §5 응답의 데이터 출처는 다음과 같다.

| 응답 필드 | 출처 | 비고 |
|----------|------|------|
| `progress.completed/failed/total` (§4.2 progress, §4.3) | **Redis** (`ax:experiment:{id}:progress`, TTL 24h) | 실행 중에만. 완료 후에는 Langfuse trace metadata에서 조회 |
| `runs[].summary` (avg_score/total_cost/avg_latency) | **ClickHouse** (Langfuse 백엔드) 조회 시점 계산 | 캐시하지 않음. `Cache-Control: private, max-age=0` |
| `metrics`/`scores` (§5.1, §5.2) | **ClickHouse** 직접 쿼리 | parameterized query, 읽기 전용 계정 |
| `distribution` (§5.3~5.5) | **ClickHouse** 직접 쿼리 | 동일 |
| `config_snapshot` (§4.3) | **Redis** (실행 중) → **Langfuse trace metadata** (완료 후) | 동일 키로 폴백 조회 |

Backend는 §4/§5 응답을 자체 캐시 계층에 저장하지 않는다 (Langfuse를 단일 진실 공급원으로 유지). 클라이언트 캐시는 §1.1 ETag 규약에 의해서만 제어된다.

### 8.3 Score Config 조회

EVALUATION §6.0의 Score Config 사전 등록 의무를 UI에서 검증/표시하기 위한 읽기 전용 엔드포인트.

```
GET /api/v1/evaluators/score-configs

Query Parameters:
- project_id: string (required)

Response:
{
    "score_configs": [
        {
            "name": "exact_match",
            "data_type": "NUMERIC",
            "min_value": 0.0,
            "max_value": 1.0,
            "source": "built_in | llm_judge | custom_code | system",
            "registered": true
        },
        {
            "name": "weighted_score",
            "data_type": "NUMERIC",
            "min_value": 0.0,
            "max_value": 1.0,
            "source": "system",
            "registered": true
        },
        {
            "name": "model_cost",
            "data_type": "NUMERIC",
            "source": "system",
            "registered": true
        }
    ]
}

내부 동작: services/score_registry.py의 단일 소스를 Langfuse Score Config 목록과 대조하여 반환.
캐시: Cache-Control: private, max-age=300 (정적 카탈로그성).
에러: 503 LANGFUSE_ERROR (Score Config 동기화 실패 시).
```

---

## 6. 데이터셋 API

### 6.1 데이터셋 목록 조회
```
GET /api/v1/datasets

Query Parameters:
- project_id: string (required)

Response:
- datasets: [{ name, item_count, created_at, last_used_at, metadata }]

내부 동작: Langfuse GET /api/public/v2/datasets 프록시
```

### 6.2 데이터셋 아이템 조회
```
GET /api/v1/datasets/{name}/items

Query Parameters:
- project_id: string
- page: int
- page_size: int

Response:
- items: [{ id, input, expected_output, metadata }]
- total: int
- page: int
- page_size: int
```

### 6.3 데이터셋 업로드
```
POST /api/v1/datasets/upload

Request Body (multipart/form-data):
- project_id: string
- dataset_name: string
- description: string
- file: File (CSV, JSON, JSONL)
- mapping: JSON string
    {
        "input_columns": ["input_text", "context"],
        "output_column": "expected_label",
        "metadata_columns": ["difficulty", "source"]
    }

Response — 동기 완료 (≤500행, HTTP 200):
{
    "dataset_name": "...",
    "items_created": 100,
    "items_failed": 0,
    "failed_items": [],
    "status": "completed | partial | failed",
    "upload_id": "uuid"
}

Response — 비동기 수락 (>500행, HTTP 202 Accepted):
{
    "upload_id": "uuid",
    "status": "processing",
    "stream_url": "/api/v1/datasets/upload/{upload_id}/stream"
}
```

### 6.3.1 업로드 진행률 (SSE)
대용량 파일(>500 행) 업로드 시 SSE로 진행률 스트리밍:

```
GET /api/v1/datasets/upload/{upload_id}/stream

Response: text/event-stream

event: progress
data: {"completed": 45, "total": 100, "failed": 0}

event: done
data: {"dataset_name": "...", "items_created": 100, "items_failed": 0, "status": "completed"}

event: error
data: {"code": "LANGFUSE_ERROR", "message": "..."}
```

클라이언트 플로우:
1. `POST /datasets/upload` 호출 → 즉시 `upload_id` 반환 (202 Accepted)
2. `GET /datasets/upload/{upload_id}/stream` 구독하여 진행률 수신
3. 완료 시 done 이벤트로 최종 결과 수신

**동작 규칙**:
- **500행 이하**: 동기 처리, 즉시 완료 Response 반환 (기존 동작)
- **500행 초과**: 202 Accepted + upload_id 반환, 백그라운드 처리
- **클라이언트 구독 없이도 처리 계속**: 서버는 Redis에 progress snapshot 지속 기록
- **재접속 지원**: upload_id TTL 1시간 내 재구독 가능. 구독 시 현재 snapshot 즉시 전송 후 live 이벤트 append
- **다중 구독자**: 여러 탭에서 동일 upload_id 구독 가능 (각 소비자가 독립적으로 Redis 폴링)
- **권한 검증**: `ax:dataset_upload:{upload_id}` Hash에 `owner_user_id` 필드 저장. SSE 요청 시 JWT sub와 일치 확인. admin은 우회 허용
- **파일 본문 저장**: 업로드된 파일은 `/tmp/ax-uploads/{upload_id}` 임시 저장, 처리 완료 또는 TTL 만료 시 자동 삭제
- **Progress 해상도**: 매 10 아이템마다 SSE 이벤트 발송 (throttling)
- **Heartbeat**: 15초마다 `: heartbeat\n\n` 주석 전송 (연결 유지), 60초 무응답 시 `error` 이벤트 후 종료
- **Partial 실패**: row-level 에러는 `failed_items`에 기록하고 계속 진행. file-level 에러는 즉시 `error` 이벤트 후 종료
- **재업로드**: 동일 dataset_name은 `mode=overwrite|append|fail` 쿼리 파라미터로 제어 (기본: fail)

Redis 저장: `ax:dataset_upload:{upload_id}` (TTL 1시간)
- fields: `owner_user_id`, `dataset_name`, `total`, `completed`, `failed`, `status`, `created_at`, `failed_items` (JSON)

### 6.4 업로드 미리보기
```
POST /api/v1/datasets/upload/preview

Request Body (multipart/form-data):
- file: File
- mapping: JSON string

Response:
{
    "columns": ["input_text", "context", "expected_label", "difficulty"],
    "preview": [
        {
            "input": { "input_text": "...", "context": "..." },
            "expected_output": "positive",
            "metadata": { "difficulty": "easy" }
        },
        ... (최대 5건)
    ],
    "total_rows": 100
}
```

---

## 7. 모델 API

### 7.1 사용 가능 모델 목록
```
GET /api/v1/models

Response:
{
    "models": [
        {
            "id": "gpt-4o",
            "provider": "azure",
            "display_name": "GPT-4o (Azure)",
            "supports_vision": true,
            "supports_streaming": true,
            "max_tokens": 128000,
            "cost_per_1k_input": 0.0025,
            "cost_per_1k_output": 0.01
        },
        ...
    ]
}

내부 동작: LiteLLM Proxy /model/info 프록시
```

---

## 8. 평가 함수 API

### 8.1 내장 평가 함수 목록
```
GET /api/v1/evaluators/built-in

Response:
{
    "evaluators": [
        {
            "name": "exact_match",
            "description": "출력과 기대값의 정확 일치 여부",
            "return_type": "binary",
            "parameters": {
                "ignore_case": { "type": "boolean", "default": false },
                "normalize_whitespace": { "type": "boolean", "default": false }
            }
        },
        {
            "name": "cosine_similarity",
            "description": "임베딩 기반 의미 유사도",
            "return_type": "float",
            "parameters": {
                "embedding_model": { "type": "string", "default": "text-embedding-3-small" }
            }
        },
        ...
    ]
}
```

### 8.2 커스텀 평가 함수 검증
```
POST /api/v1/evaluators/validate

Request Body:
{
    "code": "def evaluate(output, expected, metadata):\n    return 1.0 if output == expected else 0.0",
    "test_cases": [
        { "output": "positive", "expected": "positive", "metadata": {} },
        { "output": "negative", "expected": "positive", "metadata": {} }
    ]
}

Response:
{
    "valid": true,
    "test_results": [
        { "input_index": 0, "result": 1.0, "error": null },
        { "input_index": 1, "result": 0.0, "error": null }
    ]
}
```

---

## 9. 프로젝트 API

### 9.1 프로젝트 목록
```
GET /api/v1/projects

Response:
- projects: [{ id, name, created_at }]

내부 동작: Langfuse에 등록된 프로젝트 API Key 목록 기반
```

### 9.2 프로젝트 전환
```
POST /api/v1/projects/switch

Request Body:
{ "project_id": "string" }

Response:
{
    "status": "success",
    "data": { "project_id": "string", "name": "string" }
}

에러: 404 PROJECT_NOT_FOUND

내부 동작: 해당 프로젝트의 Langfuse API Key로 클라이언트 전환

NOTE: 이 엔드포인트는 프로젝트 전환 검증용이다. 실제 전환은 stateless — 이후 API 호출 시 project_id 파라미터로 프로젝트를 지정한다.
```

---

## 10. 검색 API

### 10.1 글로벌 검색
```
GET /api/v1/search

Query Parameters:
- project_id: string (required)
- q: string (required, 검색어)
- type: string (optional, "prompt" | "dataset" | "experiment")

Response:
{
    "results": {
        "prompts": [{ name, latest_version, match_context }],
        "datasets": [{ name, item_count, match_context }],
        "experiments": [{ experiment_id, name, status, match_context }]
    }
}
```

**`q` 표현식 보안/검증 규칙**:
- 길이: 1~200자 (외 `422 VALIDATION_ERROR`)
- 허용 문자: 유니코드 letter/digit + 공백 + `_ - . : @ /` (그 외는 `422`). 와일드카드/정규식/연산자(`* ? % \ | & ! ( ) [ ] { } < > ; "` `'`)는 거절
- 매칭 방식: ClickHouse `positionCaseInsensitiveUTF8` 기반 부분일치만 지원, LIKE/regex/SQL 질의 절대 금지
- 모든 쿼리는 **parameterized query**로 ClickHouse/Langfuse에 전달(f-string 보간 금지 — CLAUDE.md 준수)
- `match_context` 응답은 매칭 부위 ±40자 컨텍스트만 반환하고, HTML/JS는 escape하여 XSS 방지
- 로깅: `q` 원문은 로그에 기록하지 않으며 SHA-256 prefix(8자)만 기록
- Rate limit: 사용자당 60/min (기본 GET 한도와 별도 카운터)

---

## 11. 실험/데이터셋 삭제 API

### 11.1 실험 삭제
```
DELETE /api/v1/experiments/{experiment_id}

Response:
{ "status": "success", "data": { "experiment_id": "uuid", "deleted": true } }

에러: 409 STATE_CONFLICT (running/paused 상태에서는 삭제 불가, 먼저 cancel 필요)
권한: admin
```

### 11.2 데이터셋 삭제
```
DELETE /api/v1/datasets/{name}

Query Parameters:
- project_id: string (required)

Response:
{ "status": "success", "data": { "dataset_name": "string", "deleted": true } }

에러: 404 DATASET_NOT_FOUND, 409 STATE_CONFLICT (활성 실험에서 참조 중)
권한: admin
내부 동작: Langfuse DELETE /api/public/v2/datasets/{name} 프록시
```

---

## 12. 데이터셋 파생 생성 (실패 아이템 기반)

```
POST /api/v1/datasets/from-items

Request Body:
{
    "project_id": "string",
    "source_run_names": ["run_a", "run_b"],
    "item_ids": ["item_001", "item_042"],
    "new_dataset_name": "failed_cases_20260415",
    "description": "감성분석 v3 실패 케이스 10건"
}

Response:
{
    "dataset_name": "...",
    "items_created": 10,
    "status": "completed"
}

권한: user 이상
내부 동작: 기존 trace에서 input/expected/metadata 추출 → 새 Langfuse 데이터셋 생성
```

---

## 12.5 Rate Limiting & 비용 제어

### 12.5.1 엔드포인트별 Rate Limit

사용자당(JWT sub 기반) sliding window rate limit. Redis 기반 구현 (`ax:ratelimit:{user_id}:{endpoint}`).

| 엔드포인트 | 제한 | 초과 시 |
|-----------|------|-------|
| `POST /experiments` | 10/min, 100/day | 429 + `Retry-After` 헤더 |
| `POST /tests/single` | 60/min | 429 |
| `POST /datasets/upload` | 5/min, 50/day | 429 |
| `POST /evaluators/validate` | 20/min | 429 |
| `POST /evaluators/submissions` | 5/day | 429 |
| `/analysis/*` (분포/비교, GET/POST 포함) | 30/min | 429 + 캐시된 결과 제공 시도 |
| 기본 GET 엔드포인트 | 300/min | 429 |

### 12.5.2 프로젝트 LLM 비용 Budget

- `PROJECTS_CONFIG`의 각 프로젝트에 `daily_cost_limit_usd` 필드 추가 (기본: $100)
- Redis Hash `ax:project:{id}:cost:daily:{YYYY-MM-DD}` 에 `HINCRBYFLOAT` 로 누적
- 실험 생성 시점에 예상 비용 + 현재 누적 비용이 한도 초과 시 `403 BUDGET_EXCEEDED` 반환
- 실행 중 한도 초과 시 해당 실험만 `failed` 전이, 다른 실험은 유지
- 한도 80% 도달 시 프로젝트 admin에게 알림(`budget_warning` 타입)

### 12.5.3 실험 동시성 상한

- 프로젝트당 동시 `running` 실험: 기본 5개 (환경변수 `MAX_CONCURRENT_EXPERIMENTS_PER_PROJECT`)
- 초과 시 `429 CONCURRENCY_LIMIT_EXCEEDED` + 현재 실행 중 목록 반환
- Lua script로 원자적 카운터 증감 (`ax:project:{id}:running_count`)

### 12.5.4 Custom Evaluator 샌드박스 상한

- 호스트당 동시 샌드박스 컨테이너: `EVAL_SANDBOX_MAX_CONCURRENT` (기본 10)
- 초과 시 실험 생성 큐잉 (최대 60초 대기), 이후 `503 SANDBOX_UNAVAILABLE`
- asyncio.Semaphore로 enforce

---

## 13. 알림 (Notification Inbox) API

### 13.1 알림 목록 조회
```
GET /api/v1/notifications

Query Parameters:
- project_id: string
- unread_only: boolean (default false)
- page: int, page_size: int (default 20, max 100)

Response:
- notifications: [{
    id, type ("experiment_complete" | "experiment_failed" | "experiment_cancelled" | "evaluator_approved" | "evaluator_rejected" | "evaluator_submission_pending"),
    title, message, target_url, read, created_at
  }]
- unread_count: int
- total: int
- page: int
- page_size: int

권한: viewer 이상 (본인 알림만)
```

### 13.2 알림 읽음 처리
```
PATCH /api/v1/notifications/{id}/read
POST /api/v1/notifications/mark-all-read

Response (PATCH 단건):
{ "status": "success", "data": { "id": "string", "read": true } }

Response (mark-all-read):
{ "status": "success", "data": { "marked_count": 12 } }

에러: 404 (id 없음), 403 FORBIDDEN (타 사용자 알림)
권한: viewer 이상 (본인 알림만)
```

저장소: Redis `ax:notification:{user_id}:*` (TTL 30일) — IMPLEMENTATION.md 참조

---

## 14. Custom Evaluator 거버넌스 API

### 14.1 Evaluator 제출
```
POST /api/v1/evaluators/submissions

Request Body:
{
    "name": "category_f1",
    "description": "카테고리별 F1 스코어",
    "code": "def evaluate(output, expected, metadata):\n    ..."
}

Response:
{
    "submission_id": "uuid",
    "status": "pending",
    "submitted_at": "..."
}

권한: user 이상 (admin도 제출 가능하지만 자기 제출은 자동 승인)
```

### 14.2 제출 목록 조회
```
GET /api/v1/evaluators/submissions

Query Parameters:
- status: "pending" | "approved" | "rejected" | "deprecated" (default all)
- project_id: string

Response:
- submissions: [{ submission_id, name, description, code, submitter, status, created_at, reviewed_at, rejection_reason }]

권한: admin (전체), user (본인 제출만)
```

### 14.3 제출 승인/반려
```
POST /api/v1/evaluators/submissions/{id}/approve
POST /api/v1/evaluators/submissions/{id}/reject

Request Body (reject):
{ "reason": "외부 네트워크 호출 시도가 감지되었습니다" }

Response:
{
    "status": "success",
    "data": {
        "submission_id": "uuid",
        "status": "approved | rejected",
        "reviewed_at": "2026-04-12T03:14:15.123456Z",
        "reviewer": "user_id"
    }
}

권한: admin
반려 시: 제출자에게 알림 생성 (13.x)
승인 시: 제출자 + 전체 사용자에게 활성화된 evaluator로 사용 가능
```

### 14.4 승인된 Evaluator 목록 (실험 생성 시 사용)
```
GET /api/v1/evaluators/approved

Query Parameters:
- project_id: string

Response:
- evaluators: [{ submission_id, name, description, version, approved_at, approver }]

권한: user 이상 (위저드 Step 3에서 체크박스 목록으로 표시)
```

**Scope**: Evaluator 승인은 **프로젝트별 격리**. `project_id`별로 독립 카탈로그 유지. 동일 evaluator를 다른 프로젝트에서 사용하려면 재제출 필요.

**재제출 처리**:
- 반려 후 재제출: 새 `submission_id` 발급 (이전 제출은 rejected 상태로 보존)
- 승인된 evaluator 업데이트: admin이 `POST /evaluators/submissions/{id}/new-version` 호출 (버전 증가, 기존 version은 보존)
- 삭제: `DELETE /evaluators/submissions/{id}` (admin 전용, 상태 `deprecated`로 전환 — 실제 삭제 아님, `POST /evaluators/submissions/{id}/deprecate`의 alias)
- Deprecate 액션 (FEATURES §9.1): `POST /api/v1/evaluators/submissions/{id}/deprecate` (admin 전용, 본문 `{ "reason": "보안 이슈 사후 발견" }`). status 필드는 `pending | approved | rejected | deprecated` 중 하나로 전이하며, `approved → deprecated`만 허용 (그 외 전이는 `409 INVALID_STATE_TRANSITION`). 효과: (1) `GET /evaluators/approved` 응답에서 즉시 제외, (2) 신규 실험에서 해당 submission 사용 시 `403 EVALUATOR_DEPRECATED` + `ax_unauthorized_evaluator_attempts_total{reason="deprecated"}` 증가, (3) 진행 중 실험은 `ax:experiment:{id}` snapshot의 inline 코드로 완료, (4) 소유자/구독자에게 `evaluator_deprecated` 알림 발송 (§13). 응답 본문에 `deprecated_at`, `deprecated_by`, `reason` 포함. Confirmation 헤더 필수 (§3 cross-cutting).

**실험 생성 시 참조 방식**: `POST /experiments`의 `evaluators[]`에 다음 형식 추가:
```json
{ "type": "approved", "submission_id": "uuid", "version": 3, "weight": 0.3 }
```

**코드 버전 고정 (재현성 보장)**:
- 실험 생성 시점에 해당 submission_id + version의 코드를 **`ax:experiment:{id}` → `config` snapshot에 inline 포함**
- 실행 중 admin이 evaluator를 삭제/반려해도 실행 중인 실험은 snapshot의 고정된 코드로 완료
- retry-failed 시에도 원본 snapshot의 코드 그대로 재실행

저장소: Redis `ax:evaluator_submission:{id}` (TTL 없음, 영구 보관)

---

## 15. 에러 코드

| 코드 | HTTP Status | 설명 |
|------|-------------|------|
| AUTH_REQUIRED | 401 | 인증 토큰 누락/만료 |
| FORBIDDEN | 403 | 권한 부족 (RBAC 위반) |
| PROJECT_NOT_FOUND | 404 | 프로젝트를 찾을 수 없음 |
| PROMPT_NOT_FOUND | 404 | 프롬프트를 찾을 수 없음 |
| DATASET_NOT_FOUND | 404 | 데이터셋을 찾을 수 없음 |
| EXPERIMENT_NOT_FOUND | 404 | 실험을 찾을 수 없음 |
| STATE_CONFLICT | 409 | 실험 상태 전이 불가 (예: cancelled 실험 재시작) |
| INVALID_STATE_TRANSITION | 409 | Evaluator 상태 전이 불가 (§14.4 deprecate: `approved → deprecated`만 허용) |
| EVALUATOR_DEPRECATED | 403 | Deprecated된 evaluator를 신규 실험에서 사용 시도 (§14.4) |
| INVALID_IDEMPOTENCY_KEY | 400 | Idempotency-Key 형식 오류 (UUIDv4 아님 또는 128자 초과, IMPLEMENTATION §6.1) |
| IDEMPOTENCY_CONFLICT | 409 | (deprecated alias) 동일 Idempotency-Key + 본문 해시 불일치 — 신규 코드는 422 `IDEMPOTENCY_KEY_REUSED` 사용 |
| IDEMPOTENCY_IN_PROGRESS | 409 | 동일 Idempotency-Key 선행 요청 처리 중 (Retry-After 헤더 포함, IMPLEMENTATION §6.1/§6.5) |
| IDEMPOTENCY_KEY_REUSED | 422 | 완료된 Idempotency-Key를 다른 본문 해시로 재사용 (IMPLEMENTATION §6.1/§6.5) |
| ETAG_MISMATCH | 412 | `If-Match` 헤더 불일치 (§1.1 낙관적 동시성) |
| BULK_TOO_LARGE | 422 | Bulk 요청 `items` 길이 > 100 (§1.1 Bulk 규약) |
| NOT_FOUND | 404 | 일반 리소스 없음 (도메인별 `*_NOT_FOUND`가 없는 경우 fallback, IMPLEMENTATION §6.5 code_map) |
| METHOD_NOT_ALLOWED | 405 | 허용되지 않은 HTTP 메서드 (IMPLEMENTATION §6.5 code_map) |
| HTTP_ERROR | 4xx/5xx | Starlette 내장 `HTTPException` fallback 코드 (IMPLEMENTATION §6.5) |
| INTERNAL_ERROR | 500 | 미처리 예외 (details.request_id 포함, IMPLEMENTATION §6.5) |
| LLM_ERROR | 502 | LLM 호출 실패 |
| LLM_TIMEOUT | 504 | LLM 호출 타임아웃 |
| LLM_RATE_LIMIT | 429 | LLM 프로바이더 rate limit |
| RATE_LIMIT_EXCEEDED | 429 | 사용자 API rate limit 초과 (Retry-After 헤더 포함) |
| BUDGET_EXCEEDED | 403 | 프로젝트 일일 LLM 비용 한도 초과 |
| CONCURRENCY_LIMIT_EXCEEDED | 429 | 프로젝트 동시 실험 수 초과 |
| SANDBOX_UNAVAILABLE | 503 | 호스트 샌드박스 컨테이너 포화 상태 |
| LANGFUSE_ERROR | 502 | Langfuse API 호출 실패 |
| CLICKHOUSE_ERROR | 502 | ClickHouse 쿼리 실패 |
| EVALUATOR_ERROR | 500 | 평가 함수 실행 실패 |
| EVALUATOR_TIMEOUT | 504 | 커스텀 평가 함수 실행 시간 초과 (5초) |
| SANDBOX_VIOLATION | 403 | 커스텀 평가 함수 보안 제약 위반 |
| EVALUATOR_IMPORT | 400 | 커스텀 평가 함수에서 비허용 모듈 import 시도 |
| EVALUATOR_OOM | 500 | 커스텀 평가 함수 메모리 제한 초과 (128MB) |
| INVALID_EVALUATOR | 400 | 커스텀 평가 함수 문법 오류 |
| FILE_PARSE_ERROR | 400 | 업로드 파일 파싱 실패 |
| FILE_TOO_LARGE | 413 | 업로드 파일 크기 초과 (50MB) |
| FILE_ENCODING_ERROR | 400 | 파일 인코딩 감지 실패 |
| MAPPING_ERROR | 400 | 데이터셋 컬럼 매핑 오류 |
| VALIDATION_ERROR | 422 | 요청 데이터 검증 실패 |
