# 빌드 순서 가이드

프로젝트를 처음부터 구축할 때의 단계별 빌드 순서.
각 Phase는 이전 Phase의 산출물에 의존하므로, 반드시 순서대로 진행한다.

---

## Phase 0: 테스트 인프라 구축

### 선행 조건
- 없음 (최우선 작업)

### 작업 목록

#### 0-1. Backend 테스트 프로젝트 구조
- `backend/app/__init__.py` + `backend/app/main.py` 스텁 생성 (빈 FastAPI 인스턴스 `app = FastAPI()`). pytest fixture에서 import하기 위한 최소 구조.
- `backend/tests/` 디렉토리 생성
- `backend/tests/conftest.py` — 공통 fixture 정의
- `backend/tests/unit/` — 단위 테스트
- `backend/tests/integration/` — 통합 테스트
- `backend/tests/infra/` — 인프라 연결 테스트
- pyproject.toml 테스트 설정
- pytest-asyncio, pytest-cov, fakeredis, httpx 의존성

#### 0-2. 공통 Mock/Fixture 구현
- `backend/tests/fixtures/mock_langfuse.py` — MockLangfuseClient (TEST_SPEC.md 0.3.1 참조)
- `backend/tests/fixtures/mock_redis.py` — MockRedisClient (TEST_SPEC.md 0.3.2 참조)
- `backend/tests/fixtures/mock_litellm.py` — MockLiteLLMProxy (TEST_SPEC.md 0.3.3 참조)
- `backend/tests/fixtures/mock_clickhouse.py` — MockClickHouseClient
- `backend/tests/fixtures/jwt_helper.py` — create_test_jwt() (TEST_SPEC.md 0.3.4 참조)

#### 0-3. Frontend 테스트 설정
- vitest.config.ts 설정
- MSW (Mock Service Worker) 핸들러 기본 구조
- `frontend/tests/` 디렉토리 구조

#### 0-4. CI 파이프라인
- `.github/workflows/test.yml` — backend-unit, backend-integration, frontend-unit, lint 분리 (TEST_SPEC.md 0.4 참조)

### 산출물
- `backend/tests/` 구조 + conftest.py + mock 4종 + jwt_helper
- `frontend/tests/` 구조 + vitest.config.ts
- `.github/workflows/test.yml`
- `pytest --collect-only`로 fixture 로딩 확인

### 검증 방법
```bash
cd backend && pytest --collect-only  # fixture 수집 성공, 0 errors
cd frontend && npx vitest --run --reporter=verbose 2>&1 | head  # vitest 실행 가능
```

### 테스트 명세 참조
- TEST_SPEC.md Phase 0 전체

---

## Phase 1: 인프라 셋업

### 선행 조건
- Docker, Docker Compose 설치
- LLM Provider API 키 (Azure OpenAI, Gemini, Bedrock, Anthropic, OpenAI 중 최소 1개)
- 프로젝트 디렉토리 구조 생성 완료 (`docker/`, `scripts/`, `backend/`, `frontend/`)

**TDD 순서**: 인프라 코드 구현 → 인프라 테스트로 검증 (인프라는 TDD보다 구현 후 검증이 적합)

### 작업 목록

#### 1-1. docker-compose.yml
개발 환경의 전체 인프라를 정의한다.

| 서비스 | 이미지 | 포트 | 비고 |
|--------|--------|------|------|
| langfuse | langfuse/langfuse:3 | 3001 | Langfuse Web UI |
| postgres | postgres:15-alpine | 5432 | Langfuse 메타데이터 저장 |
| clickhouse | clickhouse/clickhouse-server | 8123 | 시계열 분석 데이터 |
| redis | redis:7 | 6379 | Langfuse 큐잉 + Labs 실험 상태 |
| litellm | ghcr.io/berriai/litellm | 4000 | LLM Gateway |

네트워크 분리:
- `frontend_net`: 외부 노출 (frontend, backend)
- `backend_net`: 내부 서비스 간 통신 (backend, litellm, langfuse, postgres, clickhouse, redis). 운영 환경에서는 VPC/방화벽으로 외부 접근 차단

내부 서비스는 `expose:`만 사용하고 `ports:`는 사용하지 않는다.

#### 1-2. LiteLLM config.yaml
- 사용할 모델을 등록한다 (Azure OpenAI, Gemini, Bedrock, Anthropic, OpenAI)
- `success_callback: []` 으로 Langfuse callback을 명시적으로 비활성화한다
- Master Key를 설정한다 (최소 32자)
- Labs Backend가 trace/generation 기록을 전담하므로 LiteLLM에서는 기록하지 않는다

#### 1-3. .env.example
모든 환경변수의 템플릿 파일. 실제 값은 `.env`에 작성하고 gitignore 처리한다.

```
# Langfuse
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_HOST=http://langfuse:3000

# LiteLLM
LITELLM_MASTER_KEY=
LITELLM_BASE_URL=http://litellm:4000

# ClickHouse (admin - Langfuse용)
CLICKHOUSE_HOST=clickhouse
CLICKHOUSE_PORT=8123
CLICKHOUSE_DB=langfuse
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=

# ClickHouse (읽기 전용 - Labs Backend 분석용)
CLICKHOUSE_READONLY_USER=labs_readonly
CLICKHOUSE_READONLY_PASSWORD=

# Redis
REDIS_PASSWORD=
LABS_REDIS_DB=1
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/${LABS_REDIS_DB:-1}

# JWT
AUTH_JWKS_URL=
AUTH_JWT_AUDIENCE=
AUTH_JWT_ISSUER=

# LLM Provider Keys (LiteLLM에서만 사용)
AZURE_API_KEY=
GEMINI_API_KEY=
...
```

