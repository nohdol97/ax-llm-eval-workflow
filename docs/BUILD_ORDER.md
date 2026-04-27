# 빌드 순서 가이드

프로젝트를 처음부터 구축할 때의 단계별 빌드 순서.
각 Phase는 이전 Phase의 산출물에 의존하므로, 반드시 순서대로 진행한다.

---

## 사내 공용 인프라 의존 [Canonical]

> **본 절은 본 프로젝트(ax-llm-eval-workflow)의 인프라 분담의 단일 진실 원본이다.** Phase 1·7·8 및 마이그레이션/롤백/체크리스트 절은 본 절을 참조한다.
>
> **실행 가이드**: 본 절을 실제 사내 협의·티켓 발행·검증 단계로 풀어쓴 운영 체크리스트는 [`INFRA_INTEGRATION_CHECKLIST.md`](INFRA_INTEGRATION_CHECKLIST.md) 참조.

본 프로젝트는 **사내 공용 인프라**의 다음 서비스를 사용한다. 별도 기동/운영하지 않으며, 사내 인프라팀과의 사전 합의(API Key 발급, 모델 등록, scrape job 추가 등)가 Phase 1 시작 조건이다.

| 서비스 | 사용 목적 | 본 프로젝트 책임 | 사내 인프라팀 책임 |
|--------|-----------|----------------|------------------|
| **Langfuse** | trace/score/dataset/prompt 영속화 | Public/Secret Key 발급 요청, SDK 호출, score config idempotent 등록 | 가용성, 백업, 버전 관리, 내부 의존성(Postgres/ClickHouse/MinIO) 운영 |
| **LiteLLM Proxy** | LLM Gateway | 모델 등록 PR/티켓 제출, Virtual Key 사용 (`success_callback: []` 합의) | Master Key·Provider 키 관리, rate limit, config 적용 |
| **Prometheus** | 메트릭 수집/조회 | `/metrics` 엔드포인트 노출, scrape job·recording rule(`ax:*`)·alert rule PR 제출 | scrape, 룰 적용, Alertmanager 라우팅 |
| **OpenTelemetry Collector** | 분산 트레이스 (OTLP) | OTel SDK 통합, traces export, sampling 전략(parentbased ratio) | Collector 가용성, 인증 토큰 발급, 백엔드(Tempo/Jaeger) 라우팅 |
| **Loki** | 구조화 로그 수집 | stdout JSON 로그 + 라벨 규약 준수, **PII 미포함** 강제 | 수집기(Promtail/Vector) 운영, 보존 정책 |
| **ClickHouse** (Langfuse 내부) | 분석 쿼리 직접 실행 | readonly 계정 발급 요청, parameterized 쿼리 작성, LIMIT 강제 | 계정 발급(`GRANT SELECT ON langfuse.*` 한정), TLS 강제, 네트워크 ACL |

**본 프로젝트 자체 운영 컴포넌트**:
- Labs Backend (FastAPI), Labs Frontend (Next.js)
- `ax-eval-sandbox` Docker 이미지 (Custom Code Evaluator 격리 — 작업 1-5)
- **Redis (Labs 전용)**: 실험 상태/진행률 저장. 운영 정책 두 가지 중 하나 선택 (Phase 1 진입 시점에 합의):
  1. **1순위**: 사내 공용 Redis의 별도 DB(`LABS_REDIS_DB=1`) 임차 — 운영 부담 최소
  2. **차선**: 자체 단일 컨테이너(`redis:7`)를 본 프로젝트 compose에서 기동 — 사내 공용 Redis 임차 거부 시

**Phase 1 시작 선결 조건** (모두 합의/발급 완료되어야 Phase 1 진입):
1. Langfuse organization/project 생성 + Public/Secret Key 발급
2. LiteLLM 모델 등록 + 본 프로젝트 전용 Virtual Key 발급 (사용량/예산/rate limit 분리, callback 비활성)
3. ClickHouse `labs_readonly` 계정 발급 **또는** Langfuse 공개 API 폴백 합의 (작업 1-4 참조)
4. Redis 정책 결정 (1순위 임차 시 DB 번호 합의, 차선 시 자체 컨테이너 인입)
5. Prometheus scrape 대상 URL(예: `http://backend.labs.internal:8000/metrics`) 사전 협의
6. OTel Collector OTLP/HTTP 엔드포인트 + 인증 토큰 발급
7. Loki 라벨 규약(예: `service=ax-llm-eval-workflow-backend, env=production`) + 수집기 합의
8. 사내 Auth 서비스 JWKS URL + audience/issuer 합의 (RBAC: admin/user/viewer)
9. 네트워크 경로 검증 (Backend → Langfuse/LiteLLM/ClickHouse/Redis/Prometheus/OTel/Loki 모두 도달성)
10. ADR-011 시크릿 관리 정책 작성 완료 (작업 1-9)

선결 조건 미충족 시 Phase 1 작업은 차단되며, 누락 항목별로 사내 인프라팀에 별도 티켓을 발행한다. 본 절의 표는 Phase 1·7·8 작업 항목 결정의 기준이다.

---

## Phase 0: 테스트 인프라 구축

### 선행 조건
- 없음 (최우선 작업)

**TDD 순서**: Phase 0 자체는 테스트 인프라를 구축하는 단계이므로 TDD 대상이 아니다. 구축 후 `pytest --collect-only`와 vitest 실행으로 인프라 자체를 검증한다. 이후 Phase 2~6은 이 인프라 위에서 TDD(테스트 먼저 → 구현)를 적용한다.

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
- `backend/tests/fixtures/mock_langfuse.py` — MockLangfuseClient (TEST_SPEC.md 0.3 Mock Langfuse Client 참조)
- `backend/tests/fixtures/mock_redis.py` — MockRedisClient (TEST_SPEC.md 0.3 Mock Redis Client 참조)
- `backend/tests/fixtures/mock_litellm.py` — MockLiteLLMProxy (TEST_SPEC.md 0.3 Mock LiteLLM Proxy 참조)
- `backend/tests/fixtures/mock_clickhouse.py` — MockClickHouseClient (TEST_SPEC.md 0.3.6 참조)
- `backend/tests/fixtures/jwt_helper.py` — create_test_jwt() (TEST_SPEC.md 0.3 Test JWT Generator 참조)

#### 0-3. Frontend 테스트 설정
- vitest.config.ts 설정
- MSW (Mock Service Worker) 핸들러 기본 구조
- `frontend/tests/` 디렉토리 구조

#### 0-4. CI 파이프라인
- `.github/workflows/test.yml` — backend-unit, backend-integration, frontend-unit, lint 분리 (TEST_SPEC.md 0.4 참조)
- 잡 공통 단계 순서: ① `actions/checkout@v4` → ② `actions/setup-python@v5`(3.12, `cache: pip`) / `actions/setup-node@v4`(20, `cache: npm`) → ③ 의존성 설치(`pip install -e .[test]` / `npm ci`) → ④ 린트(ruff, eslint) → ⑤ 테스트 실행 → ⑥ 커버리지 업로드(`actions/upload-artifact@v4`).
- 의존성 설치는 setup 액션의 캐시 키가 적중한 직후에 수행하며, 인프라 의존 잡(backend-integration)은 `services:` 블록으로 postgres/redis/clickhouse를 먼저 기동한 뒤 `wait-for-it.sh`로 health 대기 후 설치/테스트 진행.

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

> **본 Phase는 「사내 공용 인프라 의존」 절(문서 상단 Canonical)을 전제로 한다.** 본 프로젝트는 Langfuse / LiteLLM / Prometheus / OpenTelemetry Collector / Loki / ClickHouse(Langfuse 내부)를 **자체 운영하지 않으며**, 자체 운영 대상은 Backend / Frontend / `ax-eval-sandbox` 이미지 / Redis(자체 또는 임차)에 한정된다.

