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

---

## 1.5 헬스 체크
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
            "name": "exact_match"
        },
        {
            "type": "llm_judge",
            "name": "accuracy_judge",
            "config": {
                "judge_model": "gpt-4o",
                "prompt": "주어진 입력에 대해 출력이 정확한지 0-10 점수로 평가하세요..."
            }
        },
        {
            "type": "custom_code",
            "name": "label_f1",
            "code": "def evaluate(output, expected, metadata):\n    ...",
            "weight": 1.0
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
    "completed_at": "..."
}
```

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
    "updated_at": "ISO 8601"
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
    "page": 1
}
```

### 5.3 스코어 분포 조회
```
GET /api/v1/analysis/scores/distribution

Query Parameters:
- project_id: string
- run_name: string
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

Response:
{
    "dataset_name": "...",
    "items_created": 100,
    "status": "completed"
}
```

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

권한: admin
내부 동작: Langfuse DELETE /api/public/v2/datasets/{name} 프록시
```

---

## 11.3 데이터셋 파생 생성 (실패 아이템 기반)

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
    id, type ("experiment_complete" | "experiment_failed" | "evaluator_approved" | "evaluator_rejected"),
    title, message, target_url, read, created_at
  }]
- unread_count: int
- total: int

권한: viewer 이상 (본인 알림만)
```

### 13.2 알림 읽음 처리
```
PATCH /api/v1/notifications/{id}/read
POST /api/v1/notifications/mark-all-read

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
- status: "pending" | "approved" | "rejected" (default all)
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

권한: admin
반려 시: 제출자에게 알림 생성 (13.x)
승인 시: 제출자 + 전체 사용자에게 활성화된 evaluator로 사용 가능
```

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
| LLM_ERROR | 502 | LLM 호출 실패 |
| LLM_TIMEOUT | 504 | LLM 호출 타임아웃 |
| LLM_RATE_LIMIT | 429 | LLM 프로바이더 rate limit |
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