#### 1-4. ClickHouse 읽기 전용 계정 생성 스크립트
`docker/scripts/setup-clickhouse-readonly.sh` 파일로 작성한다.

- `labs_readonly` 사용자 생성
- `GRANT SELECT ON langfuse.*` 권한만 부여
- INSERT/UPDATE/DELETE 권한 없음
- ClickHouse는 `docker-entrypoint-initdb.d`를 지원하지 않으므로, 컨테이너 기동 후 수동 실행:
  ```bash
  docker compose exec clickhouse bash /scripts/setup-clickhouse-readonly.sh
  ```

#### 1-5. ax-eval-sandbox Docker 이미지 빌드
Custom Code Evaluator 실행을 위한 샌드박스 이미지.

- Python 3.12 slim 기반
- 허용 패키지만 설치: json, re, math, collections, difflib, statistics, unicodedata (표준 라이브러리)
- 네트워크 없음, 볼륨 없음, non-root 사용자
- 메모리 128MB 제한, 실행 시간 5초 제한
- `runner.py` (stdin으로 입력 수신, stdout으로 결과 반환) 포함
- `docker/eval-sandbox/` 디렉토리에 배치

### 산출물
- `docker compose up -d` 실행 시 모든 서비스가 정상 기동
- Langfuse Web UI (localhost:3001) 접속 가능
- LiteLLM Proxy (localhost:4000) 에서 `/health` 응답 확인
- ClickHouse에 `labs_readonly` 계정으로 SELECT 쿼리 가능
- sandbox 이미지 빌드 성공

### 검증 방법
```bash
# 1. 인프라 서비스만 기동 (frontend/backend는 아직 없으므로 제외)
docker compose -f docker/docker-compose.yml up -d postgres clickhouse redis langfuse litellm

# 2. 서비스 상태 확인
docker compose ps  # 모든 서비스 healthy/running

# 3. ClickHouse 읽기 전용 계정 생성 (초기 1회)
docker compose exec clickhouse bash /scripts/setup-clickhouse-readonly.sh

# 4. Langfuse 접속
curl http://localhost:3001/api/public/health

# 5. LiteLLM 헬스체크
curl http://localhost:4000/health

# 6. ClickHouse 읽기 전용 계정 확인
docker compose exec clickhouse clickhouse-client \
  --user labs_readonly --password <password> \
  --query "SELECT 1"

# 7. Redis 연결 확인
docker compose exec redis redis-cli ping  # PONG

# 8. sandbox 이미지 빌드
docker build -t ax-eval-sandbox:1.0.0 docker/eval-sandbox/
```

### 테스트 명세 참조
- TEST_SPEC.md Phase 1 (인프라 테스트 23개)

---

## Phase 2: Backend 기초

### 선행 조건
- Phase 1 완료 (인프라 서비스 정상 가동)
- Python 3.12+ 설치
- `backend/` 디렉토리 내 가상환경 구성

**TDD 순서**: 테스트 먼저 작성 (TEST_SPEC 참조) → 프로덕션 코드 구현 → 테스트 통과 확인

### 작업 목록

#### 2-1. FastAPI 프로젝트 구조
```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 앱 인스턴스, 라우터 등록, CORS
│   ├── api/                  # API 라우터
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── health.py     # 헬스체크 라우터
│   ├── core/                 # 설정, 의존성, 미들웨어
│   │   ├── __init__.py
│   │   ├── config.py         # pydantic-settings 기반 설정
│   │   ├── deps.py           # FastAPI 의존성 주입
│   │   └── security.py       # JWT 인증 미들웨어
│   ├── services/             # 비즈니스 로직
│   │   ├── __init__.py
│   │   ├── langfuse_client.py
│   │   └── redis_client.py
│   └── models/               # Pydantic 모델 (요청/응답 스키마)
│       └── __init__.py
├── tests/
├── requirements.txt
└── pyproject.toml
```

#### 2-2. Config 모듈 (`app/core/config.py`)
- `pydantic-settings`의 `BaseSettings` 상속
- `.env` 파일에서 환경변수 로드
- 필수 설정: Langfuse 키, LiteLLM URL, ClickHouse 접속 정보, Redis URL, JWKS URL
- 프로젝트별 Langfuse API Key 매핑 구조 포함

#### 2-3. JWT 인증 미들웨어 (`app/core/security.py`)
- JWKS 엔드포인트에서 공개키를 가져와 JWT 서명 검증
- JWT payload에서 role/groups 추출
- RBAC 데코레이터:
  - `admin`: Custom Code Evaluator 실행, 설정 변경, 삭제
  - `user`: 실험 생성/실행, 데이터셋 업로드
  - `viewer`: 읽기 전용
- 401/403 응답 처리

#### 2-4. Langfuse 클라이언트 래퍼 (`app/services/langfuse_client.py`)
- 모든 Langfuse SDK 호출을 중앙 관리
- 프로젝트별 API Key 전환 지원
- 주요 메서드: `get_prompt()`, `create_trace()`, `create_generation()`, `score()`, `get_dataset()`, `create_dataset_item()`, `flush()`
- 에러 핸들링 및 재시도 로직