### 선행 조건
- Docker, Docker Compose 설치
- 「사내 공용 인프라 의존」 절의 **Phase 1 시작 선결 조건 1~10번 모두 충족** (Langfuse/LiteLLM Virtual Key 발급, ClickHouse 계정 또는 폴백 합의, Redis 정책 결정, Prometheus/OTel/Loki 합의, JWKS URL, 네트워크 도달성, ADR-011)
- 프로젝트 디렉토리 구조 생성 완료 (`docker/`, `scripts/`, `backend/`, `frontend/`)
- LLM Provider API 키는 사내 LiteLLM이 단독 보관 — **본 프로젝트 `.env`에 포함 금지** (보안 규칙: CLAUDE.md "보안 규칙")

**TDD 순서**: 인프라 코드 구현 → 인프라 테스트로 검증 (인프라는 TDD보다 구현 후 검증이 적합)

### 작업 목록

#### 1-1. 자체 docker-compose 파일 세트 (단순화)
사내 공용 서비스를 외부 엔드포인트로 참조하므로, 본 프로젝트 compose는 **자체 운영 컴포넌트만** 정의한다.

| 파일 | 용도 | 비고 |
|------|------|------|
| `docker/docker-compose.yml` | 베이스 정의 — backend, frontend, (선택) redis | 외부 서비스는 `.env`로 주입, 내부 서비스는 `expose:`만 사용 |
| `docker/docker-compose.override.yml` | 로컬 개발용 (자동 병합) | 호스트 포트 노출(backend 8000, frontend 3100), 코드 볼륨 마운트, 핫리로드, `LABS_LOG_LEVEL=DEBUG` |
| `docker/docker-compose.demo.yml` | 데모 환경 | seed 컨테이너 포함(데이터셋·프롬프트·실험 + **score config 등록**, 모든 항목 idempotent: 이름/버전 기반 존재 검사 후 skip, `restart: on-failure`로 재시도해도 중복 생성 없음), 데모 도메인/리소스 제한, 읽기 전용 사용자 강제 |
| `docker/docker-compose.prod.yml` | 운영 환경 | 포트 노출 없음(사내 reverse proxy 전제), 리소스 제한, `restart: always`, secrets 마운트 |
| `docker/.env.example` / `.env.demo` / `.env.production` | 환경별 변수 템플릿 (사내 엔드포인트 참조) | 시크릿은 사내 secret store 경유 주입 |

기동 명령:
- 개발: `docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml up -d`
- 데모: `docker compose -f docker/docker-compose.yml -f docker/docker-compose.demo.yml --env-file docker/.env.demo up -d`
- 운영: `docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml --env-file docker/.env.production up -d`

| 서비스 | 이미지 | 포트 | 비고 |
|--------|--------|------|------|
| backend | (자체 빌드) | 8000 | FastAPI, `/metrics`·`/health` 노출 |
| frontend | (자체 빌드) | 3100 | Next.js 15 |
| redis | redis:7 | 6379 | **선택** — 사내 Redis 임차 시 본 서비스 생략. 임차 거부 시에만 기동 |

기동 순서(depends_on + healthcheck로 강제):
- (선택) redis healthy → backend(부팅 시 사내 Langfuse/LiteLLM/ClickHouse/Redis 헬스체크 + score config 등록) → frontend
- 데모는 backend healthy 이후 seed 컨테이너가 1회 실행된다.
- **사내 공용 서비스(Langfuse/LiteLLM/Prometheus/OTel/Loki)는 사전 기동 가정** — 본 compose의 `depends_on`에는 포함하지 않는다. Backend 부팅 시점에 헬스체크로 도달성을 확인하고 미도달 시 기동 실패 처리.

네트워크 분리:
- `frontend_net`: 외부 노출 (frontend → 사내 reverse proxy)
- `backend_net`: 내부 통신 (backend, redis 옵션). 사내 공용 서비스로의 egress는 사내 네트워크 정책(VPC/방화벽)에 의존

내부 서비스는 `expose:`만 사용, `ports:`는 사용하지 않는다.

#### 1-2. LiteLLM 모델 등록 요청 (사내 인프라팀)
사내 LiteLLM Proxy의 `config.yaml`에 본 프로젝트가 사용할 모델을 추가하는 변경 PR/티켓을 사내 인프라팀에 제출한다. **본 프로젝트는 `config.yaml`을 직접 작성/관리하지 않는다.**

요청 내용:
- 사용 모델 목록: Azure OpenAI(GPT-4o, GPT-4.1), Google Gemini(2.5 Pro/Flash), AWS Bedrock(Claude 4.5/Llama 3.3), Anthropic(Claude 4.6 Opus, 4.5 Sonnet), OpenAI(o3·o4-mini) 중 합의된 모델
- 본 프로젝트 전용 LiteLLM **Virtual Key** 발급 (사용량/예산/rate limit 분리)
- `success_callback: []` (Langfuse callback 명시적 비활성화 — Labs Backend가 trace/generation 기록 전담)
- 모델 식별자(`provider/model-name`)는 본 프로젝트 `frontend/src/lib/mock/data.ts`의 모델 카탈로그와 사전 일치 합의

본 프로젝트 측: 발급받은 `LITELLM_BASE_URL` + `LITELLM_VIRTUAL_KEY`만 환경변수로 주입.

#### 1-3. .env.example (사내 인프라 엔드포인트 참조)
모든 환경변수의 템플릿. 실제 값은 `.env`에 작성하고 gitignore.

```
# === 사내 공용 인프라 엔드포인트 ===

# Langfuse (사내 공용)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://langfuse.internal.example.com

# LiteLLM (사내 공용)
LITELLM_VIRTUAL_KEY=                                # 본 프로젝트 전용 Virtual Key
LITELLM_BASE_URL=https://litellm.internal.example.com

# ClickHouse (Langfuse 내부, 사내가 readonly 계정 발급)
CLICKHOUSE_HOST=clickhouse.internal.example.com
CLICKHOUSE_PORT=8443                                # TLS 권장
CLICKHOUSE_SECURE=true
CLICKHOUSE_DB=langfuse
CLICKHOUSE_READONLY_USER=labs_readonly
CLICKHOUSE_READONLY_PASSWORD=
# 폴백: 사내 보안정책상 ClickHouse 직접 접근 불가 시 USE_LANGFUSE_PUBLIC_API_FALLBACK=true (작업 1-4)

# Prometheus (사내 공용 — Backend가 /metrics 노출, scrape는 사내가 수행)
LABS_METRICS_ENABLED=true
LABS_METRICS_PATH=/metrics
PROMETHEUS_QUERY_URL=https://prometheus.internal.example.com  # 헬스체크용

# OpenTelemetry (사내 공용 Collector)
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector.internal.example.com:4318
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer%20<token>
OTEL_SERVICE_NAME=ax-llm-eval-workflow-backend
OTEL_RESOURCE_ATTRIBUTES=service.namespace=labs,deployment.environment=production
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1

# Loki (사내 공용 — stdout JSON 로그를 사내 수집기가 picking)
LABS_LOG_FORMAT=json
LABS_LOG_LEVEL=INFO
LABS_LOG_LOKI_LABELS=service=ax-llm-eval-workflow-backend,env=production

# === 본 프로젝트 자체 운영 ===

# Redis (1순위: 사내 공용 Redis 임차 / 차선: 자체 컨테이너)
REDIS_URL=redis://:${REDIS_PASSWORD}@redis.internal.example.com:6379/${LABS_REDIS_DB:-1}
REDIS_PASSWORD=
LABS_REDIS_DB=1                                     # 사내 임차 시 합의된 DB 번호. 자체 컨테이너 사용 시 0

# JWT (사내 Auth 서비스)
AUTH_JWKS_URL=https://auth.internal.example.com/.well-known/jwks.json
AUTH_JWT_AUDIENCE=labs
AUTH_JWT_ISSUER=https://auth.internal.example.com

# === 보안 경계 ===
# LLM Provider Keys: 사내 LiteLLM이 단독 보관. 본 프로젝트 .env에 포함 금지.
# (예: AZURE_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY 절대 추가 금지)
```

