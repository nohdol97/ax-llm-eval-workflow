# 사내 인프라 연동 체크리스트

> 사내 공용 인프라(Langfuse / LiteLLM / Prometheus / OpenTelemetry / Loki)와 본 프로젝트(`ax-llm-eval-workflow`)를 연결하기 위한 준비 단계·티켓 템플릿·결정 사항·검증 방법을 정리한 실행형 가이드.

**참조 (Canonical)**: 책임 분담과 선결 조건의 단일 진실 원본은 [`BUILD_ORDER.md`의 「사내 공용 인프라 의존」 섹션](BUILD_ORDER.md#사내-공용-인프라-의존-canonical)이다. 본 문서는 그 절을 실행 가능한 형태로 풀어쓴 운영 가이드다.

**문서 상태**: Draft · **Owner**: _(TBD)_ · **Last Updated**: 2026-04-27

---

## 목차

1. [큰 그림 — 두 트랙 병행 전략](#0-큰-그림--두-트랙-병행-전략)
2. [사내 인프라팀에 요청할 항목 (티켓 9건)](#1-사내-인프라팀에-요청할-항목-티켓-9건)
3. [본 프로젝트에서 미리 만들 수 있는 것](#2-본-프로젝트에서-미리-만들-수-있는-것)
4. [합의·확정해야 할 결정 사항](#3-합의확정해야-할-결정-사항)
5. [Critical Path — 가장 먼저 받아야 할 답](#4-critical-path--가장-먼저-받아야-할-답)
6. [검증 방법 (점진적 3단계)](#5-검증-방법-점진적-3단계)
7. [Phase 1 Done Definition](#6-phase-1-done-definition)
8. [티켓 진행 상태 보드](#7-티켓-진행-상태-보드)

---

## 0. 큰 그림 — 두 트랙 병행 전략

사내 회신은 비동기로 평균 1~2주 소요된다. 회신을 기다리는 동안 본 프로젝트에서 미리 할 수 있는 일을 최대한 진행하는 것이 핵심.

```
      [외부 트랙 — 사내 협의]                [내부 트랙 — 자체 준비]
   ─────────────────────────────────       ─────────────────────────────────
   ① 사전 미팅 (책임 분담 + 10 선결조건)    ① docker-compose 단순화 버전
   ② 티켓 9건 발행 (병렬)                   ② .env.example 신 템플릿
   ③ 회신 대기 (1~2주, 비동기)              ③ ADR-011 시크릿 정책
                                             ④ Mock fixture 6종 (Phase 0)
                                             ⑤ FastAPI 스캐폴드 (Phase 2)
                                             ⑥ sandbox 이미지 검증
   ─────────────────────────────────       ─────────────────────────────────
                              ↓
                   ④ 회신 도착 → .env 주입
                              ↓
                   ⑤ Smoke test (curl 9건)
                              ↓
                   ⑥ Backend /api/v1/health 종합 OK
                              ↓
                   ⑦ Phase 1 마일스톤 도달
```

**핵심 원칙**:
- Mock fixture로 Phase 0~5 대부분을 사내 의존성 없이 개발 가능 (TDD)
- 사내 회신은 Phase 1 인프라 검증과 Phase 6 분석 쿼리에서 비로소 필요
- 사내 인프라팀 SLA 추적을 위해 모든 요청은 티켓 단위로 분리 발행

---

## 1. 사내 인프라팀에 요청할 항목 (티켓 9건)

각 항목은 **개별 티켓**으로 분리 발행한다. 추적이 쉽고 회신도 분산 처리된다.

### 1.1 Langfuse 프로젝트 + Key 발급
- [ ] 티켓 발행
- [ ] 회신 수령
- [ ] `.env` 주입 + smoke test 통과

**티켓 템플릿**
```
제목: [labs] Langfuse 프로젝트 + Public/Secret Key 발급

요청 사항:
- 본 프로젝트 전용 organization/project 생성: `labs-ax-llm-eval-workflow`
- 환경 분리: dev / staging / prod 각각 별도 프로젝트 + 별도 Key
- Public/Secret Key 발급 (총 3쌍)
- 권한:
  · Prompt Management 쓰기 (POST/PATCH /api/public/v2/prompts)
  · Dataset 쓰기 (POST /api/public/datasets)
  · Score 쓰기 (POST /api/public/scores)
  · score_config 등록 (POST /api/public/score-configs)
  · prompt label 승격 (PATCH labels) — admin RBAC 매핑
- 엔드포인트 URL 안내 (예: https://langfuse.internal.example.com)
- (선택) 사내 Langfuse 버전 명시 (v3.x)

연락처: <project owner>
예상 SLA: 1주
참조: BUILD_ORDER.md 작업 1-3, IMPLEMENTATION.md
```

### 1.2 LiteLLM 모델 등록 + Virtual Key
- [ ] 모델 등록 PR 제출 (사내 `config.yaml`)
- [ ] PR 머지
- [ ] Virtual Key 수령 + 사용량 한도 합의

**티켓 템플릿**
```
제목: [labs] LiteLLM 모델 등록 PR + 본 프로젝트용 Virtual Key 발급

요청 사항:
- 사용 모델 목록 (사내 `config.yaml` 추가):
  · Azure OpenAI: gpt-4o, gpt-4.1
  · Google: gemini-2.5-pro, gemini-2.5-flash
  · Anthropic: claude-4-6-opus, claude-4-5-sonnet
  · AWS Bedrock: claude-4-5, llama-3-3-70b
  · OpenAI: o4-mini, o3
- 모델 식별자(`provider/model-name`)는 본 프로젝트
  `frontend/src/lib/mock/data.ts`의 모델 카탈로그와 사전 일치
- 본 프로젝트 전용 Virtual Key 발급:
  · 사용량/예산/rate limit 분리
  · 일일 비용 한도: $50 (운영) / $10 (스테이징) / $5 (개발)
  · `success_callback: []` (Langfuse callback 비활성화 합의 — Labs Backend가
    trace/generation 기록 전담)
- 엔드포인트 URL 안내

연락처: <project owner>
예상 SLA: 1~2주 (모델 등록 PR 리뷰 시간 포함)
참조: BUILD_ORDER.md 작업 1-2, CLAUDE.md 보안 규칙
```

### 1.3 ClickHouse readonly 계정 (또는 폴백)
- [ ] 티켓 발행
- [ ] 회신 (계정 발급 vs 폴백 결정)
- [ ] 폴백 채택 시 → ADR-012 작성

**티켓 템플릿**
```
제목: [labs] Langfuse 내부 ClickHouse readonly 계정 발급 (또는 폴백 합의)

배경: 본 프로젝트 Phase 6 분석 쿼리는 latency 분포·점수 분포·아이템별 비교 등
복잡한 집계가 필요. Langfuse public API로는 성능/유연성 한계가 있어
ClickHouse 직접 조회를 선호.

요청 (Option 1, 권장):
- 사용자명: `labs_readonly`
- 권한: `GRANT SELECT ON langfuse.* TO labs_readonly` 한정
  · INSERT/UPDATE/DELETE 명시 거부
- 접속 제한: 본 프로젝트 Backend 네트워크에서만 접근 (host_regex 또는 IP allowlist)
- TLS 강제 (`CLICKHOUSE_SECURE=true`, 8443 포트)
- 비밀번호 발급

대안 (Option 2, 폴백):
- 사내 보안정책상 직접 접근 거부 시
- 본 프로젝트가 Langfuse public API(`/api/public/metrics`, `/api/public/observations`)
  로 폴백 구현 → 성능/유연성 trade-off를 ADR-012로 명시
- 이 경우 회신에 "거부 사유" 명시 요청

연락처: <project owner>
예상 SLA: 1주
참조: BUILD_ORDER.md 작업 1-4, LANGFUSE.md §3
```

### 1.4 Prometheus scrape job + 룰 등록
- [ ] scrape job PR 제출 (사내 `prometheus.yml`)
- [ ] recording rules PR 제출 (`ax:*`)
- [ ] alert rules PR 제출 + Alertmanager 라우팅
- [ ] PR 머지 + targets up 확인

**티켓 템플릿**
```
제목: [labs] Prometheus scrape job + recording/alert rules + Alertmanager 라우팅 PR

요청 사항:
1. scrape job 추가 (사내 prometheus.yml):
   - job_name: ax-llm-eval-workflow-backend
     static_configs:
       - targets: ['backend.labs.internal.example.com:8000']
     metrics_path: /metrics
     scrape_interval: 15s

2. Recording rules (`ax:*`) — 본 프로젝트 OBSERVABILITY.md §2.4 발췌본 첨부
   - ax:active_users:wau, ax:wvpi:7d, ax:experiment_cycle:p50_24h, ...

3. Alert rules — 본 프로젝트 OBSERVABILITY.md §2.5 임계치 첨부
   - LabsBackendDown, LangfuseUnreachable, EvaluatorSandboxOOM, ...

4. Alertmanager 라우팅:
   - matcher: team=labs
   - 채널: Slack webhook <URL> + Telegram chat_id <ID>

본 프로젝트 측 책임:
- /metrics 엔드포인트 노출 (prometheus-fastapi-instrumentator 사용)
- ax_* 메트릭 명명 규칙 준수

연락처: <project owner>
예상 SLA: 2주 (룰 검증 시간 포함)
참조: BUILD_ORDER.md 작업 1-7-A, OBSERVABILITY.md §2.2~2.5
```

### 1.5 OpenTelemetry Collector
- [ ] 티켓 발행
- [ ] OTLP 엔드포인트 + 인증 토큰 수령
- [ ] 첫 trace 도착 확인 (Tempo/Jaeger UI)

**티켓 템플릿**
```
제목: [labs] OpenTelemetry Collector OTLP 엔드포인트 + 인증 토큰 발급

요청 사항:
- OTLP/HTTP 엔드포인트 (예: https://otel-collector.internal.example.com:4318)
- 인증 토큰 발급 (`Authorization: Bearer <token>`)
- 본 프로젝트 식별자:
  · service.namespace=labs
  · service.name=ax-llm-eval-workflow-backend
- 백엔드(Tempo/Jaeger) 라우팅 정책 안내
- Sampling 권장값:
  · dev: 0.0 (off)
  · staging: 1.0
  · prod: 0.1 (parentbased_traceidratio)

본 프로젝트 측 책임:
- OTel SDK 통합 (opentelemetry-distro, instrumentation-fastapi/httpx/redis)
- traceparent 헤더 propagation (LiteLLM/Langfuse 호출에 전파)

연락처: <project owner>
예상 SLA: 1주
참조: BUILD_ORDER.md 작업 1-7-B
```

### 1.6 Loki 라벨 규약 + 수집기
- [ ] 티켓 발행
- [ ] 라벨 규약 + 수집기 엔드포인트 합의
- [ ] 첫 JSON 로그 도착 확인 (Loki 라벨 필터)

**티켓 템플릿**
```
제목: [labs] Loki 로그 수집 — 라벨 규약 + 수집기(Promtail/Vector) 합의

요청 사항:
- 라벨 규약:
  · service=ax-llm-eval-workflow-backend
  · env={dev,staging,prod}
  · component={api,worker,sandbox}
- 수집기 설정:
  · 본 프로젝트 컨테이너 stdout pickup (Promtail/Vector)
  · 운영 보존 정책 30일 권장
- PII 미포함 정책 합의 (CI 단계에서 본 프로젝트가 검증)

본 프로젝트 측 책임:
- 모든 로그 stdout JSON 출력 (필수 필드: timestamp, level, event,
  request_id, trace_id, experiment_id)
- 프롬프트/모델 출력 원본 로그 금지 (CLAUDE.md 보안 규칙)

연락처: <project owner>
예상 SLA: 1주
참조: BUILD_ORDER.md 작업 1-7-C
```

### 1.7 Redis 정책 결정 (임차 vs 자체)
- [ ] Option 1 (사내 Redis 임차) 가능 여부 확인
- [ ] DB 번호 + 비밀번호 + 엔드포인트 수령 (Option 1 시)
- [ ] 또는 자체 컨테이너 운영 결정 (Option 2)

**티켓 템플릿**
```
제목: [labs] Redis 정책 결정 — 사내 공용 Redis 임차 또는 자체 운영 합의

배경: 본 프로젝트는 실험 상태/진행률을 Redis에 저장 (TTL 24h ~ 30d).
사내 공용 Redis가 있다면 별도 DB 임차가 운영 부담이 적음.

요청 (Option 1, 권장):
- 사내 공용 Redis의 별도 DB 임차 (예: LABS_REDIS_DB=1)
- DB 번호 합의 (충돌 회피)
- 비밀번호 + 엔드포인트 (예: redis.internal.example.com:6379)
- 키 prefix: ax:* 로 격리
- 메모리 사용량 한도: 1GB (운영)

대안 (Option 2):
- 사내 임차 거부 시 본 프로젝트 자체 단일 컨테이너(redis:7) 운영
- 이 경우 본 프로젝트 docker-compose에 redis 서비스 추가

연락처: <project owner>
예상 SLA: 3일
참조: BUILD_ORDER.md 「사내 공용 인프라 의존」 절
```

### 1.8 Auth (JWT) — JWKS + 클레임 매핑
- [ ] 티켓 발행
- [ ] JWKS URL + audience/issuer 수령
- [ ] RBAC 그룹 매핑 합의

**티켓 템플릿**
```
제목: [labs] 사내 Auth 서비스 JWT 검증 — JWKS URL + audience/issuer + RBAC 매핑

요청 사항:
- JWKS URL (예: https://auth.internal.example.com/.well-known/jwks.json)
- audience 값 (예: labs)
- issuer 값 (예: https://auth.internal.example.com)
- RBAC 클레임 위치 안내:
  · roles 배열? groups 배열? custom claim?
- 본 프로젝트 RBAC(admin / user / viewer)와 사내 그룹의 매핑 합의:
  · 예: 사내 그룹 `labs-platform-admin` → 본 프로젝트 admin
  · 예: 사내 그룹 `labs-researcher` → 본 프로젝트 user
  · 그 외 사내 인증된 사용자 → 본 프로젝트 viewer (기본값)

본 프로젝트 측 책임:
- JWKS 공개키로 JWT 서명 검증만 수행 (서명 키 보유 X)
- 401/403 응답 처리 (FRONTEND auth 컨텍스트와 정합)

연락처: <project owner>
예상 SLA: 1주
참조: BUILD_ORDER.md 작업 2-3
```

### 1.9 네트워크 / 호스트 / 컨테이너 레지스트리
- [ ] 호스트 도메인 명명 합의
- [ ] 네트워크 도달성 (모든 사내 서비스로 egress 허용)
- [ ] 외부 노출 도메인 (Frontend) + TLS 인증서
- [ ] 컨테이너 레지스트리 push 권한

**티켓 템플릿**
```
제목: [labs] 네트워크 배치 + 호스트 도메인 + 컨테이너 레지스트리 권한

요청 사항:

1. 네트워크 배치:
   - 본 프로젝트 Backend → 사내 Langfuse/LiteLLM/ClickHouse/Redis/Prometheus/OTel/Loki
     모두 도달 가능한 VPC/방화벽 규칙
   - egress 정책 명시 (개발/운영 분리)

2. 호스트 도메인 명명 (사내 표준 따름):
   - Backend internal: backend.labs.internal.example.com (Prometheus scrape 대상)
   - Frontend external: labs.example.com (사내 reverse proxy 뒤)

3. TLS 인증서:
   - 사내 reverse proxy가 TLS 종단 가정
   - 사내 인증서 발급 절차 안내

4. 컨테이너 레지스트리 push 권한:
   - registry.internal.example.com/labs/* 경로 push 권한
   - 이미지: ax-llm-eval-workflow-backend, -frontend, ax-eval-sandbox

연락처: <project owner>
예상 SLA: 1~2주
참조: BUILD_ORDER.md 작업 1-1, 1-5
```

---

## 2. 본 프로젝트에서 미리 만들 수 있는 것

사내 회신을 기다리지 않고 자체적으로 진행 가능한 작업. 회신 도착 시 즉시 결합할 수 있도록 인터페이스를 분리해 둔다.

| # | 항목 | 산출물 | 사내 회신 의존성 |
|---|---|---|---|
| 2-1 | `.env.example` 신 버전 | `docker/.env.example` (BUILD_ORDER 1-3 템플릿 그대로) | 없음 |
| 2-2 | `docker-compose.yml` 단순화 | backend / frontend / (옵션) redis만 정의 | 없음 |
| 2-3 | `docker-compose.override.yml` | 로컬 개발 (포트 노출, 볼륨 마운트, 핫리로드) | 없음 |
| 2-4 | `docker-compose.demo.yml` | seed 컨테이너 + 데모 score config | 없음 (Langfuse Key는 추후 주입) |
| 2-5 | `docker-compose.prod.yml` | 사내 reverse proxy 전제, secrets 마운트 | 없음 (호스트 도메인은 추후 주입) |
| 2-6 | ADR-011 시크릿 정책 | `docs/adr/ADR-011-secrets-management.md` | 없음 |
| 2-7 | ADR-012 ClickHouse 폴백 (조건부) | `docs/adr/ADR-012-clickhouse-fallback.md` | 1.3 회신 후 작성 |
| 2-8 | Mock fixtures (Phase 0) | MockLangfuse / LiteLLM / ClickHouse / Redis / OTel / Loki — `backend/tests/fixtures/` | 없음 (TDD 즉시 가능) |
| 2-9 | FastAPI 스캐폴드 (Phase 2) | `backend/app/` 구조 + config + 헬스체크 + observability 통합 | Mock fixture만 있으면 시작 가능 |
| 2-10 | sandbox 이미지 빌드 | `docker/eval-sandbox/` (이미 있음, 검증만) | 1.9의 컨테이너 레지스트리 권한은 push 시점에만 필요 |
| 2-11 | 모델 카탈로그 정합 점검 | `frontend/src/lib/mock/data.ts`의 모델 ID와 1.2 등록 요청 모델 ID 일치 검증 | 없음 |

**핵심**: Mock fixture 기반 TDD로 Phase 0~5 대부분이 사내 의존성 없이 개발 가능. 사내 회신은 Phase 1 인프라 검증과 Phase 6 분석 쿼리에서 비로소 필요.

---

## 3. 합의·확정해야 할 결정 사항

| 결정 | 옵션 | 권고 | 결정 여부 |
|---|---|---|---|
| Redis 운영 | ① 사내 공용 Redis 임차 / ② 자체 컨테이너 | ① (운영 부담 최소) | [ ] |
| ClickHouse 접근 | ① readonly 계정 / ② Langfuse public API 폴백 | ① 우선 시도, 거부 시 ② | [ ] |
| 환경 분리 | dev/staging/prod 별도 Langfuse 프로젝트? | 별도 프로젝트 권장 (label/key 격리) | [ ] |
| OTel sampling | 환경별 비율 | dev=0.0 / staging=1.0 / prod=0.1 | [ ] |
| Loki 보존 정책 | 7d / 30d / 90d | 운영 30d, 스테이징 14d, 개발 7d | [ ] |
| 컨테이너 레지스트리 | Harbor / GHCR / 내부 표준 | 사내팀 표준 따름 | [ ] |
| Secret store | Vault / AWS SM / 환경변수만 | 사내팀 표준 따름 (운영은 Vault 권장) | [ ] |
| Frontend 외부 도메인 | TBD | 사내 명명 규칙 따름 | [ ] |
| 인증 방식 | mTLS / VPN / IP allowlist | 사내 보안 정책 따름 | [ ] |
| RBAC 매핑 | 사내 그룹 ↔ admin/user/viewer | 사내 그룹명 합의 후 매핑 (1.8) | [ ] |

---

## 4. Critical Path — 가장 먼저 받아야 할 답

순서가 있는 의존성 기준 우선순위. 위에서 아래로 회신을 받아야 본 프로젝트 구현이 막히지 않는다.

| 우선순위 | 항목 | 차단되는 작업 |
|---|---|---|
| 🔴 즉시 (3일 내) | Redis 임차 vs 자체 결정 (1.7) | Phase 1 docker-compose 구조 결정 |
| 🔴 즉시 (3일 내) | ClickHouse 직접 접근 vs 폴백 (1.3) | Phase 6 구현 방식 결정 (ADR-012 작성 여부) |
| 🟡 1주 내 | Langfuse Key (1.1) | Phase 4 단일 테스트 실제 호출 검증 |
| 🟡 1주 내 | LiteLLM Virtual Key (1.2) | Phase 4 LLM 호출 |
| 🟡 1주 내 | JWKS URL + 매핑 (1.8) | Phase 2 JWT 미들웨어 통합 테스트 |
| 🟢 2주 내 | Prometheus scrape PR (1.4) | Phase 1 마일스톤 (Backend 가동 후 scrape 등록 가능) |
| 🟢 2주 내 | OTel/Loki (1.5, 1.6) | Phase 1 observability 통합 검증 |
| 🟢 2주 내 | 네트워크 / 호스트 / 레지스트리 (1.9) | Phase 1 운영 배포 |

---

## 5. 검증 방법 (점진적 3단계)

### Stage A — 사내 회신 받기 전
```bash
# Mock fixture 기반 단위/통합 테스트
cd backend && pytest tests/ -v
cd frontend && npm run test
```

### Stage B — 사내 회신 일부 도착 후 (smoke test)
```bash
# 각 서비스 엔드포인트 단독 도달성 확인 — Backend 컨테이너 네트워크에서 실행
curl -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" "$LANGFUSE_HOST/api/public/health"
curl -H "Authorization: Bearer $LITELLM_VIRTUAL_KEY" "$LITELLM_BASE_URL/health"
clickhouse-client --secure --user "$CLICKHOUSE_READONLY_USER" \
  --password "$CLICKHOUSE_READONLY_PASSWORD" --query "SELECT 1"
redis-cli -u "$REDIS_URL" PING
curl "$PROMETHEUS_QUERY_URL/-/ready"
curl "$OTEL_EXPORTER_OTLP_ENDPOINT/v1/traces"  # 405 Method Not Allowed 도달성 OK
curl "$LOKI_URL/ready"
curl "$AUTH_JWKS_URL"  # JWKS JSON 응답 확인
```

### Stage C — 전체 회신 후 (종합 검증)
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

# 4. 사내 Tempo/Jaeger UI에서 첫 trace 도착 확인 (브라우저)
# service.namespace=labs, service.name=ax-llm-eval-workflow-backend 필터

# 5. 사내 Loki에서 라벨 필터로 첫 JSON 로그 확인
# {service="ax-llm-eval-workflow-backend"} 필터

# 6. Score config 등록 결과 (Backend 부팅 로그)
docker compose logs backend | grep score_config_registered

# 7. Sandbox 이미지 사내 레지스트리 push
docker tag ax-eval-sandbox:1.0.0 registry.internal.example.com/labs/ax-eval-sandbox:1.0.0
docker push registry.internal.example.com/labs/ax-eval-sandbox:1.0.0
```

---

## 6. Phase 1 Done Definition

다음 7가지가 모두 충족되어야 Phase 1 마일스톤 도달로 간주한다.

- [ ] **사내 의존성 합의 완료**: 9개 티켓 모두 회신 + `.env.production` 주입
- [ ] **자체 compose 가동**: backend / frontend / (옵션) redis healthy
- [ ] **종합 헬스체크 OK**: `/api/v1/health` 응답에서 7개 사내 의존 서비스 모두 ok
- [ ] **사내 Prometheus scrape UP**: 본 프로젝트 job(`ax-llm-eval-workflow-backend`)이 `up` 상태
- [ ] **OTel 첫 trace 도착**: 사내 Tempo/Jaeger UI에서 service.namespace=labs 필터로 확인
- [ ] **Loki 첫 JSON 로그 도착**: 사내 Loki에서 service 라벨 필터로 확인
- [ ] **Score config idempotent 등록**: Backend 부팅 로그에 `score_config_registered` 기록 + 재기동 시 skip 확인

---

## 7. 티켓 진행 상태 보드

> 실시간 추적용. 회신/완료 시 상태 갱신.

| # | 티켓 | 발행일 | 담당자 (사내) | 상태 | 회신일 | 비고 |
|---|---|---|---|---|---|---|
| 1.1 | Langfuse Key | _ | _ | ⬜ 미발행 | _ | _ |
| 1.2 | LiteLLM 모델 등록 + Virtual Key | _ | _ | ⬜ 미발행 | _ | _ |
| 1.3 | ClickHouse readonly | _ | _ | ⬜ 미발행 | _ | Option 1 우선 시도 |
| 1.4 | Prometheus scrape + 룰 PR | _ | _ | ⬜ 미발행 | _ | _ |
| 1.5 | OTel Collector | _ | _ | ⬜ 미발행 | _ | _ |
| 1.6 | Loki 라벨 + 수집기 | _ | _ | ⬜ 미발행 | _ | _ |
| 1.7 | Redis 정책 | _ | _ | ⬜ 미발행 | _ | Critical Path |
| 1.8 | JWKS + RBAC 매핑 | _ | _ | ⬜ 미발행 | _ | _ |
| 1.9 | 네트워크 + 레지스트리 | _ | _ | ⬜ 미발행 | _ | _ |

**상태 표기**: ⬜ 미발행 / 🟡 발행됨 (대기) / 🟢 회신 수령 / ✅ 본 프로젝트 통합 완료

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
| 2026-04-27 | 초안 작성 | _(TBD)_ |