#### 2-5. Redis 클라이언트 래퍼 (`app/services/redis_client.py`)
- 실험 상태 저장/조회 (TTL 24시간)
- 실험 진행률 관리
- 연결 풀 설정

#### 2-6. 헬스체크 엔드포인트 (`GET /api/v1/health`)
- 응답: Backend 자체 상태
- 의존 서비스 연결 상태 확인: Langfuse, LiteLLM, ClickHouse, Redis
- 각 서비스별 OK/FAIL 상태 반환

### 산출물
- FastAPI 서버가 `uvicorn`으로 기동되고 `/api/v1/health`에서 모든 서비스 연결 상태 확인 가능
- JWT가 없는 요청은 401 반환
- 유효한 JWT로 요청 시 200 반환

### 검증 방법
```bash
# 1. Backend 서버 기동
cd backend && uvicorn app.main:app --reload --port 8000

# 2. 헬스체크 (인증 없이 접근 가능)
curl http://localhost:8000/api/v1/health
# 응답: {"langfuse": "ok", "litellm": "ok", "clickhouse": "ok", "redis": "ok"}

# 3. 인증 없이 보호된 엔드포인트 접근
curl http://localhost:8000/api/v1/prompts
# 응답: 401 Unauthorized

# 4. JWT로 접근
curl -H "Authorization: Bearer <valid_jwt>" http://localhost:8000/api/v1/health
# 응답: 200 OK
```

### 테스트 명세 참조
- TEST_SPEC.md Phase 2 (Backend 기초 53개)

---

## Phase 3: Core APIs

### 선행 조건
- Phase 2 완료 (FastAPI 서버 기동, Langfuse/Redis 클라이언트 작동)
- Langfuse에 테스트용 프로젝트, 프롬프트, 데이터셋이 최소 1개씩 존재

**TDD 순서**: 테스트 먼저 작성 (TEST_SPEC 참조) → 프로덕션 코드 구현 → 테스트 통과 확인

### 작업 목록

#### 3-1. Prompt API (`app/api/v1/prompts.py`)
Langfuse Prompt Management API를 프록시한다.

| 엔드포인트 | 내부 동작 |
|------------|-----------|
| `GET /api/v1/prompts` | Langfuse `GET /api/public/v2/prompts` 프록시 |
| `GET /api/v1/prompts/{name}` | 프롬프트 조회 + `{{variable}}` 파싱하여 variables 필드 추가 |
| `GET /api/v1/prompts/{name}/versions` | 버전 목록 |
| `POST /api/v1/prompts` | 프롬프트 생성/업데이트 (user 이상) |
| `PATCH /api/v1/prompts/{name}/versions/{version}/labels` | 라벨 승격 (admin만) |

#### 3-2. Dataset API (`app/api/v1/datasets.py`)

| 엔드포인트 | 내부 동작 |
|------------|-----------|
| `GET /api/v1/datasets` | Langfuse 데이터셋 목록 |
| `GET /api/v1/datasets/{name}/items` | 아이템 조회 (페이지네이션) |
| `POST /api/v1/datasets/upload` | 파일 파싱 (CSV/JSON/JSONL) + 매핑 + Langfuse 업로드 |
| `POST /api/v1/datasets/upload/preview` | 업로드 미리보기 (첫 5건) |
| `DELETE /api/v1/datasets/{name}` | 데이터셋 삭제 (admin만) |

파일 파싱 시:
- 인코딩 자동 감지 (UTF-8, EUC-KR 등)
- 파일 크기 제한 50MB
- 컬럼 매핑: input_columns, output_column, metadata_columns

#### 3-3. Model API (`app/api/v1/models.py`)

| 엔드포인트 | 내부 동작 |
|------------|-----------|
| `GET /api/v1/models` | LiteLLM Proxy `/model/info` 프록시, 프로바이더별 그룹핑 |

#### 3-4. Project API (`app/api/v1/projects.py`)

| 엔드포인트 | 내부 동작 |
|------------|-----------|
| `GET /api/v1/projects` | 설정 파일 기반 프로젝트 목록 반환 |
| `POST /api/v1/projects/switch` | 해당 프로젝트의 Langfuse API Key로 클라이언트 전환 |

#### 3-5. Search API (`app/api/v1/search.py`)

| 엔드포인트 | 내부 동작 |
|------------|-----------|
| `GET /api/v1/search?q=...&type=...` | 프롬프트, 데이터셋, 실험을 통합 검색 |

### 산출물
- curl/httpie/Postman으로 모든 API 호출 가능
- Langfuse에 있는 프롬프트 조회, 데이터셋 업로드/조회, 모델 목록 확인 가능
- Frontend 없이 API만으로 핵심 데이터 CRUD 동작 검증 완료