**중요**: LLM Provider 키는 본 프로젝트의 `.env`에 **절대 포함하지 않는다**. LiteLLM Proxy가 단독 보관하며, 본 프로젝트는 Virtual Key로 LiteLLM을 호출한다 (CLAUDE.md "보안 규칙" + ADR-011).

#### 1-4. ClickHouse 읽기 전용 계정 발급 요청 (사내 인프라팀)
본 프로젝트가 분석 쿼리(Phase 6)를 위해 사내 Langfuse 내부 ClickHouse를 직접 조회하므로, 사내 인프라팀에 다음을 요청한다. **본 프로젝트는 자체 ClickHouse 컨테이너 또는 readonly 생성 스크립트를 운영하지 않는다.**

요청 내용:
- 사용자명: `labs_readonly` (또는 사내 명명 규칙)
- 권한: `GRANT SELECT ON langfuse.* TO labs_readonly` 한정 (INSERT/UPDATE/DELETE 금지)
- 접속 제한: Backend 네트워크에서만 접근 가능 (host_regex 또는 IP allowlist)
- TLS 강제 (`CLICKHOUSE_SECURE=true`)

**대안 — 폴백 전략**: 사내 보안정책상 ClickHouse 직접 접근이 불가하면, 분석 API는 **Langfuse 공개 API 폴백**으로 구현한다 (LANGFUSE.md §3 참조 — 일부 집계는 `/api/public/metrics` + `/api/public/observations` 사용). 본 폴백을 채택할 경우:
- `USE_LANGFUSE_PUBLIC_API_FALLBACK=true`로 분기
- Phase 6에서 `clickhouse_client.py`는 Langfuse SDK 호출로 대체
- 성능/유연성 저하 trade-off를 ADR(예: `ADR-012-clickhouse-fallback.md`)로 기록

#### 1-5. ax-eval-sandbox Docker 이미지 빌드
Custom Code Evaluator 실행을 위한 샌드박스 이미지. **본 프로젝트 자체 책임.**

- Python 3.12 slim 기반
- 허용 패키지만 설치: json, re, math, collections, difflib, statistics, unicodedata (표준 라이브러리)
- 네트워크 없음, 볼륨 없음, non-root 사용자
- 메모리 128MB 제한, 실행 시간 5초 제한
- `runner.py` (stdin으로 입력 수신, stdout으로 결과 반환) 포함
- `docker/eval-sandbox/` 디렉토리에 배치
- 사내 컨테이너 레지스트리에 push (운영 배포 시 pull)

#### 1-6. (삭제) ~~Langfuse blob storage (MinIO)~~
사내 Langfuse가 자체 blob storage(events/media/batch-export)를 운영하므로 **본 프로젝트는 별도 MinIO를 기동하지 않는다.** `LANGFUSE_S3_*` 환경변수는 사내 Langfuse가 내부적으로 처리하며, 본 프로젝트에는 영향 없음.

#### 1-7. Observability 연동 (사내 Prometheus / OTel / Loki)

##### 1-7-A. Prometheus (메트릭) — 자체 노출 + 사내 scrape 등록 PR
**본 프로젝트 책임**:
- Backend FastAPI에 `/metrics` 엔드포인트 노출 (`prometheus-fastapi-instrumentator` 또는 `prometheus_client`)
- 모든 `ax_*` 메트릭은 OBSERVABILITY.md §2.2 명명 규칙 준수
- 내부 통신만 노출, 외부 인증 불필요 (사내 네트워크 한정)

**사내 인프라팀 변경 요청 (PR/티켓)**:
- `prometheus.yml`에 scrape job 등록:
  ```yaml
  - job_name: ax-llm-eval-workflow-backend
    static_configs:
      - targets: ['backend.labs.internal.example.com:8000']
    metrics_path: /metrics
    scrape_interval: 15s
  ```
- Recording rules (`ax:*`): OBSERVABILITY.md §2.4를 사내 룰 디렉터리에 PR
- Alert rules: OBSERVABILITY.md §2.5 임계치를 사내 Alertmanager 라우팅 키와 함께 PR
- Alertmanager 라우팅: 본 프로젝트 알림 채널(Telegram/Slack) 분기

##### 1-7-B. OpenTelemetry (분산 트레이스) — OTel SDK 통합
**본 프로젝트 책임**:
- Backend에 OTel SDK 통합 (`opentelemetry-distro`, `opentelemetry-instrumentation-fastapi`, `-httpx`, `-redis`)
- OTLP/HTTP exporter 설정 (`OTEL_EXPORTER_OTLP_ENDPOINT`)
- 리소스 속성: `service.namespace=labs`, `service.name=ax-llm-eval-workflow-backend`
- Trace context 전파: 들어오는 요청의 `traceparent` 헤더 보존, LiteLLM/Langfuse 호출에 propagate
- Sampling: `parentbased_traceidratio` 0.1 기본 (운영), 0.0 dev (off), 1.0 demo

**사내 인프라팀 책임**:
- OTel Collector 가용성, 인증 토큰 발급, 백엔드(Tempo/Jaeger) 라우팅

##### 1-7-C. Loki (로그) — stdout JSON + 라벨 규약
**본 프로젝트 책임**:
- 모든 로그를 stdout으로 출력, **JSON 구조화** (필수 필드: `timestamp`, `level`, `event`, `request_id`, `trace_id`, `experiment_id`)
- 사내 수집기(Promtail/Vector)와 협의된 라벨 부착 (`service`, `env`)
- **PII 미포함** 정책 강제 — 프롬프트/모델 출력 원본 로그 금지 (CLAUDE.md "보안 규칙"). CI 단계에서 PII 스캐너 통과 필수.

**사내 인프라팀 책임**:
- 컨테이너 stdout → Loki 수집 파이프라인, 보존 정책

##### 1-7-D. (삭제) ~~자체 Prometheus 컨테이너 + 룰 파일~~
사내 Prometheus를 사용하므로 본 프로젝트의 `docker/prometheus/` 디렉터리는 **사내 Prometheus 룰 저장소로 이전**한다. 본 프로젝트 리포지토리에는 룰 변경 PR의 사본 또는 링크를 `docs/observability/` 하위에 보관하여 추적성을 확보한다.

#### 1-8. Score Config 등록 부팅 훅 (LANGFUSE.md §Score Config)
- backend 컨테이너 entrypoint에서 `services/score_registry.py`가 evaluator 카탈로그를 순회하며 **사내 Langfuse**의 `POST /api/public/score-configs`로 idempotent 등록 (이미 존재하면 skip, data_type/range 불일치 시 startup 실패)
- 데모 환경 seed 컨테이너는 backend healthy 이후 기동하며, 데이터셋·프롬프트·실험 시드 외에 **데모 전용 score config**(예: `demo_quality`, `demo_toxicity`)도 동일 경로로 등록
- compose의 `backend.depends_on`에는 사내 서비스가 없으므로, **Backend 부팅 시 사내 Langfuse 헬스체크 + 재시도(최대 30초)** 후 등록 진행. 30초 내 도달 실패 시 기동 실패 처리.

