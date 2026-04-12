# 관찰성 & 운영 (Observability & Operations)

프로덕션 운영에 필요한 로깅/메트릭/트레이싱/헬스체크/인시던트 대응 규약.

---

## 1. 구조화 로깅

### 1.1 로그 스키마 (JSON)

모든 로그는 JSON 형식 (`LOG_JSON_FORMAT=true`). 필수 필드:

```json
{
    "timestamp": "2026-04-12T10:23:45.123Z",
    "level": "INFO | WARN | ERROR | DEBUG",
    "service": "ax-llm-eval-backend",
    "version": "1.0.0",
    "message": "...",
    "request_id": "uuid",
    "trace_id": "Langfuse trace_id",
    "user_id": "JWT sub",
    "role": "admin | user | viewer",
    "project_id": "...",
    "experiment_id": "...",
    "run_name": "...",
    "endpoint": "POST /api/v1/experiments",
    "status_code": 200,
    "duration_ms": 123,
    "extra": { ... }
}
```

### 1.2 PII 필터링

- `message` 및 `extra` 필드에서 다음 패턴 자동 마스킹:
  - `sk-[A-Za-z0-9]{32,}` (OpenAI API key)
  - `AKIA[0-9A-Z]{16}` (AWS key)
  - PEM 헤더 (`-----BEGIN`)
  - JWT 토큰 (`eyJ[\w-]+\.[\w-]+\.[\w-]+`)
  - 이메일, 전화번호 (옵션, `LOG_REDACT_PII=true`)
- **프롬프트/모델 출력 원본은 로그 금지** (CLAUDE.md 원칙). `trace_id`만 기록하고 상세는 Langfuse UI에서 조회.

### 1.3 필수 로깅 이벤트

| 이벤트 | level | 필수 필드 |
|--------|-------|---------|
| 실험 생성/완료/실패/취소 | INFO | experiment_id, status, total_items, total_cost_usd |
| 인증 실패 | WARN | user_id(시도값), source_ip, reason |
| Rate limit 초과 | WARN | user_id, endpoint, limit |
| Budget 초과 | ERROR | project_id, daily_cost, limit |
| Evaluator 제출/승인/반려 | INFO | submission_id, actor_user_id, status |
| 샌드박스 실행 실패 | ERROR | experiment_id, error_code, container_id |
| Redis/ClickHouse/Langfuse 연결 실패 | ERROR | service, retry_count |
| Lua script STATE_CONFLICT | WARN | experiment_id, current_status, target_status |

---

## 2. 메트릭 (Prometheus)

### 2.1 엔드포인트

`GET /metrics` — Prometheus scrape용. 인증 불필요 (내부 네트워크에서만 접근).

### 2.2 핵심 메트릭

**API 메트릭**:
- `ax_http_requests_total{method, endpoint, status}` (counter)
- `ax_http_request_duration_seconds{method, endpoint}` (histogram)
- `ax_http_requests_in_flight` (gauge)

**비즈니스 메트릭**:
- `ax_experiments_created_total{project_id}` (counter)
- `ax_experiments_running{project_id}` (gauge)
- `ax_llm_cost_usd_total{project_id, model}` (counter)
- `ax_llm_tokens_total{project_id, model, direction}` (counter, direction=input/output)
- `ax_evaluator_executions_total{type, status}` (counter, type=built_in/llm_judge/custom_code/approved)
- `ax_dataset_uploads_total{status}` (counter)

**인프라 메트릭**:
- `ax_redis_operations_total{op, status}` (counter)
- `ax_redis_operation_duration_seconds{op}` (histogram)
- `ax_clickhouse_query_duration_seconds{query_type}` (histogram)
- `ax_langfuse_api_calls_total{method, status}` (counter)
- `ax_sse_connections{endpoint}` (gauge)
- `ax_sandbox_containers_active` (gauge)
- `ax_sandbox_container_duration_seconds` (histogram)

**보안 메트릭**:
- `ax_auth_failures_total{reason}` (counter, reason=expired/invalid_signature/missing)
- `ax_rate_limit_hits_total{endpoint, user_id}` (counter)
- `ax_budget_exceeded_total{project_id}` (counter)
- `ax_sandbox_violations_total{pattern}` (counter)