### 검증 방법
```bash
# 1. 프롬프트 목록 조회
curl -H "Authorization: Bearer <jwt>" \
  "http://localhost:8000/api/v1/prompts?project_id=<id>"

# 2. 프롬프트 상세 + 변수 파싱
curl -H "Authorization: Bearer <jwt>" \
  "http://localhost:8000/api/v1/prompts/sentiment-analysis?version=3"
# 응답에 variables: ["input_text", "rules"] 포함 확인

# 3. 데이터셋 업로드
curl -X POST -H "Authorization: Bearer <jwt>" \
  -F "file=@test.csv" -F "dataset_name=test-dataset" \
  -F 'mapping={"input_columns":["text"],"output_column":"label"}' \
  "http://localhost:8000/api/v1/datasets/upload"

# 4. 모델 목록 확인
curl -H "Authorization: Bearer <jwt>" \
  "http://localhost:8000/api/v1/models"

# 5. 프로젝트 전환
curl -X POST -H "Authorization: Bearer <jwt>" \
  -d '{"project_id":"proj_123"}' \
  "http://localhost:8000/api/v1/projects/switch"
```

### 테스트 명세 참조
- TEST_SPEC.md Phase 3 (Core API 116개)

---

## Phase 4: 실험 실행 엔진

### 선행 조건
- Phase 3 완료 (Prompt API, Dataset API, Model API 작동)
- Langfuse에 프롬프트와 데이터셋 준비
- LiteLLM Proxy에 최소 1개 모델 등록

**TDD 순서**: 테스트 먼저 작성 (TEST_SPEC 참조) → 프로덕션 코드 구현 → 테스트 통과 확인

### 작업 목록

#### 4-1. Context Engine (`app/services/context_engine.py`)
- 프롬프트 템플릿에서 `{{variable_name}}` 패턴을 파싱
- 데이터셋 아이템의 input 필드를 변수에 바인딩
- 변수 타입 처리: text, json, file, list
- Langfuse SDK의 `prompt.compile(**variables)` 활용

#### 4-2. Single Test Runner (`app/services/single_test_runner.py`)
`POST /api/v1/tests/single` 엔드포인트의 핵심 로직.

실행 흐름:
1. Context Engine으로 프롬프트 변수 바인딩
2. LiteLLM Proxy로 LLM 호출 (SSE 스트리밍)
3. 응답 수신 중 SSE로 Frontend에 토큰 스트리밍
4. 응답 완료 후 Langfuse에 trace + generation 기록
5. `litellm.completion_cost()`로 비용 계산
6. usage (input_tokens, output_tokens), latency_ms, cost_usd 반환

SSE 이벤트:
- `event: token` — 스트리밍 토큰
- `event: done` — 완료 메타데이터 (trace_id, usage, latency, cost)
- `event: error` — 에러 정보

#### 4-3. Batch Experiment Runner (`app/services/batch_runner.py`)
`POST /api/v1/experiments` 엔드포인트의 핵심 로직.

실행 흐름:
1. 실험 설정 수신 (프롬프트 버전들 x 모델들 = N개 Run)
2. Redis에 실험 상태 초기화 (status: running, progress: 0/total)
3. 각 Run에 대해:
   - 데이터셋 아이템 순회
   - `asyncio.Semaphore(concurrency)`로 동시 실행 제한
   - 아이템별: Context Engine → LiteLLM 호출 → Langfuse trace/generation 기록
   - 진행률 Redis 업데이트
   - SSE로 Frontend에 progress 이벤트 전송
4. 모든 아이템 완료 후 실험 결과 집계

SSE 이벤트:
- `event: progress` — 진행률 (completed/total, current_item)
- `event: run_complete` — Run 완료 요약 (avg_score, total_cost)
- `event: experiment_complete` — 실험 전체 완료
- `event: error` — 아이템별 에러

#### 4-4. 실험 제어 (`app/services/experiment_control.py`)
Redis Lua 스크립트 기반 원자적 상태 전이.

| 엔드포인트 | 상태 전이 |
|------------|-----------|
| `POST /experiments/{id}/pause` | running → paused |
| `POST /experiments/{id}/resume` | paused → running |
| `POST /experiments/{id}/cancel` | running/paused → cancelled |
| `POST /experiments/{id}/retry-failed` | completed/failed → running (실패 아이템만) |

- 이미 cancelled인 실험은 재시작 불가 (409 Conflict)
- 상태 전이 규칙은 Lua 스크립트로 Redis에서 원자적으로 처리
- 실험 완료 시 최종 상태를 Langfuse trace metadata로 영속화

### 산출물
- 단일 테스트: curl로 SSE 스트리밍 응답 수신 가능
- 배치 실험: N개 아이템에 대해 LLM 호출 → Langfuse 기록 → SSE 진행률 확인
- 실험 일시 정지/재개/취소 동작 확인
- Langfuse UI에서 trace/generation 기록 확인 가능

### 검증 방법
```bash
# 1. 단일 테스트 (스트리밍)
curl -N -X POST -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "...",
    "prompt": {"source": "langfuse", "name": "test-prompt", "version": 1},
    "variables": {"input_text": "테스트 입력"},
    "model": "gpt-4o",
    "parameters": {"temperature": 0.1},
    "stream": true
  }' \
  "http://localhost:8000/api/v1/tests/single"
# SSE 스트림으로 토큰 수신 확인

# 2. 배치 실험 생성
curl -X POST -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "...",
    "name": "테스트 실험",
    "prompt_configs": [{"name": "test-prompt", "version": 1}],
    "dataset_name": "test-dataset",
    "model_configs": [{"model": "gpt-4o", "parameters": {"temperature": 0.1}}],
    "evaluators": [{"type": "built_in", "name": "json_validity"}],
    "concurrency": 3
  }' \
  "http://localhost:8000/api/v1/experiments"

# 3. 실험 진행 SSE 스트림
curl -N "http://localhost:8000/api/v1/experiments/<id>/stream"

# 4. 실험 일시 정지
curl -X POST "http://localhost:8000/api/v1/experiments/<id>/pause"

# 5. Langfuse UI에서 trace 확인
# localhost:3001 → 해당 프로젝트 → Traces 탭에서 기록 확인
```