#### 1-9. ADR-011 시크릿 관리 정책 신설 (범위 축소)
- `docs/adr/ADR-011-secrets-management.md` 작성
- **본 프로젝트가 관리하는 secret 카탈로그** (사내 LiteLLM이 보관하는 LLM Provider 키는 제외):
  - `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY` — 사내 Langfuse 발급
  - `LITELLM_VIRTUAL_KEY` — 사내 LiteLLM 발급
  - `CLICKHOUSE_READONLY_PASSWORD` — 사내 인프라팀 발급
  - `REDIS_PASSWORD` — 사내 Redis 임차 시 또는 자체 운영 시
  - `OTEL_EXPORTER_OTLP_HEADERS` — 사내 OTel Collector 토큰
  - JWT 서명 키는 **사내 Auth 서비스 책임** — 본 프로젝트는 JWKS 공개키로 검증만
- 주입 경로: 개발=`.env`(git-ignored), 데모=환경변수 직접 주입, 운영=사내 secret store(Vault/KMS) → Docker secrets 마운트
- 로테이션 책임자: 각 secret의 발급 주체(사내 인프라팀)와 본 프로젝트 platform owner의 RACI 명시
- BUILD_ORDER 작업 1-1/1-3과 직접 의존 (파일 구조·환경변수 명명 규칙 확정)

### 산출물
- 자체 compose(backend/frontend/(옵션) redis) 정상 기동
- Backend `/api/v1/health` 응답에서 사내 Langfuse / LiteLLM / ClickHouse(또는 폴백) / Redis / OTel / Loki / Prometheus 도달성 모두 OK
- 사내 Langfuse에 본 프로젝트 score config 자동 등록(idempotent) 결과 로그
- 사내 Prometheus의 `/api/v1/targets`에서 본 프로젝트 scrape job이 UP 상태
- 사내 OTel Collector에 첫 trace 도착 (Tempo/Jaeger UI 확인)
- 사내 Loki에 첫 JSON 로그 도착 (라벨 필터로 확인)
- sandbox 이미지 빌드 + 사내 레지스트리 push 성공

### 검증 방법
```bash
# 1. 자체 compose 기동 (사내 서비스는 .env로 참조)
docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml up -d

# 2. Backend 헬스체크 (사내 의존 서비스 모두 OK 응답 필요)
curl -s http://localhost:8000/api/v1/health | jq .
# 응답 services: {langfuse: ok, litellm: ok, clickhouse: ok, redis: ok, prometheus: ok, otel: ok, loki: ok}

# 3. 사내 Langfuse 접속 확인
curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" "$LANGFUSE_HOST/api/public/health"

# 4. 사내 LiteLLM 접속 확인
curl -s -H "Authorization: Bearer $LITELLM_VIRTUAL_KEY" "$LITELLM_BASE_URL/health"

# 5. ClickHouse readonly 접속 확인 (또는 폴백 채택 시 본 단계 skip)
clickhouse-client --host "$CLICKHOUSE_HOST" --port "$CLICKHOUSE_PORT" \
  --secure --user "$CLICKHOUSE_READONLY_USER" --password "$CLICKHOUSE_READONLY_PASSWORD" \
  --query "SELECT 1"

# 6. Redis 연결 확인 (사내 임차)
redis-cli -u "$REDIS_URL" PING  # PONG

# 7. 사내 Prometheus에서 본 서비스 scrape UP 확인
curl -s "$PROMETHEUS_QUERY_URL/api/v1/targets" \
  | jq '.data.activeTargets[] | select(.labels.job=="ax-llm-eval-workflow-backend") | .health'
# "up"

# 8. Score config 등록 결과 (Backend 로그)
docker compose logs backend | grep "score_config_registered"

# 9. Sandbox 이미지 빌드 + push
docker build -t ax-eval-sandbox:1.0.0 docker/eval-sandbox/
docker tag ax-eval-sandbox:1.0.0 registry.internal.example.com/labs/ax-eval-sandbox:1.0.0
docker push registry.internal.example.com/labs/ax-eval-sandbox:1.0.0
```

### 테스트 명세 참조
- TEST_SPEC.md Phase 1 (인프라 테스트) — 사내 의존 서비스 도달성 테스트로 범위 조정 필요. 자체 운영 컴포넌트(redis, sandbox)와 사내 의존성 모킹(Mock Langfuse/LiteLLM/ClickHouse/Loki/OTel — Phase 0의 fixture 재사용)으로 분리.

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
- **사내 공용 의존 서비스** 연결 상태 확인: Langfuse(`/api/public/health`), LiteLLM(`/health` + Virtual Key 인증), ClickHouse(readonly `SELECT 1` — 폴백 채택 시 Langfuse public API ping으로 대체), Prometheus(`/-/ready` — `PROMETHEUS_QUERY_URL` 기준), OTel Collector(OTLP `/v1/traces` HEAD 또는 헬스 path), Loki(`/ready`)
- **자체 운영 의존성**: Redis(`PING`)
- 각 서비스별 OK/WARN/FAIL 상태 + 마지막 체크 시각 + 응답 시간(ms) + 엔드포인트 host(secret 마스킹) 반환
- MinIO/Postgres는 **사내 Langfuse 내부 의존성**이므로 본 프로젝트가 직접 헬스체크하지 않는다 (Langfuse 헬스체크가 통과하면 정상 가정).

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
# 응답: {"status": "ok", "version": "1.0.0", "services": {"langfuse": "ok", ...}}

# 3. JWT 인증 검증 — 인증 없이 임의의 보호된 경로 접근
# (Prompt API는 Phase 3에서 구현하므로, 여기서는 미들웨어 레벨의 401 확인)
curl http://localhost:8000/api/v1/projects
# 응답: 401 Unauthorized (JWT 미포함)

