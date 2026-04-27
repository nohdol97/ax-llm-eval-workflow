# 사내 인프라 연동 체크리스트

> 사내 공용 인프라(Langfuse / LiteLLM / Prometheus / OpenTelemetry / Loki)와 본 프로젝트(`ax-llm-eval-workflow`)를 연결하기 위한 셋업 명세·결정 사항·검증 방법을 정리한 실행형 가이드.

**참조 (Canonical)**: 책임 분담과 선결 조건의 단일 진실 원본은 [`BUILD_ORDER.md`의 「사내 공용 인프라 의존」 섹션](BUILD_ORDER.md#사내-공용-인프라-의존-canonical)이다. 본 문서는 그 절을 실제 셋업·검증 단계로 풀어쓴 운영 가이드다.

**운영 모드**: **Self-Service** — 본 프로젝트 운영자가 사내 인프라팀 권한을 보유하여 Langfuse / LiteLLM / Prometheus / OTel / Loki / ClickHouse / Redis / Auth 모든 서비스를 **직접 셋업·구성**할 수 있다. 외부 협의·티켓 발행·SLA 대기 단계는 생략한다.

> **참고**: BUILD_ORDER.md의 책임 분담 표(사내 인프라팀 vs 본 프로젝트)는 단일 운영자가 양 책임을 겸임하더라도 **변경 추적·감사 목적**으로 그대로 유지한다. 본 문서의 셋업 항목은 표의 "사내 인프라팀 책임" 컬럼에 해당하는 작업을 직접 수행하는 형태로 재구성됐다.

**문서 상태**: Draft · **Owner**: _(TBD)_ · **Last Updated**: 2026-04-27

---

## 목차

1. [큰 그림 — 단일 트랙 셋업 흐름](#0-큰-그림--단일-트랙-셋업-흐름)
2. [인프라 셋업 9건 (셋업 명세)](#1-인프라-셋업-9건-셋업-명세)
3. [본 프로젝트 자체 준비 (병렬 진행)](#2-본-프로젝트-자체-준비-병렬-진행)
4. [결정 사항 10개](#3-결정-사항-10개)
5. [의존성 우선순위](#4-의존성-우선순위)
6. [검증 방법 (점진적 3단계)](#5-검증-방법-점진적-3단계)
7. [Phase 1 Done Definition](#6-phase-1-done-definition)
8. [셋업 진행 상태 보드](#7-셋업-진행-상태-보드)

---

## 0. 큰 그림 — 단일 트랙 셋업 흐름

외부 협의·SLA 대기 단계가 없으므로 셋업과 본 프로젝트 구현을 **단일 트랙**으로 진행한다. 셋업 9건과 본 프로젝트 자체 준비는 의존성이 없는 한 **모두 병렬 진행 가능**.

```
[Stage 1 — 결정 (즉시, 1일 내)]
   결정 사항 10개 확정 (§3)
   - Redis 임차 vs 자체 / ClickHouse 직접 vs 폴백 / 환경 분리 / sampling 등
   - 본인이 결정 권한 보유 → 빠른 확정 가능

[Stage 2 — 인프라 셋업 9건 (병렬)]              [Stage 3 — 본 프로젝트 자체 준비 (Stage 2와 병렬)]
   2.1 Langfuse 프로젝트+Key 생성                3.1 docker-compose 단순화 버전
   2.2 LiteLLM 모델 등록+Virtual Key             3.2 .env.example 신 템플릿
   2.3 ClickHouse readonly 계정 (또는 폴백)       3.3 ADR-011/012 작성
   2.4 Prometheus scrape+rules                   3.4 Mock fixture 6종 (Phase 0)
   2.5 OTel Collector 엔드포인트                  3.5 FastAPI 스캐폴드 (Phase 2)
   2.6 Loki 라벨 규약+수집기                      3.6 sandbox 이미지 검증
   2.7 Redis 정책 적용                           3.7 모델 카탈로그 정합 점검
   2.8 Auth JWKS+RBAC 매핑
   2.9 네트워크/도메인/레지스트리

                              ↓
                  [Stage 4 — 통합 검증]
                  - .env 주입
                  - smoke test (curl 9건, §5 Stage B)
                  - Backend /api/v1/health 종합 OK (§5 Stage C)
                              ↓
                  [Stage 5 — Phase 1 마일스톤]
                  - Done Definition 7개 충족 (§6)
                              ↓
                       Phase 2~7 진입
```

**핵심 원칙**:
- 결정(§3)을 가장 먼저 확정 — 이후 셋업의 분기점이 됨
- Mock fixture 기반 TDD로 본 프로젝트 구현이 셋업과 병렬 가능
- 셋업 9건은 의존성 그래프(§4)를 따라 병렬화 — 단일 운영자라도 Stage 2를 한 번에 1건씩 처리할 필요 없음

---

## 1. 인프라 셋업 9건 (셋업 명세)

각 항목은 독립적으로 셋업 가능하며 병렬 진행을 권장한다. 본 절의 "셋업 명세"는 변경 PR/감사 추적용 자료로도 활용한다 (변경 사유·권한 부여 명세를 그대로 보존).

### 1.1 Langfuse 프로젝트 + Key 생성
**액션**
- [ ] organization 확인 (`labs` 또는 사내 표준 명명)
- [ ] 환경별 project 생성: `labs-ax-eval-dev`, `labs-ax-eval-staging`, `labs-ax-eval-prod`
- [ ] 각 project별 Public/Secret Key 발급 (총 3쌍)
- [ ] `.env.{development,staging,production}` 또는 secret store에 주입
- [ ] smoke test 통과

**셋업 명세**
- 권한 부여:
  - Prompt Management 쓰기 (`POST/PATCH /api/public/v2/prompts`)
  - Dataset 쓰기 (`POST /api/public/datasets`, `POST /datasets/{name}/items`)
  - Score 쓰기 (`POST /api/public/scores`)
  - score_config 등록 (`POST /api/public/score-configs`)
  - prompt label 승격 (`PATCH /prompts/{name}/versions/{version}/labels`) — admin RBAC 매핑
- 엔드포인트 URL: 사내 Langfuse 호스트 (예: `https://langfuse.internal.example.com`)
- Langfuse 버전 확인 (v3.x 가정 — Score Config API 등 v3 기능 사용)

**검증 명령**
```bash
curl -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "$LANGFUSE_HOST/api/public/health"
# 기대: 200 OK
```

**참조**: BUILD_ORDER.md 작업 1-3, IMPLEMENTATION.md, LANGFUSE.md

---

### 1.2 LiteLLM 모델 등록 + Virtual Key
**액션**
- [ ] 사내 LiteLLM `config.yaml`에 모델 추가 PR (또는 직접 적용)
- [ ] LiteLLM 재기동 또는 hot-reload
- [ ] 본 프로젝트 전용 Virtual Key 발급 (환경별 3개)
- [ ] 일일 비용 한도 설정
- [ ] `.env`에 주입
- [ ] smoke test 통과

**셋업 명세**
- 등록 모델 (본 프로젝트 frontend 모델 카탈로그와 일치):
  - Azure OpenAI: `gpt-4o`, `gpt-4.1`
  - Google: `gemini-2.5-pro`, `gemini-2.5-flash`
  - Anthropic: `claude-4-6-opus`, `claude-4-5-sonnet`
  - AWS Bedrock: `claude-4-5`, `llama-3-3-70b`
  - OpenAI: `o4-mini`, `o3`
- 모델 식별자(`provider/model-name`) 표기는 `frontend/src/lib/mock/data.ts`의 모델 ID와 일치
- Virtual Key 정책:
  - 일일 비용 한도: $50 (운영) / $10 (스테이징) / $5 (개발)
  - rate limit: 사내 LiteLLM 정책 따름
  - **`success_callback: []`** (Langfuse callback 비활성화 — Labs Backend가 trace/generation 기록 전담)

**검증 명령**
```bash
curl -H "Authorization: Bearer $LITELLM_VIRTUAL_KEY" "$LITELLM_BASE_URL/health"
# 기대: 200 OK

# 모델 목록 확인
curl -H "Authorization: Bearer $LITELLM_VIRTUAL_KEY" "$LITELLM_BASE_URL/model/info" \
  | jq '.data[] | .model_name'
# 기대: 등록한 10개 모델 표시
```

**참조**: BUILD_ORDER.md 작업 1-2, CLAUDE.md 보안 규칙

---

### 1.3 ClickHouse readonly 계정 (또는 폴백 결정)
**결정 분기**
- [ ] **Option 1 (권장)**: readonly 계정 직접 생성
- [ ] **Option 2 (폴백)**: 보안 정책상 직접 접근 불가 → ADR-012 작성 후 Langfuse public API 폴백

**Option 1 액션**
- [ ] Langfuse 내부 ClickHouse에 `labs_readonly` 사용자 생성
- [ ] 권한 부여 (`GRANT SELECT ON langfuse.* TO labs_readonly`)
- [ ] 접속 제한: Backend 네트워크 IP allowlist 또는 host_regex
- [ ] TLS 강제 (8443 포트, `CLICKHOUSE_SECURE=true`)
- [ ] `.env`에 주입

**Option 1 셋업 명세**
- 사용자명: `labs_readonly`
- 권한: `GRANT SELECT ON langfuse.* TO labs_readonly` 한정 (INSERT/UPDATE/DELETE 명시 거부)
- TLS 강제, 비밀번호는 secret store에 보관

**Option 2 액션**
- [ ] `docs/adr/ADR-012-clickhouse-fallback.md` 작성 (성능/유연성 trade-off 기록)
- [ ] `USE_LANGFUSE_PUBLIC_API_FALLBACK=true` 설정
- [ ] Phase 6에서 `clickhouse_client.py` 대신 Langfuse SDK 사용

**검증 명령 (Option 1)**
```bash
clickhouse-client --host "$CLICKHOUSE_HOST" --port "$CLICKHOUSE_PORT" --secure \
  --user "$CLICKHOUSE_READONLY_USER" --password "$CLICKHOUSE_READONLY_PASSWORD" \
  --query "SELECT 1"
# 기대: 1

# 쓰기 거부 확인
clickhouse-client ... --query "INSERT INTO langfuse.traces VALUES (...)"
# 기대: Access denied
```

**참조**: BUILD_ORDER.md 작업 1-4, LANGFUSE.md §3

---

### 1.4 Prometheus scrape job + recording/alert rules
**액션**
- [ ] 사내 `prometheus.yml`에 scrape job 추가 (PR 또는 직접 적용)
- [ ] `recording.yml`에 `ax:*` 룰 추가 (OBSERVABILITY.md §2.4)
- [ ] `alerts.yml`에 alert rules 추가 (OBSERVABILITY.md §2.5)
- [ ] Alertmanager 라우팅 규칙 추가 (`team=labs` matcher → Slack/Telegram webhook)
- [ ] Prometheus 재기동 또는 reload (`curl -X POST $PROMETHEUS_QUERY_URL/-/reload`)
- [ ] targets up 확인

**셋업 명세 — scrape job**
```yaml
# prometheus.yml
- job_name: ax-llm-eval-workflow-backend
  static_configs:
    - targets: ['backend.labs.internal.example.com:8000']
  metrics_path: /metrics
  scrape_interval: 15s
```

**셋업 명세 — recording rules** (OBSERVABILITY.md §2.4)
- `ax:active_users:wau`, `ax:wvpi:7d`, `ax:experiment_cycle:p50_24h`
- `ax:llm_budget_utilization_ratio:5m`, `ax:baseline:*`
- 기타 `ax:*` 사전 집계 룰

**셋업 명세 — alert rules** (OBSERVABILITY.md §2.5)
- `LabsBackendDown`, `LangfuseUnreachable`, `LiteLLMHighErrorRate`
- `EvaluatorSandboxOOM`, `ExperimentStuckRunning`
- 기타 임계치 기반 alert

**셋업 명세 — Alertmanager 라우팅**
```yaml
route:
  routes:
    - matchers: [team="labs"]
      receiver: labs-channels
receivers:
  - name: labs-channels
    slack_configs: [{api_url: <webhook>, channel: '#labs-alerts'}]
    telegram_configs: [{bot_token: <token>, chat_id: <id>}]
```

**검증 명령**
```bash
# scrape 등록 확인
curl -s "$PROMETHEUS_QUERY_URL/api/v1/targets" \
  | jq '.data.activeTargets[] | select(.labels.job=="ax-llm-eval-workflow-backend")'
# 기대: health="up"

# recording rule 적용 확인
curl -s "$PROMETHEUS_QUERY_URL/api/v1/rules" \
  | jq '.data.groups[] | select(.name | startswith("ax_"))'
# 기대: ax:* 룰 목록

# alert rule 적용 확인
curl -s "$PROMETHEUS_QUERY_URL/api/v1/alerts"
```

**참조**: BUILD_ORDER.md 작업 1-7-A, OBSERVABILITY.md §2.2~2.5

---

### 1.5 OpenTelemetry Collector
**액션**
- [ ] 본 프로젝트용 OTLP 엔드포인트 확정 (사내 Collector 기존 endpoint 재사용 또는 별도 라우트)
- [ ] 인증 토큰 발급 (Bearer token)
- [ ] 백엔드(Tempo/Jaeger) 라우팅 정책 확인
- [ ] `.env`에 주입
- [ ] 첫 trace 도착 확인

**셋업 명세**
- OTLP/HTTP 엔드포인트 (예: `https://otel-collector.internal.example.com:4318`)
- 본 프로젝트 식별자:
  - `service.namespace=labs`
  - `service.name=ax-llm-eval-workflow-backend`
- Sampling 정책 (환경별):
  - dev: 0.0 (off)
  - staging: 1.0
  - prod: 0.1 (`parentbased_traceidratio`)
- 백엔드 라우팅: 본 프로젝트 trace를 Tempo/Jaeger의 어느 tenant로 보낼지 결정

**검증 명령**
```bash
# 도달성 확인 (HEAD/OPTIONS는 405 응답이 정상 — 엔드포인트가 살아있다는 의미)
curl -i "$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces"
# 기대: 4xx 응답 (도달성 OK)

# Backend 기동 후 Tempo/Jaeger UI에서 service.namespace=labs 필터로 trace 도착 확인
```

**참조**: BUILD_ORDER.md 작업 1-7-B

---

### 1.6 Loki 라벨 규약 + 수집기
**액션**
- [ ] 본 프로젝트 라벨 규약 확정
- [ ] 사내 수집기(Promtail/Vector) 설정에 본 프로젝트 컨테이너 추가
- [ ] 보존 정책 설정 (환경별 retention)
- [ ] `.env`에 주입
- [ ] 첫 JSON 로그 도착 확인

**셋업 명세**
- 라벨 규약:
  - `service=ax-llm-eval-workflow-backend`
  - `env={dev,staging,prod}`
  - `component={api,worker,sandbox}`
- 수집기 설정 (Promtail/Vector):
  - 본 프로젝트 컨테이너 stdout pickup
  - JSON 파싱 활성화 (필드 자동 인덱싱)
- 보존 정책:
  - prod: 30일
  - staging: 14일
  - dev: 7일
- PII 미포함 정책: 본 프로젝트 측이 CI 단계 PII 스캐너로 강제 (수집기에 추가 필터 불요)

**검증 명령**
```bash
curl "$LOKI_URL/ready"
# 기대: ready

# Backend 기동 후 라벨 필터로 로그 도착 확인
curl -G -s "$LOKI_URL/loki/api/v1/query_range" \
  --data-urlencode 'query={service="ax-llm-eval-workflow-backend"}' \
  --data-urlencode "start=$(date -u -v-5M +%Y-%m-%dT%H:%M:%SZ)" \
  --data-urlencode "end=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  | jq '.data.result | length'
# 기대: > 0
```

**참조**: BUILD_ORDER.md 작업 1-7-C

---

### 1.7 Redis 정책 적용
**결정 분기**
- [ ] **Option 1 (권장)**: 사내 공용 Redis의 별도 DB 임차
- [ ] **Option 2**: 본 프로젝트 자체 단일 컨테이너 운영

**Option 1 액션**
- [ ] DB 번호 결정 (충돌 회피, 예: `LABS_REDIS_DB=1`)
- [ ] 키 prefix 합의 (`ax:*`로 격리)
- [ ] 메모리 사용량 한도 모니터링 설정 (1GB 권장)
- [ ] `.env`에 `REDIS_URL` 주입

**Option 2 액션**
- [ ] `docker/docker-compose.yml`에 `redis:7` 서비스 추가
- [ ] 볼륨 영속화 (`/data` 마운트, AOF 활성화)
- [ ] 비밀번호 설정 (`requirepass`)
- [ ] 본 프로젝트 backend의 `depends_on` 추가

**셋업 명세**
- 키 스키마: `ax:experiment:{id}`, `ax:notification:{user_id}:*`, `ax:concurrency:experiments` 등 (IMPLEMENTATION.md §1.5)
- TTL 정책: 실험 상태 24h, 알림 30일

**검증 명령**
```bash
redis-cli -u "$REDIS_URL" PING
# 기대: PONG

# DB 격리 확인 (Option 1)
redis-cli -u "$REDIS_URL" CONFIG GET databases
# 기대: 16+ (사내 Redis 설정)

# 키 충돌 확인
redis-cli -u "$REDIS_URL" --scan --pattern '*' | head -5
# 기대: 빈 결과 또는 ax:* 만 표시
```

**참조**: BUILD_ORDER.md 「사내 공용 인프라 의존」 절, IMPLEMENTATION.md §1.5

---

### 1.8 Auth (JWT) — JWKS + RBAC 매핑
**액션**
- [ ] 사내 Auth 서비스 JWKS endpoint 확정
- [ ] audience/issuer 값 결정
- [ ] RBAC 매핑 결정 (사내 그룹 ↔ admin/user/viewer)
- [ ] `.env`에 주입
- [ ] JWT 검증 통합 테스트 통과

**셋업 명세**
- JWKS URL: `https://auth.internal.example.com/.well-known/jwks.json`
- audience: `labs`
- issuer: `https://auth.internal.example.com`
- RBAC 클레임 위치 결정: `roles` 배열? `groups` 배열? custom claim?
- 그룹 매핑 (예시):
  - `labs-platform-admin` → `admin` (Custom Code Evaluator 실행, 거버넌스, 삭제)
  - `labs-researcher` → `user` (실험 생성/실행, 데이터셋 업로드)
  - 그 외 사내 인증된 사용자 → `viewer` (읽기 전용, 기본값)

**검증 명령**
```bash
curl "$AUTH_JWKS_URL"
# 기대: JWKS JSON (keys 배열 + kid)

# Backend 기동 후 JWT 미들웨어 테스트
curl http://localhost:8000/api/v1/projects
# 기대: 401 (JWT 미포함)

curl -H "Authorization: Bearer $TEST_JWT" http://localhost:8000/api/v1/projects
# 기대: 200 (유효 JWT)
```

**참조**: BUILD_ORDER.md 작업 2-3, API_DESIGN.md 권한 섹션

---

### 1.9 네트워크 / 도메인 / 컨테이너 레지스트리
**액션**
- [ ] 호스트 도메인 명명 합의 (사내 표준 따름)
- [ ] 네트워크 도달성 검증 (Backend → 모든 사내 서비스)
- [ ] Frontend 외부 노출 도메인 + TLS 인증서 발급
- [ ] 컨테이너 레지스트리 push 권한 확인
- [ ] sandbox 이미지 사내 레지스트리 push

**셋업 명세**
- 호스트 도메인:
  - Backend (internal): `backend.labs.internal.example.com` (Prometheus scrape 대상)
  - Frontend (external): `labs.example.com` (사내 reverse proxy 뒤)
- TLS 인증서: 사내 reverse proxy가 종단 (Let's Encrypt 또는 사내 CA)
- 네트워크 정책: Backend → Langfuse / LiteLLM / ClickHouse / Redis / Prometheus / OTel / Loki egress 허용
- 컨테이너 레지스트리:
  - 경로: `registry.internal.example.com/labs/*`
  - 이미지: `ax-llm-eval-workflow-backend`, `-frontend`, `ax-eval-sandbox`

**검증 명령**
```bash
# 네트워크 도달성 (Backend 컨테이너 내부에서)
nc -zv langfuse.internal.example.com 443
nc -zv litellm.internal.example.com 443
nc -zv clickhouse.internal.example.com 8443
nc -zv redis.internal.example.com 6379
# 기대: 모두 succeeded

# 외부 도메인 + TLS
curl -I https://labs.example.com
# 기대: 200/301/302 + 유효한 TLS 인증서

# 컨테이너 레지스트리 push
docker tag ax-eval-sandbox:1.0.0 registry.internal.example.com/labs/ax-eval-sandbox:1.0.0
docker push registry.internal.example.com/labs/ax-eval-sandbox:1.0.0
# 기대: push 성공
```

**참조**: BUILD_ORDER.md 작업 1-1, 1-5

---

## 2. 본 프로젝트 자체 준비 (병렬 진행)

§1과 무관하게 즉시 착수 가능한 작업. 셋업 결과를 `.env`로 받아 결합하는 인터페이스를 분리해 둔다.

| # | 항목 | 산출물 | §1 의존성 |
|---|---|---|---|
| 2-1 | `.env.example` 신 버전 | `docker/.env.example` (BUILD_ORDER 1-3 템플릿 그대로) | 없음 |
| 2-2 | `docker-compose.yml` 단순화 | backend / frontend / (옵션) redis만 정의 | 없음 |
| 2-3 | `docker-compose.override.yml` | 로컬 개발 (포트 노출, 볼륨 마운트, 핫리로드) | 없음 |
| 2-4 | `docker-compose.demo.yml` | seed 컨테이너 + 데모 score config | 없음 (Key는 추후 주입) |
| 2-5 | `docker-compose.prod.yml` | 사내 reverse proxy 전제, secrets 마운트 | 1.9 호스트 명명 결정 후 |
| 2-6 | ADR-011 시크릿 정책 | `docs/adr/ADR-011-secrets-management.md` | 없음 |
| 2-7 | ADR-012 ClickHouse 폴백 (조건부) | `docs/adr/ADR-012-clickhouse-fallback.md` | 1.3 결정 후 |
| 2-8 | Mock fixtures (Phase 0) | MockLangfuse / LiteLLM / ClickHouse / Redis / OTel / Loki — `backend/tests/fixtures/` | 없음 |
| 2-9 | FastAPI 스캐폴드 (Phase 2) | `backend/app/` 구조 + config + 헬스체크 + observability 통합 | Mock fixture만 있으면 시작 가능 |
| 2-10 | sandbox 이미지 빌드 | `docker/eval-sandbox/` (이미 있음, 검증만) | 1.9 레지스트리 권한 (push 시점에만) |
| 2-11 | 모델 카탈로그 정합 점검 | `frontend/src/lib/mock/data.ts`의 모델 ID와 1.2 등록 모델 ID 일치 | 없음 |

**핵심**: Mock fixture 기반 TDD로 Phase 0~5 대부분이 사내 의존성 없이 개발 가능. 셋업 결과는 Phase 1 인프라 검증과 Phase 6 분석 쿼리에서 비로소 결합.

---

## 3. 결정 사항 10개

본인이 결정 권한 보유 — Stage 1에서 한 번에 확정 가능.

| # | 결정 | 옵션 | 권고 | 결정 |
|---|---|---|---|---|
| 1 | Redis 운영 | ① 사내 공용 Redis 임차 / ② 자체 컨테이너 | ① (운영 부담 최소) | [ ] |
| 2 | ClickHouse 접근 | ① readonly 계정 / ② Langfuse public API 폴백 | ① 우선 시도, 사내 정책 충돌 시 ② | [ ] |
| 3 | 환경 분리 | dev/staging/prod 별도 Langfuse 프로젝트? | 별도 프로젝트 권장 (label/key 격리) | [ ] |
| 4 | OTel sampling | 환경별 비율 | dev=0.0 / staging=1.0 / prod=0.1 | [ ] |
| 5 | Loki 보존 정책 | 7d / 30d / 90d | 운영 30d, 스테이징 14d, 개발 7d | [ ] |
| 6 | 컨테이너 레지스트리 | Harbor / GHCR / 내부 표준 | 사내 표준 따름 | [ ] |
| 7 | Secret store | Vault / AWS SM / 환경변수만 | 운영은 Vault 권장, 개발은 `.env` | [ ] |
| 8 | Frontend 외부 도메인 | TBD | 사내 명명 규칙 따름 | [ ] |
| 9 | 인증 방식 | mTLS / VPN / IP allowlist | 사내 보안 정책 따름 | [ ] |
| 10 | RBAC 매핑 | 사내 그룹 ↔ admin/user/viewer | `labs-platform-admin`/`labs-researcher`/기타 매핑 (1.8) | [ ] |

---

## 4. 의존성 우선순위

셋업·구현의 의존성 그래프. 위에서 아래로 진행하면 막히지 않는다. 같은 단계 내 항목은 병렬 가능.

| 단계 | 항목 | 차단되는 후속 작업 |
|---|---|---|
| 🔴 **Stage 1 — 결정** | 결정 1, 2, 3, 4, 5, 7 (§3) | 모든 Stage 2 셋업·자체 준비의 분기 결정 |
| 🟡 **Stage 2 — 즉시 셋업 (병렬)** | 1.1 Langfuse / 1.2 LiteLLM / 1.3 ClickHouse / 1.7 Redis / 1.8 Auth | Phase 4 LLM 호출, Phase 6 분석, Phase 2 JWT 통합 |
| 🟡 **Stage 2 — 즉시 셋업 (병렬)** | 1.5 OTel / 1.6 Loki | Phase 1 observability 검증 |
| 🟢 **Stage 3 — Backend 기동 후 등록** | 1.4 Prometheus scrape (Backend `/metrics` 노출 필요) | Phase 1 마일스톤 |
| 🟢 **Stage 3 — 운영 준비** | 1.9 네트워크/도메인/레지스트리 | 운영 배포 |

**병렬 진행 팁**:
- Stage 1 결정을 1일 내 끝내면 Stage 2 셋업 7개를 모두 동시에 시작 가능
- §2 자체 준비 작업(특히 Mock fixture, FastAPI 스캐폴드)은 Stage 1·2와 무관하게 병렬
- Stage 3은 Backend가 기동되어야 가능 — `/metrics` 노출 후 Prometheus scrape 등록

---

## 5. 검증 방법 (점진적 3단계)

### Stage A — 셋업 전·자체 준비 단계
```bash
# Mock fixture 기반 단위/통합 테스트
cd backend && pytest tests/ -v
cd frontend && npm run test
```

### Stage B — 셋업 일부 완료 후 (smoke test)
```bash
# 각 서비스 엔드포인트 단독 도달성 확인 — Backend 컨테이너 네트워크에서 실행
curl -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" "$LANGFUSE_HOST/api/public/health"
curl -H "Authorization: Bearer $LITELLM_VIRTUAL_KEY" "$LITELLM_BASE_URL/health"
clickhouse-client --secure --user "$CLICKHOUSE_READONLY_USER" \
  --password "$CLICKHOUSE_READONLY_PASSWORD" --query "SELECT 1"
redis-cli -u "$REDIS_URL" PING
curl "$PROMETHEUS_QUERY_URL/-/ready"
curl "$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces"  # 405 도달성 OK
curl "$LOKI_URL/ready"
curl "$AUTH_JWKS_URL"  # JWKS JSON 응답 확인
```

### Stage C — 전체 셋업 완료 후 (종합 검증)
```bash
# 1. 자체 compose 기동
docker compose -f docker/docker-compose.yml -f docker/docker-compose.override.yml up -d

# 2. Backend 종합 헬스체크
curl -s http://localhost:8000/api/v1/health | jq .
# 기대 응답:
# {
#   "status": "ok",
#   "services": {
#     "langfuse": {"status": "ok", "latency_ms": 42},
#     "litellm": {"status": "ok", "latency_ms": 18},
#     "clickhouse": {"status": "ok", "latency_ms": 8},
#     "redis": {"status": "ok", "latency_ms": 2},
#     "prometheus": {"status": "ok", "latency_ms": 12},
#     "otel": {"status": "ok", "latency_ms": 15},
#     "loki": {"status": "ok", "latency_ms": 10}
#   }
# }

# 3. 사내 Prometheus에서 본 서비스 scrape UP 확인
curl -s "$PROMETHEUS_QUERY_URL/api/v1/targets" \
  | jq '.data.activeTargets[] | select(.labels.job=="ax-llm-eval-workflow-backend") | .health'
# "up"

# 4. Tempo/Jaeger UI에서 첫 trace 도착 확인 (브라우저)
# service.namespace=labs, service.name=ax-llm-eval-workflow-backend 필터

# 5. Loki에서 라벨 필터로 첫 JSON 로그 확인
# {service="ax-llm-eval-workflow-backend"} 필터

# 6. Score config 등록 결과 (Backend 부팅 로그)
docker compose logs backend | grep score_config_registered

# 7. Sandbox 이미지 사내 레지스트리 push 확인
docker pull registry.internal.example.com/labs/ax-eval-sandbox:1.0.0
```

---

## 6. Phase 1 Done Definition

다음 7가지가 모두 충족되어야 Phase 1 마일스톤 도달로 간주한다.

- [ ] **9건 셋업 완료**: §1 모든 항목 셋업 명세대로 적용 + `.env.production` 주입
- [ ] **자체 compose 가동**: backend / frontend / (옵션) redis healthy
- [ ] **종합 헬스체크 OK**: `/api/v1/health` 응답에서 7개 사내 의존 서비스 모두 ok
- [ ] **사내 Prometheus scrape UP**: 본 프로젝트 job(`ax-llm-eval-workflow-backend`)이 `up` 상태
- [ ] **OTel 첫 trace 도착**: Tempo/Jaeger UI에서 `service.namespace=labs` 필터로 확인
- [ ] **Loki 첫 JSON 로그 도착**: `service` 라벨 필터로 확인
- [ ] **Score config idempotent 등록**: Backend 부팅 로그에 `score_config_registered` 기록 + 재기동 시 skip 확인

---

## 7. 셋업 진행 상태 보드

> 실시간 추적용. 각 항목 셋업 시작/완료 시 상태 갱신.

| # | 항목 | 시작일 | 상태 | 완료일 | 비고 |
|---|---|---|---|---|---|
| 1.1 | Langfuse Key | _ | ⬜ 미착수 | _ | _ |
| 1.2 | LiteLLM 모델 등록 + Virtual Key | _ | ⬜ 미착수 | _ | _ |
| 1.3 | ClickHouse readonly | _ | ⬜ 미착수 | _ | Option 1 우선 시도 |
| 1.4 | Prometheus scrape + 룰 | _ | ⬜ 미착수 | _ | Backend `/metrics` 가동 후 |
| 1.5 | OTel Collector | _ | ⬜ 미착수 | _ | _ |
| 1.6 | Loki 라벨 + 수집기 | _ | ⬜ 미착수 | _ | _ |
| 1.7 | Redis 정책 | _ | ⬜ 미착수 | _ | 결정 1 따라 |
| 1.8 | JWKS + RBAC 매핑 | _ | ⬜ 미착수 | _ | _ |
| 1.9 | 네트워크 + 도메인 + 레지스트리 | _ | ⬜ 미착수 | _ | _ |

**상태 표기**: ⬜ 미착수 / 🟡 진행 중 / 🟢 셋업 완료 / ✅ 본 프로젝트 통합 검증 완료

---

## 부록 A. 참조 문서

| 문서 | 절 | 내용 |
|---|---|---|
| [BUILD_ORDER.md](BUILD_ORDER.md) | 「사내 공용 인프라 의존」 [Canonical] | 책임 분담 + 선결 조건 10개 (단일 진실 원본) |
| [BUILD_ORDER.md](BUILD_ORDER.md) | Phase 1 작업 1-1~1-9 | 인프라 셋업 상세 |
| [BUILD_ORDER.md](BUILD_ORDER.md) | Phase 8 8-1~8-5 | 운영 인계 — 책임 분담 적용 |
| [OBSERVABILITY.md](OBSERVABILITY.md) | §2.2~2.5 | 메트릭 카탈로그 + recording rules + alert rules |
| [LANGFUSE.md](LANGFUSE.md) | §3 | ClickHouse 직접 쿼리 vs public API 폴백 |
| [IMPLEMENTATION.md](IMPLEMENTATION.md) | §1.5 | Redis 키 스키마 (`ax:*`) |
| [API_DESIGN.md](API_DESIGN.md) | §권한 | RBAC API 매트릭스 (admin/user/viewer) |

## 부록 B. 변경 이력

| 일자 | 변경 | 작성자 |
|---|---|---|
| 2026-04-27 | 초안 작성 (외부 협의 가정) | _(TBD)_ |
| 2026-04-27 | Self-Service 모드 반영 — 티켓 9건 → 셋업 명세 9건, 단일 트랙 흐름, 진행 보드 단순화 | _(TBD)_ |