### 테스트 명세 참조
- TEST_SPEC_PART2.md Phase 4 (실험 엔진 48개)

---

## Phase 5: 평가 시스템

### 선행 조건
- Phase 4 완료 (실험 실행 엔진 작동, LLM 호출 및 Langfuse trace 기록 가능)
- ax-eval-sandbox Docker 이미지 빌드 완료 (Phase 1-5)

**TDD 순서**: 테스트 먼저 작성 (TEST_SPEC 참조) → 프로덕션 코드 구현 → 테스트 통과 확인

### 작업 목록

#### 5-1. Built-in Evaluators (`app/evaluators/built_in.py`)
13개 내장 평가 함수 구현.

| 함수 | 반환값 | 비고 |
|------|--------|------|
| `exact_match` | 0/1 | 대소문자 무시, 공백 정규화 옵션 |
| `contains` | 0/1 | AND/OR 조건 |
| `regex_match` | 0/1 | |
| `json_validity` | 0/1 | |
| `json_schema_match` | 0/1 | jsonschema 패키지 사용 |
| `json_key_presence` | 0.0~1.0 | 필수 키 존재 비율 |
| `levenshtein_similarity` | 0.0~1.0 | 편집 거리 기반 |
| `cosine_similarity` | 0.0~1.0 | LiteLLM 통해 임베딩, text-embedding-3-small 기본 |
| `bleu` | 0.0~1.0 | n-gram 정밀도 |
| `rouge` | 0.0~1.0 | ROUGE-L |
| `latency_check` | 0/1 | 임계값 비교 |
| `token_budget_check` | 0/1 | 출력 토큰 예산 |
| `cost_check` | 0/1 | 비용 임계값 |

모든 evaluator는 동일한 인터페이스: `(output, expected, metadata, **config) -> float`

#### 5-2. LLM-as-Judge (`app/evaluators/llm_judge.py`)
- Judge 프롬프트 조립: rubric + input + output + expected → Judge LLM 호출
- 응답 파싱: `{"score": 0-10, "reasoning": "..."}`
- 0-10 스코어를 0.0-1.0으로 정규화 (`score / 10`)
- 재시도 로직: 파싱 실패 시 최대 2회 재시도, 3회 실패 시 `score=null`
- Judge 모델 설정: 기본 gpt-4o, temperature 0.0
- 기본 제공 Judge 프롬프트: 정확성, 관련성, 일관성, 유해성, 자연스러움
- 커스텀 Judge 프롬프트 지원: `{input}`, `{output}`, `{expected}` 자동 치환

#### 5-3. Custom Code Evaluator (`app/evaluators/custom_code.py`)
Docker 샌드박스에서 사용자 작성 Python 코드를 실행한다.

구성 요소:
- **이미지**: Phase 1에서 빌드한 `ax-eval-sandbox`
- **runner.py**: 컨테이너 내부에서 실행되는 스크립트
  - stdin으로 JSON 수신: `{"code": "...", "output": "...", "expected": "...", "metadata": {...}}`
  - 코드를 동적 로드하여 `evaluate()` 함수 실행
  - stdout으로 JSON 반환: `{"score": 0.85}` 또는 `{"error": "..."}`
- **라이프사이클 관리**:
  - 실험 시작 시 컨테이너 1개 생성
  - 전체 아이템에 대해 해당 컨테이너에서 반복 실행
  - 실험 완료 또는 취소 시 컨테이너 삭제
  - 아이템마다 컨테이너를 생성하지 않는다 (성능)
- **보안 제약**: 네트워크 없음, 볼륨 없음, non-root, 5초 타임아웃, 128MB 메모리
- **권한**: admin 역할만 Custom Code Evaluator 사용 가능

#### 5-4. Evaluation Pipeline Orchestrator (`app/evaluators/pipeline.py`)
평가 함수들을 조합하여 병렬 실행하고 결과를 Langfuse에 기록한다.

```
모델 응답 수신
  → [병렬] Built-in evaluators (즉시, <10ms)
  → [병렬] Custom evaluators (샌드박스, <5s)
  → [순차/병렬] LLM-as-Judge (LLM 호출, ~1-3s)
  → 모든 스코어 수집
  → langfuse.score(trace_id, name, value) × N
```

에러 처리:
- 개별 evaluator 실패 시 `score=null`로 기록, 실험은 계속 진행
- 모든 evaluator 실패 시 아이템을 "평가 실패"로 표시
- LLM Judge 비용은 실험 비용에 별도 집계

### 산출물
- 단일 테스트 시 evaluators를 지정하면 스코어가 Langfuse에 기록됨
- 배치 실험 시 각 아이템마다 선택한 평가 함수들이 병렬 실행됨
- Custom Code Evaluator: 코드 검증 API (`POST /api/v1/evaluators/validate`)로 사전 테스트 가능
- Langfuse UI의 Scores 탭에서 평가 결과 확인