# 4. 유효한 JWT로 보호된 엔드포인트 접근
curl -H "Authorization: Bearer <valid_jwt>" http://localhost:8000/api/v1/projects
# 응답: 200 OK (빈 목록이라도 인증 통과 확인)
```

### 테스트 명세 참조
- TEST_SPEC.md Phase 2 (Backend 기초 약 61개)

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
| `POST /api/v1/datasets/upload` | 파일 파싱 (CSV/JSON/JSONL) + 매핑 + Langfuse 업로드 (async, upload_id 반환) |
| `GET /api/v1/datasets/upload/{upload_id}/stream` | SSE 진행률 스트리밍 |
| `POST /api/v1/datasets/upload/preview` | 업로드 미리보기 (첫 5건) |
| `POST /api/v1/datasets/from-items` | 기존 실험의 실패 아이템에서 파생 데이터셋 생성 (user) |
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
- TEST_SPEC.md Phase 3 (Core API 약 126개 + 신규 기능 테스트 필요: notifications 10+, submissions 15+, datasets/from-items+upload stream 12+, latency/cost distribution 6+)

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

#### 4-5. 실험 삭제
- `DELETE /api/v1/experiments/{experiment_id}` (admin 전용, running/paused 상태는 삭제 불가)
- 데이터셋 삭제(`DELETE /api/v1/datasets/{name}`)와 글로벌 검색(`GET /api/v1/search`)은 Phase 3에서 이미 구현됨

#### 4-6. 알림 생성 로직
- `batch_runner.py` 완료/실패 훅에서 Notification 생성
- 대상 사용자: `ax:experiment:{id}` → `started_by`
- Redis `ax:notification:{user_id}:*` 저장 (TTL 30일)
- IMPLEMENTATION.md §1.5 "알림 생성 주체" 참조

#### 4-7. 실험 설정 스냅샷
- `POST /experiments` 시 원본 요청을 `ax:experiment:{id}` → `config` 필드에 JSON으로 저장 (이미 IMPLEMENTATION §1.3에 정의됨)
- `GET /experiments/{id}` 응답의 `config_snapshot`으로 노출
- Frontend "같은 설정으로 재실행" 기능 지원

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
- TEST_SPEC_PART2.md Phase 4 (실험 엔진 약 53개)

---

## Phase 5: 평가 시스템

### 선행 조건
- Phase 4 완료 (실험 실행 엔진 작동, LLM 호출 및 Langfuse trace 기록 가능)
- ax-eval-sandbox Docker 이미지 빌드 완료 (Phase 1 작업 1-5)

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

#### 5-5. 가중 평균 스코어 (weighted_score)
- 각 evaluator에 `weight` 필드 적용 (합계 1.0 검증)
- Pipeline Orchestrator에서 null 제외 재정규화 후 가중 평균 계산
- `langfuse.score(trace_id, "weighted_score", value)`로 별도 기록
- EVALUATION.md §5.4 참조

#### 5-6. Custom Evaluator 거버넌스 API
- `POST /api/v1/evaluators/submissions` — 코드 제출 (user)
- `GET /api/v1/evaluators/submissions` — 제출 목록 (admin=전체, user=본인만)
- `POST /api/v1/evaluators/submissions/{id}/approve|reject` — 승인/반려 (admin)
- `GET /api/v1/evaluators/approved` — 승인된 evaluator 목록 (user 이상, 위저드 Step 3 UI 데이터 소스)
- Redis `ax:evaluator_submission:*` 저장소 (IMPLEMENTATION §1.5)
- 승인/반려 시 제출자에게 Notification 생성

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
- TEST_SPEC_PART2.md Phase 5 (평가 시스템 약 53개 + weighted_score 5+, evaluator 거버넌스 10+ 필요)

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
| Outlier 감지 | score_range가 큰 아이템 (threshold 0.3) |
| 비용 효율 분석 | Run별 score_per_dollar |
| 스코어 분포 | 히스토그램 bin별 count |

#### 6-3. Compare API (`app/api/v1/analysis.py`)

| 엔드포인트 | 기능 |
|------------|------|
| `POST /api/v1/analysis/compare` | 요약 비교 (latency, cost, tokens, scores) |
| `POST /api/v1/analysis/compare/items` | 아이템별 상세 비교 (score_range 정렬, score_min/max 필터) |
| `GET /api/v1/analysis/scores/distribution` | 스코어 분포 히스토그램 + 통계 (run_names 복수 지원) |
| `GET /api/v1/analysis/latency/distribution` | 지연시간 P50/P90/P99 + 히스토그램 (LANGFUSE.md §3.2 쿼리) |
| `GET /api/v1/analysis/cost/distribution` | 비용 분포 (LANGFUSE.md §3.2 쿼리) |

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
    "sort_by": "score_range",
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
- Tailwind CSS v4 설정 — **데스크톱 전용**: 최소 지원 1280px, 권장 1440px+. `<1280px` 미디어 쿼리/모바일 레이아웃 분기 금지, 가로 스크롤 허용 (UI_UX_DESIGN.md "뷰포트 정책" 참조)
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
- **Top Bar** (48px): 로고 + 프로젝트 선택 드롭다운 (좌측), 알림 종 아이콘 + 비교 장바구니 아이콘 + 사용자 메뉴 (우측)
- **알림 수신함 드롭다운** (Top Bar 종 아이콘): `GET /notifications` 폴링 (30초 간격), unread 배지, 클릭 시 `target_url`로 이동
- **비교 장바구니 드롭다운** (Top Bar 배지): localStorage 기반 전역 상태, 최대 5개, 클릭 시 `/compare?runs=...`로 이동
- **Side Nav** (56px): 아이콘만 표시, hover 시 라벨 툴팁, 실험 아이콘에 실행 중 건수 배지
  - 메뉴 항목: 실험, 결과, 데이터셋, 프롬프트, 평가, 설정
  - 현재 페이지는 accent 색상 배경 (indigo-400)
- **글로벌 실행 상태 바** (페이지 하단 고정): SSE로 진행 중 실험 표시, 페이지 이동 후에도 유지
- **브라우저 알림 권한 요청**: 첫 실험 시작 직후 1회 (Notification API), 거부 시 알림 수신함으로 유도
- **Main Content**: Side Nav 제외 전체 너비

#### 7-3. 페이지 구현 (구현 순서대로)

아래 순서는 의존성과 복잡도를 고려한 것이다. 단순한 페이지부터 시작하여 점진적으로 복잡한 페이지를 구축한다.

---

**페이지 1: 설정 (Settings)**

가장 단순한 페이지. 사내 공용 인프라 + 자체 운영 컴포넌트의 연결 상태를 확인하여 전체 셋업이 올바른지 검증한다.

- 프로젝트 전환 (드롭다운 + `POST /projects/switch`)
- LiteLLM에 등록된 모델 목록 표시 (`GET /models`) — 사내 LiteLLM에서 본 프로젝트 Virtual Key로 조회 가능한 모델만 노출
- 의존 서비스 연결 상태 패널:
  - **사내 공용**: Langfuse, LiteLLM, ClickHouse(폴백 채택 시 "Langfuse Public API"), Prometheus, OTel Collector, Loki
  - **자체 운영**: Redis (사내 임차 또는 자체 컨테이너 — 라벨에 출처 명시)
- 상태 표시: emerald dot (정상), amber dot (경고/저하), rose dot (실패). 각 항목 옆에 마지막 헬스체크 시각·응답 시간·엔드포인트 호스트(secret 마스킹) 표시

검증: 페이지 로드 시 모든 서비스 상태가 표시되고, 프로젝트 전환이 동작한다. 사내 서비스 일시 장애 시 amber/rose로 즉시 반영, 복구 시 polling(예: 30초)으로 자동 갱신.

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
- 변수 프리셋 저장/로드 (localStorage 기반, 프리셋 이름/설명/변수 값 세트)

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
  - 테스트 케이스 입력 + 검증 실행 (`POST /evaluators/validate`, user 권한)
  - 실행 결과 표시
- **Custom Evaluator 거버넌스 UI** (섹션 26.9):
  - user 역할: 제출 폼 + 본인 제출 목록 (`POST/GET /evaluators/submissions`)
  - admin 역할: 검토 큐 (pending 목록), 코드 diff, 승인/반려 버튼
  - 승인된 evaluator는 위저드 Step 3 (`GET /evaluators/approved` 체크박스 목록)에 표시

검증: Built-in 목록 확인 → LLM Judge 프롬프트 설정 → Custom Code 작성 → 검증 실행 → 결과 확인.

---

**페이지 7: 결과 비교**

차트와 테이블로 실험 결과를 시각화한다.

상단 - 비교 Run 선택:
- Run 선택 (복수, 최소 2개, 최대 5개 — 섹션 28.2 시리즈 색상 수)
- 선택된 Run들의 프롬프트 버전, 모델 정보 표시
- Compare Basket에서 자동 로드 (URL `?runs=...` 파라미터)

KPI 카드 (요약, 가중 평균 스코어 기준):
- Best Score (어떤 Run), Fastest (어떤 Run), Cheapest (어떤 Run)
- 상대 비교 수치 표시 (% 차이)
- **"🏆 이 버전 승격하기" CTA** (admin 전용): Best Score 카드에서 해당 Run의 프롬프트 버전을 production으로 승격하는 다이얼로그 호출 (섹션 18.3)
- "📁 새 데이터셋으로 저장": 선택된 실패 아이템으로 파생 데이터셋 생성 (`POST /datasets/from-items`)
- "📥 CSV 내보내기": 비교 결과 테이블 다운로드

상세 비교 (탭):
- 스코어 탭: 분포 히스토그램 (Recharts), Run별 통계 (avg, stddev)
- 지연시간 탭: P50/P90/P99 비교
- 비용 탭: 총 비용 bar chart, score_per_dollar 효율
- 토큰 탭: input/output 토큰 비교

아이템별 비교 테이블:
- score_range 기준 정렬 (outlier 우선)
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

## Phase 8: 운영 인계 (Operational Handoff)

### 선행 조건
- Phase 0~7 완료, 모든 마일스톤 통과

### 작업 목록
- **8-1. 런북 작성**: `docs/runbooks/` 디렉터리 — 시나리오별 파일 분리. **사내 의존 시나리오**(`langfuse-down.md`, `litellm-down.md`, `clickhouse-readonly-revoked.md`, `otel-collector-unreachable.md`, `loki-pipeline-stalled.md`, `prometheus-scrape-down.md`)는 사내 인프라팀 에스컬레이션 절차 명시. **자체 운영 시나리오**(`labs-redis-down.md`, `sandbox-oom.md`, `backend-deadlock.md`)는 본 프로젝트 platform owner 1차 대응. `docs/runbooks/README.md`(인덱스) + OBSERVABILITY.md 알림 ID ↔ 런북 파일 매핑 표 포함 (ADR-011 참조)
- **8-2. 시크릿 로테이션 절차**: 본 프로젝트가 관리하는 secret만 대상 (작업 1-9 카탈로그 — Langfuse Key/LiteLLM Virtual Key/ClickHouse readonly/Redis/OTel 토큰). LLM Provider 키와 JWT 서명 키는 **사내 인프라팀 책임**으로 명시하고 사내 로테이션 일정과 동기화 절차만 문서화. 회전 주기/명령/검증 단계는 ADR-011에 정의된 secret store에서 주입
- **8-3. 백업/복구 드릴**: 본 프로젝트는 **자체 Redis AOF 스냅샷** + **sandbox 이미지 버전 보존**만 담당. Langfuse Postgres/ClickHouse/MinIO 백업은 **사내 인프라팀 책임**이며, 본 프로젝트는 분기 1회 사내 백업 리허설 결과 보고서 검토 + 본 프로젝트 의존 핵심 시나리오(예: `dataset_run` 복원, `score_configs` 재등록) 통과 여부 확인. `scripts/backup-drill.sh`는 자체 Redis/sandbox 한정 자동 검증 + 사내 백업 메타 fetch + 분기 1회 복구 리허설 체크리스트
- **8-4. 온콜 설정**: Telegram/Slack 알림 채널은 본 프로젝트 platform owner 운영. **Prometheus Alertmanager 라우팅**은 사내 인프라팀에 본 프로젝트 알림 키(예: `team=labs`)와 채널 webhook을 PR로 등록. 1차(platform owner) → 2차(Admin RBAC 보유자) → 3차(사내 인프라팀 escalation) 로테이션 명시
- **8-5. 인수 인계 회의**: 본 프로젝트 platform owner ↔ 사내 인프라팀 합동 회의. 본 프로젝트 아키텍처 워크스루, 대시보드/메트릭(`ax_*` 카탈로그) 사용법, 사내↔자체 책임 분담표(「사내 공용 인프라 의존」 절), 알려진 한계(MVP 범위) 공유

### 산출물
- `docs/runbooks/{README,langfuse-down,litellm-down,clickhouse-readonly-revoked,otel-collector-unreachable,loki-pipeline-stalled,prometheus-scrape-down,labs-redis-down,sandbox-oom,backend-deadlock}.md`
- `docs/SECRETS_ROTATION.md` (본 프로젝트 카탈로그 한정), `docs/BACKUP_RESTORE.md` (자체 Redis/sandbox 한정 + 사내 백업 의존성 명시), `scripts/backup-drill.sh`
- 사내 Alertmanager 라우팅 설정 PR 머지 확인
- 인계 회의록 + 운영팀(사내 인프라팀 + 본 프로젝트 platform owner) sign-off

### 검증 방법
- 자체 Redis 백업 → 빈 환경에서 복구 → 핵심 시나리오 1건 통과
- 사내 Langfuse/LiteLLM/Prometheus 인위적 장애 주입(사내 인프라팀과 협의된 카오스 테스트 윈도우) 후 본 프로젝트 알림 채널 수신 + 런북 절차로 에스컬레이션 정상 동작 확인
- 사내 백업 리허설 보고서 검토 후 본 프로젝트 의존 시나리오(score config 재등록, dataset run 복원) 통과 확인

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
                                            └─▶ Phase 8 (운영 인계)

Phase 1 작업 1-5 (sandbox 이미지) ──▶ Phase 5 작업 5-3 (Custom Code Evaluator)
```