### 2.3 알림 임계치 (Prometheus Alerting Rules)

| 알림 | 조건 | 심각도 |
|------|------|-------|
| HighErrorRate | `rate(ax_http_requests_total{status=~"5.."}[5m]) > 0.05` | critical |
| LLMCostBudgetWarning | `ax_llm_cost_usd_total > 0.8 * daily_limit` | warning |
| SandboxPoolExhausted | `ax_sandbox_containers_active / max >= 0.9` | warning |
| RedisDown | `up{job="redis"} == 0` | critical |
| ClickHouseSlow | `ax_clickhouse_query_duration_seconds{quantile="0.99"} > 10` | warning |
| AuthFailureSpike | `rate(ax_auth_failures_total[5m]) > 10` | critical |
| SandboxViolationSpike | `rate(ax_sandbox_violations_total[15m]) > 1` | critical |

---

## 3. 분산 트레이싱

### 3.1 Trace Context 전파

- **헤더**: W3C Trace Context (`traceparent`, `tracestate`)
- 체인: `Frontend → Backend → LiteLLM → (Langfuse)`
- Backend는 수신된 `traceparent`를 Langfuse trace의 `metadata.trace_parent`로 저장 → Langfuse UI에서 외부 추적 상관 가능

### 3.2 OpenTelemetry (권장)

- `opentelemetry-instrumentation-fastapi` (자동 span 생성)
- `opentelemetry-instrumentation-httpx` (LiteLLM/Langfuse 호출 추적)
- `opentelemetry-instrumentation-redis`
- Span 속성: `user_id`, `project_id`, `experiment_id`, `trace_id`(Langfuse)
- Exporter: OTLP → Tempo/Jaeger/Datadog APM

---

## 4. 헬스체크

### 4.1 Liveness vs Readiness

| 엔드포인트 | 목적 | 검사 내용 |
|-----------|------|---------|
| `GET /api/v1/health/live` | Liveness probe | 프로세스 살아있음만 확인 (즉시 200) |
| `GET /api/v1/health/ready` | Readiness probe | Redis/Langfuse/LiteLLM/ClickHouse 연결 검증 (deep check) |
| `GET /api/v1/health` | 사용자 대상 상태 | Ready와 동일 + 서비스별 상태 반환 |

### 4.2 Deep check 규약

- 각 의존성 ping은 **최대 2초 타임아웃**
- 실패 시 readiness 503 반환하지만 liveness는 200 유지 (K8s가 재시작 대신 traffic 차단)
- `/api/v1/health/ready?details=true` 옵션으로 상세 진단 반환 (admin 권한)

### 4.3 K8s Probe 설정 예시

```yaml
livenessProbe:
  httpGet:
    path: /api/v1/health/live
    port: 8000
  periodSeconds: 10
  failureThreshold: 3
readinessProbe:
  httpGet:
    path: /api/v1/health/ready
    port: 8000
  periodSeconds: 5
  failureThreshold: 2
```

---

## 5. 에러 리포팅 (Sentry)

### 5.1 설정

- `SENTRY_DSN` 환경변수 (프로덕션 필수)
- `traces_sample_rate=0.1` (10% 샘플링)
- `environment=APP_ENV`
- `release=git commit hash`

### 5.2 필터링

- 4xx 에러는 Sentry 전송 안 함 (비즈니스 로직 정상 동작)
- 5xx만 전송 + 민감 필드 `before_send` 훅에서 제거
- `sentry-sdk` PII 자동 수집 비활성화 (`send_default_pii=False`)

### 5.3 Frontend Sentry

- `@sentry/nextjs` 통합
- 에러 경계(React ErrorBoundary) 크래시 자동 보고
- Breadcrumb: API 호출, 라우팅, 사용자 액션

---

## 6. 백업 & 복구

### 6.1 Redis 지속성

- **AOF 활성화 (appendfsync everysec)** — 실험 상태 유실 최소화
- 일일 RDB 스냅샷 + S3 업로드 (30일 보관)
- RPO (Recovery Point Objective): 1초
- RTO (Recovery Time Objective): 5분