### 검증 방법
```bash
# 1. 내장 평가 함수 목록 확인
curl "http://localhost:8000/api/v1/evaluators/built-in"

# 2. Custom Evaluator 코드 검증
curl -X POST -H "Authorization: Bearer <admin_jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "def evaluate(output, expected, metadata):\n    return 1.0 if output == expected else 0.0",
    "test_cases": [
      {"output": "positive", "expected": "positive", "metadata": {}},
      {"output": "negative", "expected": "positive", "metadata": {}}
    ]
  }' \
  "http://localhost:8000/api/v1/evaluators/validate"
# 응답: test_results[0].result = 1.0, test_results[1].result = 0.0

# 3. 단일 테스트에 evaluator 포함
curl -X POST -H "Authorization: Bearer <jwt>" \
  -d '{
    ...,
    "evaluators": [
      {"type": "built_in", "name": "json_validity"},
      {"type": "llm_judge", "name": "accuracy", "config": {"judge_model": "gpt-4o", "prompt": "..."}}
    ]
  }' \
  "http://localhost:8000/api/v1/tests/single"

# 4. Langfuse UI에서 Score 확인
# localhost:3001 → Traces → 해당 trace → Scores 탭
```

### 테스트 명세 참조
- TEST_SPEC_PART2.md Phase 5 (평가 시스템 40개)

---

## Phase 6: 분석

### 선행 조건
- Phase 5 완료 (실험 실행 + 평가 완료, Langfuse에 trace/score 데이터 축적)
- ClickHouse에 실험 데이터가 존재 (최소 2개 Run으로 비교 가능)

**TDD 순서**: 테스트 먼저 작성 (TEST_SPEC 참조) → 프로덕션 코드 구현 → 테스트 통과 확인

### 작업 목록

#### 6-1. ClickHouse 쿼리 모듈 (`app/services/clickhouse_client.py`)
- `clickhouse-connect` 드라이버 사용
- 연결 풀 설정
- 읽기 전용 계정 (`labs_readonly`)으로 접속
- 파라미터화된 쿼리 필수 (f-string, .format() 금지)
- LIMIT 없는 쿼리 자동 거부 (기본 LIMIT 10,000 추가)
- 쿼리 실행 래퍼: 파라미터 바인딩, 에러 핸들링, 결과 변환

#### 6-2. 쿼리 템플릿 (`app/services/clickhouse_queries.py`)
각 분석 유형에 대한 파라미터화된 SQL 템플릿 정의.

| 쿼리 | 용도 |
|------|------|
| 실험 간 요약 비교 | Run별 avg_latency, p50/p90/p99, total_cost, avg_tokens |
| 평가 스코어 비교 | Run별 score_name 기준 avg/min/max/stddev |
| 아이템별 상세 비교 | 동일 dataset_item_id에 대한 Run별 output/score |
| Outlier 감지 | score_variance가 큰 아이템 (threshold 0.3) |
| 비용 효율 분석 | Run별 score_per_dollar |
| 스코어 분포 | 히스토그램 bin별 count |

#### 6-3. Compare API (`app/api/v1/analysis.py`)

| 엔드포인트 | 기능 |
|------------|------|
| `POST /api/v1/analysis/compare` | 요약 비교 (latency, cost, tokens, scores) |
| `POST /api/v1/analysis/compare/items` | 아이템별 상세 비교 (score_variance 정렬) |
| `GET /api/v1/analysis/scores/distribution` | 스코어 분포 히스토그램 + 통계 |

### 산출물
- 2개 이상의 Run을 선택하여 요약/상세 비교 가능
- ClickHouse 쿼리 결과가 JSON으로 반환
- Frontend 없이 curl로 분석 결과 확인 가능

### 검증 방법
```bash
# 1. 요약 비교
curl -X POST -H "Authorization: Bearer <jwt>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "...",
    "run_names": ["run_a", "run_b"]
  }' \
  "http://localhost:8000/api/v1/analysis/compare"
# 응답: 각 Run의 metrics + scores 비교 데이터

# 2. 아이템별 상세 비교
curl -X POST -H "Authorization: Bearer <jwt>" \
  -d '{
    "project_id": "...",
    "run_names": ["run_a", "run_b"],
    "score_name": "exact_match",
    "sort_by": "score_variance",
    "sort_order": "desc"
  }' \
  "http://localhost:8000/api/v1/analysis/compare/items"

# 3. 스코어 분포
curl "http://localhost:8000/api/v1/analysis/scores/distribution?\
project_id=...&run_name=run_a&score_name=exact_match&bins=10"
```

### 테스트 명세 참조
- TEST_SPEC_PART2.md Phase 6 (분석 7개)

---

## Phase 7: Frontend

### 선행 조건
- Phase 6 완료 (모든 Backend API 작동)
- Node.js 20+ 설치
- 디자인 토큰 정의 완료 (UI_UX_DESIGN.md 참조)

**TDD 순서**: 컴포넌트 테스트는 TDD, E2E 테스트는 구현 후 작성

### 작업 목록

#### 7-0. 프로젝트 셋업
- Next.js 15 (App Router) 프로젝트 생성
- Tailwind CSS v4 설정
- shadcn/ui 컴포넌트 라이브러리 설치
- 폰트 설정: Pretendard (한글), Inter (영문/숫자), JetBrains Mono (코드)
- API 클라이언트 모듈 (`lib/api.ts`): fetch 래퍼, JWT 자동 첨부, 에러 핸들링
- SSE 클라이언트 유틸리티 (`lib/sse.ts`)