- Phase 0~6은 순차적 의존성
- Phase 7 (Frontend)은 Phase 6 완료 후 시작하는 것이 이상적이나, API 스펙 확정 후 병렬 개발 가능
- Frontend 페이지 간에도 순서가 있음: 설정 → 데이터셋 → 프롬프트 → 단일 테스트 → 배치 → 평가 → 비교

---

## FEATURES §11 우선순위 ↔ Phase 매핑

FEATURES.md §11의 P0/P1/P2 우선순위를 본 문서의 Phase 작업 단위로 역매핑한다. FEATURES.md 표의 "BUILD_ORDER.md Phase N" 표기는 기획 단계 개략치이며, 실제 구현 위치는 아래 표를 따른다.

| 우선순위 | 기능 | 실제 구현 Phase / 작업 |
|---------|------|----------------------|
| **P0 (MVP)** | 단일 테스트 (FEATURES §1) | Phase 4 / 4-1, 4-2 (Context Engine + Single Test Runner) |
| P0 | 배치 실험 (FEATURES §2) | Phase 4 / 4-1, 4-3, 4-4, 4-7 |
| P0 | 데이터셋 업로드 (FEATURES §4) | Phase 3 / 3-2 |
| P0 | 내장 평가 함수 (FEATURES §5.2) | Phase 5 / 5-1, 5-4 |
| P0 | LLM-as-Judge (FEATURES §5.2) | Phase 5 / 5-2, 5-4 |
| P0 | 기본 실험 비교 (FEATURES §3.1~3.2) | Phase 6 / 6-1, 6-2, 6-3 (compare, compare/items) |
| **P1** | 스코어 분포 분석 (FEATURES §3.3) | Phase 6 / 6-3 (scores/distribution) |
| P1 | 비용/성능 분석 (FEATURES §3.4) | Phase 6 / 6-3 (latency/cost distribution) |
| P1 | 변수 프리셋 (FEATURES §6.2) | Phase 7 / 페이지 4 (단일 테스트 localStorage) |
| P1 | 실패 아이템 파생 데이터셋 (FEATURES §9.3) | Phase 3 / 3-2 (`POST /datasets/from-items`) + Phase 7 페이지 7 |
| P1 | 알림 수신함 (FEATURES §9.2) | Phase 4 / 4-6 + Phase 7 / 7-2 (Top Bar 드롭다운) |
| **P2** | Custom Evaluator 거버넌스 (FEATURES §5.2, §9.1) | Phase 5 / 5-3, 5-6 + Phase 7 / 페이지 6 |
| P2 | 실험 템플릿 (FEATURES §9.5) | Phase 4 / 4-7 (config_snapshot) + Phase 7 페이지 5 |
| P2 | 비교 장바구니 (FEATURES §9.4) | Phase 7 / 7-2 (Top Bar) + 페이지 7 |
| P2 | 평가 가중치 (FEATURES §10) | Phase 5 / 5-5 (weighted_score) |

**MVP 완성 기준**: P0 항목이 모두 동작하려면 Phase 0~6이 완료되고 Phase 7의 페이지 1~5, 7이 구현되어야 한다. Phase 6 없이 MVP 불가(기본 실험 비교가 P0).

**참고**: FEATURES.md §11 표에 표기된 Phase 범위(P0=Phase 1~3 등)는 기획 단계 개략치로, 본 표와 상이할 경우 본 표를 신뢰한다. FEATURES.md는 차후 동기화 대상.

---