### 6.2 Langfuse 데이터

- Langfuse 인프라 팀 담당 (Labs의 책임 범위 외)
- Labs는 Langfuse trace metadata 손실 시 **Redis의 실험 상태로 부분 복구 가능**
- `config_snapshot`은 Redis에만 있으므로 Redis 백업이 곧 실험 복구 수단

### 6.3 DR (Disaster Recovery) 절차

1. 새 Redis 인스턴스 스핀업
2. 최신 RDB 파일 복원
3. Backend 재시작 → Auto-reconnect
4. 진행 중이던 실험은 상태 조회로 UI에서 재개 유도 (자동 재개 아님)
5. Langfuse 연결 확인 후 trace 기록 재개

---

## 7. 배포

### 7.1 전략

- **Zero-downtime**: Rolling update (K8s Deployment `maxUnavailable=0, maxSurge=1`)
- **Blue/Green**: LiteLLM Proxy 업그레이드 시 권장 (모델 설정 변경 검증)
- **Canary**: 신기능 rollout 시 — 10% → 50% → 100% 단계

### 7.2 마이그레이션

- Redis 스키마 변경 시 `ax:` 버전 접두사 사용 (`ax:v2:experiment:{id}` 등)
- Backward-compatible 2 버전 유지 기간 (1주일)
- 배포 전 dry-run으로 키 마이그레이션 검증

### 7.3 롤백 절차

1. K8s Deployment `kubectl rollout undo`
2. Redis 스키마가 변경된 경우 롤백 스크립트 실행
3. LiteLLM 설정 파일은 git revert + 재배포
4. Langfuse 데이터는 롤백 대상 아님 (append-only)

---

## 8. 용량 산정

### 8.1 예상 규모 (초기 목표)

- 동시 사용자: 50명
- 일일 활성 사용자: 200명
- 일일 실험: 500건
- 일일 단일 테스트: 5000건
- 프로젝트 수: 20개

### 8.2 리소스 산정

| 리소스 | 산정 |
|--------|------|
| Redis 메모리 | 실험 상태(100KB × 200 실험) + 알림(30일 × 200 사용자 × 100 알림 × 1KB) = **~700MB**. 1GB 인스턴스 권장 |
| ClickHouse 쿼리 | 일일 5000 쿼리, 평균 1초 → 약 1.5시간 CPU time. 4 vCPU 인스턴스로 충분 |
| Backend 인스턴스 | 2 replica (HA) × 4 vCPU / 4GB RAM |
| LiteLLM Proxy | 2 replica × 2 vCPU / 2GB RAM |
| 샌드박스 컨테이너 | 호스트당 최대 10개 × 128MB = 1.28GB. Backend 호스트 내 실행 |

### 8.3 스케일링 트리거

- CPU > 70% 지속 5분 → HPA 스케일 아웃
- Redis 메모리 > 80% → 알림 TTL 단축 검토
- ClickHouse 쿼리 지연 p99 > 10초 → 인덱스 추가 또는 집계 테이블 도입

---

## 9. SLA/SLO/SLI

### 9.1 SLO (Service Level Objectives)

| 지표 | 목표 |
|------|------|
| Uptime (API) | 99.5% (월 최대 3.6시간 다운) |
| API 응답 지연 p99 | < 500ms (실험 생성/상태 조회) |
| SSE 연결 성공률 | > 99% |
| 실험 실행 성공률 | > 95% (인프라 에러만, 사용자 실패 제외) |
| 샌드박스 실행 타임아웃률 | < 5% |

### 9.2 SLI 측정

- Uptime: `up{job="ax-backend"}` 평균
- API 지연: `histogram_quantile(0.99, ax_http_request_duration_seconds)`
- SSE 성공률: `1 - (ax_sse_errors_total / ax_sse_connections_total)`

### 9.3 에러 버짓

- 99.5% SLO → 0.5% 에러 허용 → 월 3.6시간
- 에러 버짓 50% 소진 시 신기능 배포 중단 + 안정성 작업 우선

---

## 10. 인시던트 대응 (Runbook)

### 10.1 공통 트리아지