#### 7-1. 인증 통합
- JWT를 메모리에 저장 (localStorage 금지)
- 401 응답 시 로그인 페이지로 리다이렉트
- RBAC 기반 UI 렌더링: admin/user/viewer 역할에 따라 버튼/메뉴 표시/숨김
- 인증 컨텍스트 Provider

#### 7-2. 레이아웃 셸
- **Top Bar** (48px): 로고 + 프로젝트 선택 드롭다운 (좌측), 사용자 메뉴 (우측)
- **Side Nav** (56px): 아이콘만 표시, hover 시 라벨 툴팁
  - 실험, 결과, 데이터셋, 프롬프트, 평가, 설정
  - 현재 페이지는 accent 색상 배경 (indigo-400)
- **Main Content**: Side Nav 제외 전체 너비

#### 7-3. 페이지 구현 (구현 순서대로)

아래 순서는 의존성과 복잡도를 고려한 것이다. 단순한 페이지부터 시작하여 점진적으로 복잡한 페이지를 구축한다.

---

**페이지 1: 설정 (Settings)**

가장 단순한 페이지. 인프라 연결 상태를 확인하여 전체 셋업이 올바른지 검증한다.

- 프로젝트 전환 (드롭다운 + `POST /projects/switch`)
- LiteLLM에 등록된 모델 목록 표시 (`GET /models`)
- 의존 서비스 연결 상태 표시 (Langfuse, LiteLLM, ClickHouse, Redis)
- 상태 표시: emerald dot (정상), rose dot (실패)

검증: 페이지 로드 시 모든 서비스 상태가 표시되고, 프로젝트 전환이 동작한다.

---

**페이지 2: 데이터셋 관리**

파일 업로드, 목록 조회, 아이템 브라우징.

- 데이터셋 목록 (`GET /datasets`): 이름, 아이템 수, 생성일
- 파일 업로드 위저드:
  1. 파일 선택 (CSV/JSON/JSONL, drag & drop)
  2. 컬럼 매핑 UI (input_columns, output_column, metadata_columns)
  3. 미리보기 (`POST /datasets/upload/preview`, 첫 5건)
  4. 업로드 실행 (`POST /datasets/upload`)
- 데이터셋 아이템 브라우징 (페이지네이션)
- 데이터셋 삭제 (admin만)

검증: CSV 파일 업로드 → 매핑 → 미리보기 → 업로드 완료 → 목록에 표시 → 아이템 조회.

---

**페이지 3: 프롬프트 관리**

Langfuse Prompt Management 연동.

- 프롬프트 목록 (`GET /prompts`): 이름, 최신 버전, 라벨, 태그
- 버전 브라우징 (`GET /prompts/{name}/versions`)
- 프롬프트 상세 보기: 텍스트/chat 형식 표시, 변수 하이라이팅
- 라벨 승격 (`PATCH /prompts/{name}/versions/{version}/labels`): "production" 라벨 부여 (admin만)
- 프롬프트 생성/업데이트 (`POST /prompts`)

검증: 프롬프트 목록 → 버전 선택 → 상세 보기에서 변수 확인 → 라벨 승격 동작.

---

**페이지 4: 단일 테스트**

좌우 분할 레이아웃, SSE 스트리밍, 멀티모달 입력.

좌측 패널 (45%):
- 프롬프트 선택 (이름 + 버전/라벨)
- 프롬프트 에디터 (CodeMirror, `{{variable}}` 하이라이팅)
- 변수 자동 감지 → 입력 폼 자동 생성
- System Prompt (접이식, 기본 닫힘)
- 파라미터 설정 (접이식): temperature, top_p, max_tokens 등
- 이미지 첨부 (drag & drop, 미리보기)

우측 패널 (55%):
- 모델 선택 드롭다운 + 실행 버튼 (상단 고정)
- SSE 스트리밍 응답 영역 (실시간 토큰 렌더링)
- 중단 버튼 (응답 생성 중)
- 완료 후 메타데이터: latency, tokens (input/output), cost
- 이전 실행 히스토리 (최근 5건)

검증: 프롬프트 로드 → 변수 입력 → 실행 → 스트리밍 응답 수신 → 메타데이터 확인.

---

**페이지 5: 배치 실험**

4단계 위저드 + 실험 목록 + 진행 모니터링.

실험 목록:
- 실험명, 상태 (dot: emerald=완료, amber+pulse=진행중, rose=실패), Runs 수, 생성일, 비용
- 상태 필터, 검색

실험 생성 위저드:
1. 기본 설정: 이름, 설명, 프롬프트 선택 (복수), 데이터셋 선택
2. 모델 선택: 모델 선택 (복수), 모델별 파라미터 설정
3. 평가 설정: Built-in 선택 (체크박스), LLM Judge 설정, Custom Code 입력
4. 확인: 총 실행 건수 (아이템 x 프롬프트 x 모델), 예상 비용, 설정 요약

실험 진행 모니터링:
- SSE 기반 진행률 바 (completed/total)
- Run별 상태 표시
- 일시 정지/재개/취소 버튼
- 실시간 스코어 스파크라인

검증: 실험 생성 (위저드 4단계) → 실행 → 진행률 실시간 업데이트 → 완료 후 결과 확인.

---

**페이지 6: 평가 관리**

평가 함수 설정 UI.