## 병렬 작업 기회

각 Phase 내부 및 Phase 간 병렬화 가능한 작업을 명시한다. 의존성이 없는 작업은 동시 진행하여 전체 기간을 단축한다.

### Phase 내부 병렬화
- **Phase 0**: 0-1(Backend 구조), 0-3(Frontend 설정), 0-4(CI)는 서로 독립 → 병렬. 0-2(Mock fixture)는 0-1 이후.
- **Phase 1**: 1-1/1-2/1-3(compose, LiteLLM config, .env)은 병렬 작성 가능. 1-4(ClickHouse readonly)는 1-1 기동 후. 1-5(sandbox 이미지)는 완전 독립 → 다른 작업과 병렬.
- **Phase 2**: 2-2(config), 2-3(JWT), 2-4(Langfuse client), 2-5(Redis client)는 2-1(구조) 이후 병렬 구현 가능. 2-6(헬스체크)은 2-4/2-5 의존.
- **Phase 3**: 3-1~3-5 API는 모듈 간 결합도가 낮아 병렬 구현 가능 (단, 공통 미들웨어/deps는 사전 확정).
- **Phase 4**: 4-1(Context Engine)은 4-2/4-3의 선행. 4-2(Single)와 4-3(Batch)은 Context Engine 완료 후 병렬. 4-4(제어), 4-6(알림), 4-7(스냅샷)은 4-3 이후 병렬.
- **Phase 5**: 5-1(Built-in), 5-2(LLM Judge), 5-3(Custom Code)은 독립적 → 병렬. 5-4(Pipeline)는 5-1~5-3 완료 후. 5-5(weighted), 5-6(거버넌스)은 5-4 이후 병렬.
- **Phase 6**: 6-1(ClickHouse client) → 6-2(쿼리 템플릿) → 6-3(API)는 순차.
- **Phase 7**: 페이지 1/2/3은 서로 독립 → 병렬 개발 가능. 4/5는 3 이후. 6/7은 5 이후.

### Phase 간 병렬화
- **Phase 1 ↔ Phase 0**: Phase 0의 Mock fixture 작성은 Phase 1 인프라 기동과 병렬 진행 가능 (Mock은 실제 서비스 불필요).
- **Phase 1-5 (sandbox 이미지)**: Phase 2~4 진행 중 별도 트랙에서 빌드 가능. Phase 5 시작 전까지만 완료되면 됨.
- **Phase 7 (Frontend)**: Phase 3에서 API 스펙(OpenAPI) 확정 시점부터 MSW mock 기반으로 선행 개발 가능. 단, Backend API 완성 전까지 E2E 검증 불가.
- **Phase 6 쿼리 설계**: Phase 5 진행 중 ClickHouse 스키마 분석과 쿼리 템플릿 초안 작성은 병렬 가능.

---

## 롤백 전략

각 Phase별 실패/중단 시 복구 절차. 모든 변경은 Git 커밋 단위로 관리하고, 인프라 변경은 `docker compose down` 후 재시작으로 깨끗하게 복원 가능해야 한다.

### 공통 원칙
- **Git 기반 코드 롤백**: Phase 단위로 태그(`phase-0-complete`, `phase-1-complete`, ...)를 찍고, 문제 발생 시 `git revert` 또는 `git reset --hard <tag>`로 복귀.
- **데이터 보존**: 자체 운영 Redis 볼륨은 `docker compose down` 시 유지(`-v` 플래그 금지). 사내 공용 서비스(Langfuse/ClickHouse/MinIO/Postgres) 데이터는 **사내 인프라팀 백업 정책**을 따름 — 본 프로젝트는 직접 관리하지 않음.
- **사내 변경 롤백**: 사내 Prometheus 룰/Alertmanager 라우팅/LiteLLM `config.yaml` 변경은 사내 PR 단위 revert로 롤백 (본 프로젝트가 직접 수행 불가, 사내 인프라팀에 요청).
- **체크포인트 원칙**: Phase 완료 시 1) 모든 테스트 통과, 2) 검증 방법 수동 확인, 3) Git 태그 생성, 4) 다음 Phase 시작.

### Phase별 롤백
- **Phase 0**: 테스트 인프라 실패 시 `backend/tests/`, `frontend/tests/`, `.github/workflows/test.yml` 삭제 후 재구축. 영향 범위는 테스트 한정이므로 데이터 리스크 없음.
- **Phase 1**:
  - **자체 컴포넌트**: `docker compose -f docker/docker-compose.yml down` (볼륨 유지) → compose/config 수정 → `up -d` 재기동. 네트워크/포트 충돌은 `docker network prune`으로 해결. sandbox 이미지 롤백은 `docker image rm ax-eval-sandbox:1.0.0` 후 재빌드.
  - **사내 의존성 변경**: 사내 Prometheus scrape job/recording rules/alert rules PR을 사내 인프라팀에 revert 요청. 본 프로젝트 PR 사본(`docs/observability/`)에서도 이전 버전으로 되돌려 추적성 일치.
  - **사내 LiteLLM 모델 등록**: 등록 PR revert 요청 + 본 프로젝트 모델 카탈로그(`frontend/src/lib/mock/data.ts`)에서 해당 모델 제거. 진행 중 실험은 영향 받은 모델 사용 시 `failed` 처리 후 재시도.
  - **ClickHouse readonly 계정 회수**: 사내가 권한 회수 시 `USE_LANGFUSE_PUBLIC_API_FALLBACK=true`로 즉시 폴백 — 작업 1-4 명시.
- **Phase 2**: Backend 구조 문제 시 `backend/app/` 전체를 Phase 0 상태(스텁)로 복귀. JWKS/환경변수 오류는 `.env` 수정 후 재기동. Langfuse/Redis 클라이언트 회귀는 단위 테스트로 즉시 감지.
- **Phase 3**: API 모듈 단위로 개별 revert 가능. Langfuse에 잘못 생성된 리소스(프롬프트/데이터셋)는 Langfuse UI에서 수동 삭제 또는 `DELETE` API 사용. 파일 업로드 실패 시 멱등성 키로 중복 업로드 방지.
- **Phase 4**: 실험 엔진 실패 시 진행 중 실험은 `POST /experiments/{id}/cancel`로 중단. Redis의 실험 상태는 `ax:experiment:*` 키 삭제로 초기화 (단, 진행 중 실험이 없을 때만). Langfuse trace는 남지만 유해하지 않으므로 보존.
- **Phase 5**: 평가 시스템 오류 시 해당 evaluator만 비활성화 (실험은 evaluator 없이도 실행 가능). Custom Code 샌드박스 이상 시 `docker rm` 후 재생성. Langfuse `score`는 잘못 기록되어도 재계산하여 덮어쓰기 가능.
- **Phase 6**: ClickHouse 쿼리는 읽기 전용이므로 데이터 손상 리스크 없음. 쿼리 오류 시 코드 revert만으로 복구. LIMIT 누락 등 리소스 폭주는 쿼리 타임아웃으로 방어.
- **Phase 7**: Frontend 페이지 단위 revert. 라우트/컴포넌트 단위로 격리되어 있어 영향 최소. SSE 연결 누수는 페이지 새로고침으로 해결.

### 심각도별 대응
- **경미 (코드 버그)**: Git revert + 재배포.
- **중간 (인프라 설정)**: `docker compose down` → 설정 수정 → `up -d`.
- **심각 (데이터 손상)**: 최근 볼륨 스냅샷으로 복원 (운영 환경에서는 일일 백업 필수).

---

## 마이그레이션 / 초기화 순서

신규 환경 셋업 시 또는 재구축 시 따라야 하는 기동 순서. 의존성 방향(아래 → 위)을 준수해야 한다. **사내 공용 서비스(Langfuse/LiteLLM/ClickHouse/Prometheus/OTel/Loki)는 사전 기동/운영 가정**이며, 본 프로젝트는 자체 컴포넌트만 직접 기동한다.