1. Sentry/Prometheus 알림 수신
2. `/api/v1/health/ready?details=true` 로 서비스별 상태 확인
3. Grafana 대시보드에서 트래픽/에러율/지연 확인
4. 최근 배포 여부 확인 (`kubectl rollout history`)
5. 필요 시 롤백 (섹션 7.3)

### 10.2 시나리오별 Runbook

**Redis 연결 실패**:
- 실험 생성/조회 API 503 반환
- 조치: Redis 인스턴스 상태 확인 → 재시작 or 페일오버 → Backend 재연결 확인
- 영향: 실행 중이던 실험은 Lua script 실패로 STATE_CONFLICT, 사용자 재시도 필요

**LLM Provider 장애**:
- LiteLLM Proxy에서 fallback 모델로 자동 전환 (설정 기반)
- Fallback 없으면 `LLM_ERROR` 반환
- 조치: 대체 모델 활성화, 사용자 공지

**샌드박스 대량 장애 (Docker 데몬 문제)**:
- 모든 `custom_code`/`approved` evaluator 실행 실패
- 조치: 해당 evaluator 타입을 일시적으로 비활성화, 실험은 다른 evaluator로만 진행
- Docker 데몬 재시작 후 복구

**ClickHouse 쿼리 폭증**:
- 분석 API 지연 급증
- 조치: 캐시 TTL 임시 증가, 복잡 쿼리 rate limit 강화, 집계 테이블 마이그레이션

### 10.3 On-call

- 주간 로테이션 (팀 내 지정)
- 1차 대응 15분 내 ack, 1시간 내 mitigation
- escalation: 30분 내 해결 불가 시 팀 리드

---

## 11. 시크릿 로테이션

### 11.1 LITELLM_MASTER_KEY (90일 주기)

1. 새 키 생성, LiteLLM config에 기존 키와 **병행 활성화** (전이 기간 24시간)
2. Backend `LITELLM_MASTER_KEY` 환경변수 업데이트 후 rolling restart
3. 이전 키 제거
4. Secret Manager 버전 업데이트 + 감사 로그 기록

### 11.2 Langfuse 프로젝트 API Key

- Langfuse UI에서 새 키 생성 → `PROJECTS_CONFIG` 업데이트 → `POST /admin/reload-config`
- 이전 키는 1주일 유지 후 revoke

### 11.3 JWT 서명 키 (Auth 서비스 담당)

- Auth 서비스가 JWKS에서 새 키로 회전
- Backend는 JWKS 캐시 만료(5분) 후 자동 반영
- 이전 키로 서명된 유효한 토큰은 exp까지 사용 가능 (grace period)

---

## 12. 개발/프로덕션 차이 강제

### 12.1 프로덕션 필수 체크리스트

- [ ] `APP_ENV=production`
- [ ] `/docs`, `/redoc`, `/openapi.json` 비활성화 (`FastAPI(docs_url=None)`)
- [ ] CORS 와일드카드 없음, 화이트리스트 도메인만
- [ ] Sentry DSN 설정
- [ ] Source maps 업로드 (Sentry에만)
- [ ] HTTPS 강제 (HSTS 헤더)
- [ ] Rate limiting 활성화
- [ ] Budget 한도 설정
- [ ] Audit log 활성화
- [ ] PII redaction 활성화
- [ ] Redis AOF 활성화

### 12.2 런타임 검증

Backend 시작 시 `APP_ENV=production`이면 위 체크리스트를 자동 검증. 실패 항목이 있으면 시작 거부.

---

## 13. 컴플라이언스 / 감사

### 13.1 GDPR / 개인정보 대응

- PII 삭제권: `DELETE /experiments/{id}` + Langfuse trace metadata cascade
- 데이터 최소화: 로그에 원본 프롬프트/출력 금지
- 보관 기간: 감사 로그 1년, 알림 30일, Redis 실험 상태 24시간

### 13.2 내부 감사

- 월 1회 감사 로그 분석:
  - Admin action 로그 검토
  - 권한 상승/RBAC 위반 시도
  - 비정상 사용 패턴 (단일 사용자 대량 실험)
- 연 1회 샌드박스 보안 감사 (외부 펜테스트)