- Built-in Evaluator 목록: 이름, 설명, 반환 타입, 파라미터 설정
- LLM-as-Judge 설정:
  - Judge 모델 선택
  - 기본 제공 프롬프트 5종 (정확성, 관련성, 일관성, 유해성, 자연스러움)
  - 커스텀 Judge 프롬프트 에디터
  - 비용 추정 표시
- Custom Code Evaluator:
  - Python 코드 에디터 (CodeMirror, Python 구문 하이라이팅)
  - 테스트 케이스 입력 + 검증 실행 (`POST /evaluators/validate`)
  - 실행 결과 표시
  - admin 권한 필요 경고

검증: Built-in 목록 확인 → LLM Judge 프롬프트 설정 → Custom Code 작성 → 검증 실행 → 결과 확인.

---

**페이지 7: 결과 비교**

차트와 테이블로 실험 결과를 시각화한다.

상단 - 비교 Run 선택:
- Run 선택 (복수, 최소 2개)
- 선택된 Run들의 프롬프트 버전, 모델 정보 표시

KPI 카드 (요약):
- Best Score (어떤 Run), Fastest (어떤 Run), Cheapest (어떤 Run)
- 상대 비교 수치 표시 (% 차이)

상세 비교 (탭):
- 스코어 탭: 분포 히스토그램 (Recharts), Run별 통계 (avg, stddev)
- 지연시간 탭: P50/P90/P99 비교
- 비용 탭: 총 비용 bar chart, score_per_dollar 효율
- 토큰 탭: input/output 토큰 비교

아이템별 비교 테이블:
- score_variance 기준 정렬 (outlier 우선)
- 각 아이템의 input → output → expected → score 나란히 보기
- 스코어 배지 색상: emerald(높음), amber(중간), rose(낮음)

차트 라이브러리: Recharts 사용.

검증: 2개 이상 Run 선택 → KPI 카드에 승자 표시 → 차트 렌더링 → 아이템별 비교 테이블 정렬 → outlier 확인.

### 산출물
- 모든 7개 페이지가 동작하는 완전한 웹 애플리케이션
- 프롬프트 관리 → 데이터셋 준비 → 실험 실행 → 평가 → 결과 비교의 전체 워크플로우가 UI에서 완결

### 검증 방법
```bash
# 1. Frontend 개발 서버 기동
cd frontend && npm run dev

# 2. 브라우저에서 접속
open http://localhost:3000

# 3. 전체 워크플로우 수동 테스트
# 설정 → 프로젝트 선택 → 서비스 상태 확인
# 데이터셋 → CSV 업로드 → 매핑 → 미리보기 → 완료
# 프롬프트 → 목록 → 버전 확인
# 단일 테스트 → 프롬프트 로드 → 변수 입력 → 실행 → 스트리밍 응답
# 배치 실험 → 위저드 4단계 → 실행 → 진행률 모니터링
# 평가 → Built-in 확인 → Custom Code 검증
# 결과 비교 → Run 선택 → KPI/차트/테이블 확인
```

### 테스트 명세 참조
- TEST_SPEC_PART2.md Phase 7 (Frontend 15개) + 엣지케이스 52개

---

## Phase 간 의존성 요약

```
Phase 0 (테스트 인프라)
  └─▶ Phase 1 (인프라)
        └─▶ Phase 2 (Backend 기초)
              └─▶ Phase 3 (Core APIs)
                    └─▶ Phase 4 (실험 실행 엔진)
                          └─▶ Phase 5 (평가 시스템)
                                └─▶ Phase 6 (분석)
                                      └─▶ Phase 7 (Frontend)

Phase 1-5 (sandbox 이미지) ──▶ Phase 5-3 (Custom Code Evaluator)
```

- Phase 0~6은 순차적 의존성
- Phase 7 (Frontend)은 Phase 6 완료 후 시작하는 것이 이상적이나, API 스펙 확정 후 병렬 개발 가능
- Frontend 페이지 간에도 순서가 있음: 설정 → 데이터셋 → 프롬프트 → 단일 테스트 → 배치 → 평가 → 비교

---

## 전체 마일스톤 체크리스트

| Phase | 핵심 마일스톤 | 검증 기준 |
|-------|-------------|-----------|
| 0 | 테스트 인프라 구축 (backend/tests, frontend/tests, CI) | `pytest --collect-only` 성공, vitest 실행 가능, CI 워크플로우 정의 완료 |
| 1 | `docker compose -f docker/docker-compose.yml up -d postgres clickhouse redis langfuse litellm`으로 인프라 가동 | 모든 서비스 healthy, Langfuse/LiteLLM 접속 가능 |
| 2 | FastAPI 서버 기동, JWT 인증 작동 | `/health` 200, 인증 없는 요청 401 |
| 3 | Langfuse 프록시 API 전체 동작 | curl로 프롬프트/데이터셋/모델 CRUD 가능 |
| 4 | 단일 테스트 SSE 스트리밍, 배치 실험 실행 | LLM 응답 수신, Langfuse trace 기록, 실험 제어 |
| 5 | 13개 Built-in + LLM Judge + Custom Code 실행 | Langfuse Score 기록, sandbox 격리 동작 |
| 6 | ClickHouse 분석 쿼리, Run 비교 API | 2개 Run 비교 결과 JSON 반환 |
| 7 | 전체 UI 워크플로우 완결 | 프롬프트→데이터셋→실험→평가→비교 UI 동작 |