0. **사내 인프라 선결 조건 충족**: 「사내 공용 인프라 의존」 절의 Phase 1 시작 선결 조건 1~10번 모두 합의/발급 완료 (Langfuse Key, LiteLLM Virtual Key, ClickHouse readonly 또는 폴백 결정, Redis 정책, Prometheus/OTel/Loki 합의, JWKS, 네트워크 도달성, ADR-011).
1. **사내 의존 서비스 도달성 확인** (Phase 1): `curl` 또는 `docker compose run --rm net-check`로 Backend 컨테이너 네트워크에서 사내 Langfuse/LiteLLM/ClickHouse/Redis(임차 시)/Prometheus/OTel/Loki 엔드포인트로의 도달성 검증. 미도달 항목은 사내 인프라팀에 티켓 발행.
2. **자체 컴포넌트 기동** (Phase 1): (선택) `redis`(자체 컨테이너 모드일 때만) → `backend`(부팅 시 사내 헬스체크 + score config idempotent 등록) → `frontend`. Docker Compose의 `depends_on` + healthcheck로 자체 컴포넌트 간 순서 보장.
3. **Langfuse 프로젝트 + 키 발급** (Phase 1, 사내 인프라팀): 사내 Langfuse UI에서 본 프로젝트 organization/project 생성 → Public/Secret Key 발급 → 본 프로젝트 secret store(또는 `.env`)에 주입.
4. **LiteLLM 모델 등록 + Virtual Key 발급** (Phase 1, 사내 인프라팀): 사내 LiteLLM `config.yaml`에 본 프로젝트가 사용할 모델 추가 변경 PR 머지 → 본 프로젝트 전용 Virtual Key 발급 → `LITELLM_BASE_URL` + `LITELLM_VIRTUAL_KEY`로 본 프로젝트 주입.
5. **Backend 기동 + 헬스체크 통과** (Phase 2+): `.env` 로드 → `uvicorn app.main:app` → `/api/v1/health`로 사내 의존 서비스 모두 OK 확인. 기동 직후 score config가 사내 Langfuse에 자동 등록(idempotent)된 결과 로그 확인.
6. **데이터 시드** (Phase 3 검증/데모용 필수, 로컬 개발 선택): `scripts/seed_langfuse.py`를 실행하여 사내 Langfuse에 ① 테스트 프롬프트 1건(`labs-demo-classification`, v1), ② 데이터셋 1건(`labs-demo-dataset`, item 5건, JSONL), ③ Custom Code Evaluator 샘플 1건, ④ 모델 별칭 매핑(`gpt-4o-mini` → LiteLLM 라우트)을 멱등 업로드한다. 스크립트는 `LANGFUSE_PUBLIC_KEY/SECRET_KEY` 필수, 재실행 시 동일 ID로 덮어쓰기. 데모 환경(`docker-compose.demo.yml`)에서는 `seed` 컨테이너가 backend `healthy` 이후 자동 1회 실행된다.
7. **사내 Prometheus scrape 등록 확인** (Phase 1): 사내 Prometheus의 `/api/v1/targets`에서 본 프로젝트 job(`ax-llm-eval-workflow-backend`)이 `up` 상태인지 확인. 미등록 시 사내 인프라팀에 PR 재요청.
8. **Frontend 기동** (Phase 7): `npm run dev`. Backend가 먼저 기동되어 있어야 `/api/v1/health` 프록시 확인 가능.

### 7-A. Backend 재기동 시 실험 상태 복구
Backend 재기동(롤링 업데이트, 장애 복구, SIGTERM 후 재가동) 직후 다음 절차를 자동 실행한다 (IMPLEMENTATION.md §6.4 Graceful shutdown과 정합):

1. Redis `SCAN MATCH ax:experiment:*` (또는 §1.5의 프로젝트별 인덱스 ZRANGE)로 `status∈{running,paused}` 실험 ID 수집.
2. 각 실험에 대해:
   - `status=paused` AND `paused_reason=shutdown` AND `last_checkpoint_at` ≤ `LABS_EXPERIMENT_STATE_TTL` 이내 → 자동 재개(`asyncio.create_task(run_experiment(..., resume=True))`), SSE `event: resumed` 송신.
   - `status=running` (이전 프로세스 비정상 종료) → 진행률 메타 검증 후 재개. 검증 실패 시 `status=failed`, `failure_reason=orphaned_after_restart`로 마감하고 Langfuse trace를 `level=ERROR`로 마감.
   - TTL 만료/체크포인트 누락 → `status=failed`로 마감.
3. Redis 분산 카운터 `ax:concurrency:experiments`를 0으로 재설정한 뒤, 재개된 실험 수만큼 INCR.
4. 복구 결과를 구조화 로그(`event=experiment_recovery`)로 기록하고 운영 알림 채널에 요약 송신.
5. 위 절차가 끝난 후에만 `/api/v1/health`가 `ready=true`를 반환한다 (k8s readinessProbe 연동).

### 역순 종료
**본 프로젝트가 직접 종료하는 컴포넌트만**: Frontend → Backend → (자체) Redis. 사내 공용 서비스(Langfuse/LiteLLM/ClickHouse/Prometheus/OTel/Loki)는 본 프로젝트가 종료하지 않으며 사내 인프라팀이 자체 운영 정책에 따라 관리한다.

Backend는 SIGTERM 수신 후 `LABS_SHUTDOWN_GRACE_SEC`(기본 30초) 동안 진행 실험을 `status=paused`로 체크포인트 저장(자체 또는 임차 Redis에) + 사내 Langfuse에 trace 마감 flush 후 종료한다 (IMPLEMENTATION.md §6.4).

자체 컴포넌트 데이터 보존을 위해 `docker compose down` (볼륨 유지), 완전 초기화 시에만 `docker compose down -v`. **사내 임차 Redis 사용 시에는 자체 컨테이너가 없으므로 본 단계 생략** — 진행 실험 체크포인트는 사내 Redis에 보존되며 다음 Backend 기동 시 자동 복구(7-A).

---

## 전체 마일스톤 체크리스트

| Phase | 핵심 마일스톤 | 검증 기준 |
|-------|-------------|-----------|
| 0 | 테스트 인프라 구축 (backend/tests, frontend/tests, CI) | `pytest --collect-only` 성공, vitest 실행 가능, CI 워크플로우 정의 완료 |
| 1 | 사내 의존성 합의(Langfuse/LiteLLM Virtual Key/ClickHouse 또는 폴백/Prometheus scrape PR/OTel 토큰/Loki 라벨) 완료 + 자체 compose(backend/frontend/(옵션) redis) 가동 + sandbox 이미지 사내 레지스트리 push | `/api/v1/health` 응답에 모든 사내 의존 서비스 ok, 사내 Prometheus 타겟 up, 사내 Langfuse에 score config idempotent 등록 로그 확인 |
| 2 | FastAPI 서버 기동, JWT 인증 작동 | `/health` 200, 인증 없는 요청 401 |
| 3 | Langfuse 프록시 API 전체 동작 | curl로 프롬프트/데이터셋/모델 CRUD 가능 |
| 4 | 단일 테스트 SSE 스트리밍, 배치 실험 실행 | LLM 응답 수신, Langfuse trace 기록, 실험 제어 |
| 5 | 13개 Built-in + LLM Judge + Custom Code 실행 | Langfuse Score 기록, sandbox 격리 동작 |
| 6 | ClickHouse 분석 쿼리, Run 비교 API | 2개 Run 비교 결과 JSON 반환 |
| 7 | 전체 UI 워크플로우 완결 | 프롬프트→데이터셋→실험→평가→비교 UI 동작 |
