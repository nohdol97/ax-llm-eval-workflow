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
    "request_id": "uuid (서버 생성, 요청당 1개)",
    "correlation_id": "클라이언트 X-Correlation-Id 헤더 또는 request_id로 폴백",
    "trace_id": "W3C traceparent의 trace-id (Langfuse trace_id와 동일)",
    "span_id": "현재 span id (OpenTelemetry)",
    "user_id_hash": "sha256(JWT sub)[:16] — 원본 sub 로그 금지",
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

### 1.2 PII 마스킹 규칙

**적용 범위**: `message`, `extra`, 예외 스택트레이스 문자열 — 로그 직렬화 직전 `SecretsFilter`에서 정규식 기반 치환 (`***REDACTED***`).

| 패턴 | 정규식 | 치환 |
|------|--------|------|
| OpenAI API key | `sk-[A-Za-z0-9]{20,}` | `sk-***` |
| Anthropic API key | `sk-ant-[A-Za-z0-9_-]{20,}` | `sk-ant-***` |
| AWS Access key | `AKIA[0-9A-Z]{16}` | `AKIA***` |
| PEM 블록 | `-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----` | `***PEM***` |
| JWT | `eyJ[\w-]+\.[\w-]+\.[\w-]+` | `***JWT***` |
| 이메일 | `[\w.+-]+@[\w-]+\.[\w.-]+` | `***@***` (`LOG_REDACT_PII=true` 시) |
| 전화번호 (KR) | `01[016-9][-\s]?\d{3,4}[-\s]?\d{4}` | `***PHONE***` |
| 주민등록번호 | `\d{6}[-\s]?[1-4]\d{6}` | `***RRN***` |

**금지 사항**:
- 프롬프트 원본, 모델 출력 원본, 데이터셋 row 내용 로그 기록 금지 — `trace_id`로 Langfuse UI 조회만 허용
- JWT `sub` 원본 기록 금지 — `user_id_hash`만 사용
- 요청/응답 본문 자동 dump 금지 (FastAPI exception handler에서 body 로그 off)

**검증**: 단위 테스트에서 위 패턴별 샘플 입력이 로그에 원본으로 남지 않는지 확인 (필수).

### 1.3 로그 레벨 정책

각 레벨의 사용 기준을 강제한다 (코드 리뷰 체크 항목).

| 레벨 | 사용 기준 | 액션 요구 | 예시 |
|------|----------|----------|------|
| **DEBUG** | 개발/디버깅 한정. 프로덕션에서 기본 비활성 (`LOG_LEVEL=INFO`) | 없음 | 캐시 hit/miss, 내부 상태 전이, SQL 바인딩 값 |
| **INFO** | 정상 비즈니스 이벤트, 상태 변경, 라이프사이클 | 없음 (감사/추적용) | 실험 생성·완료, 로그인 성공, 배포 시작/완료, 설정 reload |
| **WARN** | 자동 복구 가능하거나 사용자 잘못이지만 모니터링이 필요한 이상 | 트렌드 모니터, 임계 초과 시 알림 | 인증 실패(개별), rate limit 초과, 재시도 후 성공, deprecated API 호출, Lua STATE_CONFLICT |
| **ERROR** | 자동 복구 불가, 사용자 요청/내부 작업 실패. 스택트레이스 필수 | Sentry 전송, on-call 알림 후보 | 5xx 응답, Redis/ClickHouse 연결 실패, Budget 초과, 샌드박스 실행 실패, 미처리 예외 |
| **CRITICAL** | 서비스 전체 또는 데이터 무결성 위협. 즉시 page | PagerDuty 즉시 호출 | 데이터 손상 감지, AOF 쓰기 실패, JWKS 검증 불능, 시크릿 유출 의심 |

**원칙**:
- 같은 사건의 중복 로그 금지 — 발생 지점 1곳에서만 기록 (재발행 시 INFO로 강등)
- ERROR 이상은 반드시 `error_code`(영문 SCREAMING_SNAKE) + `trace_id` 포함
- 4xx는 원칙적으로 INFO/WARN (사용자 잘못), 5xx만 ERROR
- 로그 레벨 변경(예: WARN→ERROR)은 PR 리뷰 필수 — 알림 노이즈/누락 영향 평가
- 리트라이 루프 안에서는 마지막 시도 실패만 ERROR, 중간 시도는 WARN

### 1.4 필수 로깅 이벤트

| 이벤트 | level | 필수 필드 |
|--------|-------|---------|
| 실험 생성/완료/실패/취소 | INFO | experiment_id, status, total_items, total_cost_usd |
| 인증 실패 | WARN | user_id(시도값), source_ip, reason |
| Rate limit 초과 | WARN | user_id, endpoint, limit |
| Budget 초과 | ERROR | project_id, daily_cost, limit |
| Evaluator 제출/승인/반려 | INFO | submission_id, actor_user_id, status |
| Evaluator deprecated 전환 | WARN | submission_id, actor_user_id(admin), reason, subscriber_count — `evaluator_deprecated` 알림이 소유자/구독자에게 발송되며 Slack `#labs-alerts` 라우팅 (FEATURES §10.5, API_DESIGN §14.3) |
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
- `ax_experiments_completed_total{project_id, status}` (counter, status=success/failed/cancelled)
- `ax_experiments_running{project_id}` (gauge)
- `ax_experiments_queue_depth{project_id}` (gauge, 대기 중 실험 수)
- `ax_active_users{window}` (gauge, window=5m/1h/24h — 고유 user_id_hash 카운트, recording rule로 산출)
- `ax_dau` (gauge, 일일 고유 활성 사용자 — 자정 기준 24h 윈도우)
- `ax_wau` (gauge, 주간 고유 활성 사용자 — 7d 롤링 윈도우, FEATURES §15.3 Adoption KPI)
- `ax_mau` (gauge, 30일 윈도우, 일 1회 갱신)
- `ax_wvpi_total{project_id}` (counter, 주간 검증된 프롬프트 개선 — production 라벨 승격 시 +1, FEATURES §15.3 North Star)
- `ax_experiment_cycle_duration_seconds{project_id}` (histogram, 가설→검증 사이클 시간 — 실험 생성→완료, p50 SLO <4h)
- `ax_evaluator_submissions_total{status}` (counter, status=submitted/approved/rejected)
- `ax_evaluator_approval_duration_seconds` (histogram, 제출→승인/반려 소요 시간, p95 SLO <24h, buckets=`[1800, 3600, 7200, 14400, 43200, 86400, 172800, 259200, 604800]` — 30m/1h/2h/4h/12h/24h/48h/72h/7d, SLO 24h 경계 포함)
- `ax_regression_detection_total{outcome}` (counter, outcome=detected/missed/false_positive — 배포 전 회귀 발견율 산출용)
- `ax_unauthorized_evaluator_attempts_total` (counter, 미승인 코드 실험 진입 시도 — KPI 목표 0)
- `ax_dataset_uploads_total{status}` (counter)
- `ax_evaluator_executions_total{type, status}` (counter, type=built_in/llm_judge/custom_code/approved)
- `ax_llm_first_token_latency_seconds{model, provider}` (histogram, 단일 테스트 첫 토큰 지연 — FEATURES §12.1 NFR p95<1.5s)
- `ax_experiment_batch_duration_seconds{project_id}` (histogram, 배치 실험 처리량 — FEATURES §12.1 NFR p95<10분, 100×3 run 기준)
- `ax_experiments_in_progress{project_id}` (gauge, 워크스페이스당 동시 실험 수 — FEATURES §12.1 NFR ≤5, `ax_experiments_running`과 별개로 사용자 관점 카운트)
- `ax_experiment_persistence_failures_total{stage}` (counter, Redis→Langfuse 영속화 실패 — FEATURES §12.3 NFR 손실 0건)
- `ax_experiment_resume_total{outcome}` (counter, outcome=success/failed — Backend 재시작 후 체크포인트 재개 성공률, FEATURES §12.3 NFR ≥99%)
- `ax_litellm_errors_total{kind}` (counter, kind=timeout/rate_limit/auth/upstream_5xx/circuit_open — FEATURES §12.3 NFR 사용자 인지 가능 에러율 100%)
- `ax_langfuse_persistence_success_ratio` (gauge, recording rule 산출, 배치 실험 → Langfuse trace/score/dataset run 영속화 성공 비율 — FEATURES §12.4 NFR)

**비용/사용량 메트릭**:
- `ax_llm_cost_usd_total{project_id, model, provider}` (counter, LiteLLM 응답 usage 기반)
- `ax_llm_cost_usd_per_experiment{project_id}` (histogram, 실험당 USD 분포)
- `ax_llm_tokens_total{project_id, model, direction}` (counter, direction=input/output/cached)
- `ax_llm_budget_utilization_ratio{project_id}` (gauge, 일일 사용액/한도, 0-1+, recording rule)
- `ax_llm_unit_cost_usd{model, direction}` (gauge, 1K 토큰당 단가 — config reload 시 갱신, 사후 검증/이상 탐지용)
- `ax_llm_request_cost_usd_bucket{project_id, model}` (histogram, 단일 호출 비용 분포)
- `ax_clickhouse_query_cost_units_total{query_type}` (counter, 분석 쿼리 비용 추적)
- `ax_attachment_bytes_total{project_id, storage_class}` (counter, Langfuse Media/S3 attachment 업로드 바이트 — `storage_class=standard|infrequent|archive` 화이트리스트, LANGFUSE.md §5.5의 attachment 비용 추적용, `cost_details`와 분리)

**인프라 메트릭**:
- `ax_redis_operations_total{op, status}` (counter)
- `ax_redis_operation_duration_seconds{op}` (histogram)
- `ax_clickhouse_query_duration_seconds{query_type}` (histogram)
- `ax_langfuse_api_calls_total{method, status}` (counter)
- `ax_sse_connections_active{endpoint}` (gauge, 현재 연결 수)
- `ax_sse_connections_total{endpoint}` (counter, 누적 연결 시도)
- `ax_sse_errors_total{endpoint, reason}` (counter)
- `ax_sandbox_containers_active` (gauge)
- `ax_sandbox_container_duration_seconds` (histogram)

**보안 메트릭**:
- `ax_auth_failures_total{reason}` (counter, reason=expired/invalid_signature/missing)
- `ax_rate_limit_hits_total{endpoint, role}` (counter, 고카디널리티 방지를 위해 user_id는 라벨에 포함하지 않고 로그/감사 로그에서 조회)
- `ax_budget_exceeded_total{project_id}` (counter)
- `ax_sandbox_violations_total{pattern}` (counter)

### 2.3 Scrape 설정 & Cardinality 예산

**Scrape 설정** (Prometheus `scrape_config`):
- `scrape_interval: 15s`, `scrape_timeout: 10s` (timeout < interval 필수)
- `evaluation_interval: 15s` (alerting/recording rule 주기)
- `honor_labels: false`, `metrics_path: /metrics`
- 인스턴스 라벨: `job="ax-backend"`, `env`, `pod`, `version` (4개 고정)

**Cardinality 예산**: 단일 메트릭당 활성 시계열 상한 `10,000`, 서비스 전체 상한 `500,000`.
- `endpoint` 라벨은 **라우트 템플릿**(`/api/v1/experiments/{id}`)만 허용 — 실제 id 값 금지
- `project_id`/`model` 라벨은 화이트리스트 기반 (신규 값 유입 시 `__other__`로 축소)
- `user_id`, `trace_id`, `request_id`, `experiment_id`는 메트릭 라벨 금지 (로그/트레이스에서만 조회)
- 초과 감시: `prometheus_tsdb_symbol_table_size_bytes`, `count by (__name__)({__name__=~".+"})` 일일 리포트

### 2.4 Recording Rules

고빈도 대시보드·알림 쿼리는 recording rule로 사전 집계 (5s-30s 간격).

```yaml
groups:
- name: ax_http.rules
  interval: 30s
  rules:
  - record: ax:http_request_duration_seconds:p99_5m
    expr: histogram_quantile(0.99, sum by (le, category) (rate(ax_http_request_duration_seconds_bucket[5m])))
  - record: ax:http_request_duration_seconds:p95_5m
    expr: histogram_quantile(0.95, sum by (le, category) (rate(ax_http_request_duration_seconds_bucket[5m])))
  - record: ax:http_errors:ratio_5m
    expr: sum(rate(ax_http_requests_total{status=~"5.."}[5m])) / sum(rate(ax_http_requests_total[5m]))
- name: ax_llm.rules
  interval: 30s
  rules:
  - record: ax:llm_cost_usd:increase_24h
    expr: sum by (project_id) (increase(ax_llm_cost_usd_total[24h]))
  - record: ax:sandbox:duration_p99_5m
    expr: histogram_quantile(0.99, sum by (le) (rate(ax_sandbox_container_duration_seconds_bucket[5m])))
- name: ax_kpi.rules
  interval: 30s
  rules:
  - record: ax:active_users:5m
    expr: count(count by (user_id_hash) (ax_user_request_marker{window="5m"}))
  - record: ax:active_users:1h
    expr: count(count by (user_id_hash) (ax_user_request_marker{window="1h"}))
  - record: ax:dau
    expr: count(count by (user_id_hash) (ax_user_request_marker{window="24h"}))
  - record: ax:wau
    expr: count(count by (user_id_hash) (max_over_time(ax_user_request_marker[7d])))
  - record: ax:mau
    expr: count(count by (user_id_hash) (max_over_time(ax_user_request_marker[30d])))
  - record: ax:wvpi:7d
    expr: sum by (project_id) (increase(ax_wvpi_total[7d]))
  - record: ax:experiment_cycle:p50_24h
    expr: histogram_quantile(0.50, sum by (le, project_id) (rate(ax_experiment_cycle_duration_seconds_bucket[24h])))
  - record: ax:evaluator_approval:p95_24h
    expr: histogram_quantile(0.95, sum by (le) (rate(ax_evaluator_approval_duration_seconds_bucket[24h])))
  - record: ax:llm_request_cost_p95
    expr: histogram_quantile(0.95, sum by (le)(rate(ax_llm_request_cost_usd_bucket[5m])))
```

Grafana 패널과 알림 규칙은 위 `ax:*` 시계열을 우선 사용 (재계산 비용 절감).

### 2.5 알림 임계치 (Prometheus Alerting Rules)

| 알림 | 조건 | 심각도 |
|------|------|-------|
| HighErrorRate | `sum(rate(ax_http_requests_total{status=~"5.."}[5m])) / sum(rate(ax_http_requests_total[5m])) > 0.05` | critical |
| LLMCostBudgetWarning | `ax_llm_budget_utilization_ratio > 0.8` | warning |
| LLMCostBudgetCritical | `ax_llm_budget_utilization_ratio >= 1.0` | critical |
| SandboxPoolExhausted | `ax_sandbox_containers_active / max >= 0.9` | warning |
| RedisDown | `up{job="redis"} == 0` | critical |
| ClickHouseSlow | `histogram_quantile(0.99, sum by (le) (rate(ax_clickhouse_query_duration_seconds_bucket[5m]))) > 10` | warning |
| AuthFailureSpike | `rate(ax_auth_failures_total[5m]) > 10` | critical |
| SandboxViolationSpike | `rate(ax_sandbox_violations_total[15m]) > 1` | critical |

### 2.6 이상 탐지 규칙 (Anomaly Detection)

정적 임계치(2.5)로 잡히지 않는 **베이스라인 대비 이상**을 탐지한다. Prometheus `predict_linear`/요일 비교/표준편차 z-score 기반.

| 규칙 | 시그널 | 조건 | 심각도 |
|------|--------|------|-------|
| TrafficDropAnomaly | 요청 급감 | `sum(rate(ax_http_requests_total[10m]))` < 50% × 1주 전 동시간대 평균 (지난 4주) **AND** 평일/시간대 일치 | warning |
| TrafficSpikeAnomaly | 요청 급증 | 현재 요청률 > 평균 + 4σ (지난 6시간) | warning |
| LatencyRegressionAnomaly | p95 회귀 | `ax:http_request_duration_seconds:p95_5m` > 1.5 × 24h 중앙값 (지속 10분) | warning |
| CostSpikeAnomaly | 비용 급증 | `increase(ax_llm_cost_usd_total[1h])` > 3 × 7일 평균 동일 요일·시간대 | critical |
| CostBurnRateFast | 예산 burn rate | 1시간 윈도우 burn rate ≥ 14.4 (SRE multi-window: 1시간에 일일 예산의 60% 소진 → 일일 한도 단시간 초과 위험) | critical |
| TokenAnomalyPerExperiment | 실험당 토큰 이상 | `histogram_quantile(0.95, sum by (le)(rate(ax_llm_request_cost_usd_bucket[5m])))` > `avg_over_time(ax:llm_request_cost_p95[24h]) + 3 * stddev_over_time(ax:llm_request_cost_p95[24h])` (recording rule 사전 집계) | warning |
| ActiveUsersDropAnomaly | DAU 급감 | `ax_dau` < 0.5 × `avg_over_time(ax_dau[28d:7d])` (지난 4주 동일 요일 평균) | warning |
| ExperimentFailureRateAnomaly | 실패율 회귀 | `rate(ax_experiments_completed_total{status="failed"}[1h]) / rate(ax_experiments_completed_total[1h])` > 24h 평균 + 0.1 | warning |
| EvaluatorRejectSpike | 반려율 이상 | `rate(ax_evaluator_submissions_total{status="rejected"}[1h])` > 7일 동일 시간 평균 + 3σ | warning |
| QueueDepthRising | 큐 적체 | `predict_linear(ax_experiments_queue_depth[30m], 3600)` > 1000 | warning |
| SilentDependencyDegradation | Langfuse latency drift | `ax_langfuse_api_calls_total` p95 latency > 24h baseline × 2 (10분 지속) | warning |

**구현 노트**:
- 베이스라인 비교는 recording rule(`ax:baseline:*`)로 사전 계산하여 알림 평가 비용 최소화
- 신규 배포 후 30분간 anomaly 알림 자동 silence (`alertmanager` `inhibit_rule`)
- 모든 이상 탐지 알림은 critical 채널이 아닌 `#labs-anomaly` 채널로 우선 전송 → on-call 판단 후 escalation
- 이상 탐지 모델 튜닝 주기: 분기 1회 false-positive/negative 리뷰

### 2.7 Grafana 대시보드 패널

`labs-overview` 대시보드 (필수 패널):

| # | 패널 | 시각화 | 쿼리/설명 |
|---|------|--------|-----------|
| 1 | Request Rate | timeseries | `sum by (category) (rate(ax_http_requests_total[5m]))` |
| 2 | Error Rate (5xx 비율) | stat | `sum(rate(ax_http_requests_total{status=~"5.."}[5m])) / sum(rate(ax_http_requests_total[5m]))` — thresholds 0.01/0.05 |
| 3 | CRUD 지연 p50/p95/p99 | timeseries | `histogram_quantile(q, sum by (le)(rate(ax_http_request_duration_seconds_bucket{category="crud"}[5m])))` |
| 4 | 분석 API 지연 p95/p99 | timeseries | category="analytics" 동일 |
| 5 | In-flight Requests | gauge | `ax_http_requests_in_flight` |
| 6 | 실행 중 실험 | gauge | `sum(ax_experiments_running)` |
| 7 | LLM 일일 비용 | bar gauge | `sum by (project_id)(increase(ax_llm_cost_usd_total[24h]))` |
| 8 | LLM 토큰 처리량 | timeseries | `sum by (model, direction)(rate(ax_llm_tokens_total[5m]))` |
| 9 | Evaluator 성공/실패 | stacked bar | `sum by (type, status)(rate(ax_evaluator_executions_total[5m]))` |
| 10 | SSE 활성 연결 | gauge | `sum(ax_sse_connections_active)` |
| 11 | 샌드박스 풀 사용률 | gauge | `ax_sandbox_containers_active / scalar(max)` |
| 12 | 샌드박스 실행 p99 | timeseries | `histogram_quantile(0.99, sum by (le)(rate(ax_sandbox_container_duration_seconds_bucket[5m])))` |
| 13 | Redis 오퍼레이션 지연 | heatmap | `sum by (le)(rate(ax_redis_operation_duration_seconds_bucket[5m]))` |
| 14 | ClickHouse 쿼리 p99 | timeseries | `histogram_quantile(0.99, sum by (le, query_type)(rate(ax_clickhouse_query_duration_seconds_bucket[5m])))` |
| 15 | Langfuse API 에러율 | timeseries | `sum(rate(ax_langfuse_api_calls_total{status=~"[45].."}[5m])) / sum(rate(ax_langfuse_api_calls_total[5m]))` |
| 16 | 인증 실패율 | stat | `sum by (reason)(rate(ax_auth_failures_total[5m]))` |
| 17 | Rate limit 히트 | bar | `sum by (endpoint, role)(rate(ax_rate_limit_hits_total[5m]))` |
| 18 | Budget 초과 | stat | `sum by (project_id)(increase(ax_budget_exceeded_total[24h]))` |

**변수**: `$env`, `$project_id`, `$interval` (자동). 각 패널은 단위/thresholds/링크드 runbook URL(섹션 10.2) 주석 포함.

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

### 3.3 Labs 관찰성 스택 vs Langfuse — 역할 분담

| 축 | Labs 스택 (Prometheus/Loki/Sentry/OTel) | Langfuse |
|----|----------------------------------------|----------|
| **소유 데이터** | 인프라/앱 메트릭, 구조화 로그, 분산 트레이스(span) | LLM trace/generation, 프롬프트/출력 원본, 모델 evaluation score |
| **프롬프트·응답 원본** | 기록 금지 (PII 마스킹 대상) | 단일 소스 오브 트루스 |
| **비용/토큰 집계** | `ax_llm_cost_usd_total` (실시간 Prometheus 집계) | 개별 generation 단위 원천 데이터 |
| **실험 상태** | Redis (TTL 24시간) + 상태 변경 로그 | 완료 후 영속화된 trace metadata |
| **알림** | Alertmanager → PagerDuty/Slack | Langfuse 자체 알림 사용 안 함 |
| **SLO/에러 버짓** | Labs Prometheus 지표 기반 | 대상 아님 |
| **대시보드** | Grafana `labs-overview` (인프라·비즈니스) | Langfuse UI (trace 드릴다운·프롬프트 비교) |
| **보존 기간** | 메트릭 30일, 로그 14일, 감사 로그 1년 | Langfuse 인프라 팀 정책 (Labs 관여 없음) |
| **장애 시 영향** | 관찰성 상실 but 서비스 동작 | trace 기록만 실패, Labs가 큐잉/재시도 후 degrade |

**상관관계 키**: Labs 로그의 `trace_id` = Langfuse `trace.id` = W3C `traceparent` trace-id → 동일 값으로 3-way 조인.

**조사 워크플로우**:
1. Grafana 알림 → `trace_id` 복사
2. Loki에서 `{service="ax-llm-eval-backend"} |= "<trace_id>"`로 앱 로그 확인
3. Langfuse UI에서 동일 `trace_id`로 프롬프트/출력/evaluator score 확인
4. Sentry에서 `trace_id` 태그로 스택트레이스 조회

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

| 지표 | 대상 | 목표 |
|------|------|------|
| Uptime (API) | `/api/v1/health/ready` 성공률 | 99.5% (월 최대 3.6시간 다운) |
| CRUD API 지연 p95 | 실험/프로젝트/데이터셋 CRUD, 상태 조회 (ClickHouse 미사용 엔드포인트) | < 300ms |
| CRUD API 지연 p99 | 동상 | < 800ms |
| 분석 API 지연 p95 | ClickHouse 경유 대시보드/집계 엔드포인트 | < 3s |
| 분석 API 지연 p99 | 동상 | < 10s |
| SSE 연결 성공률 | `ax_sse_*` 기반 | > 99% |
| 실험 실행 성공률 | 인프라 에러만 집계 (사용자 코드/LLM 4xx 제외) | > 95% |
| 샌드박스 실행 타임아웃률 | `ax_sandbox_container_duration_seconds` 초과 비율 | < 5% |

**측정 창**: 28일 롤링 윈도우. 엔드포인트 분류는 `endpoint` 라벨의 `category=crud|analytics|sse|stream` 메타 라벨로 구분.

### 9.2 SLI 측정

- Uptime: `up{job="ax-backend"}` 평균
- API 지연: `histogram_quantile(0.99, sum by (le) (rate(ax_http_request_duration_seconds_bucket[5m])))`
- SSE 성공률: `1 - (rate(ax_sse_errors_total[5m]) / rate(ax_sse_connections_total[5m]))`

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

- 주간 로테이션 (팀 내 지정, 월요일 10:00 KST 교대)
- 1차 대응 15분 내 ack, 1시간 내 mitigation
- escalation: 30분 내 해결 불가 시 팀 리드 → 1시간 내 미해결 시 엔지니어링 매니저 → 2시간 내 미해결 시 CTO
- 알림 채널: critical → PagerDuty + Slack `#labs-oncall`, warning → Slack `#labs-alerts`, anomaly → `#labs-anomaly` (Alertmanager 라우팅)
- 보상 휴가: 야간(22:00-08:00) page 1건당 익일 0.5일 휴식

### 10.4 On-call 핸드오프 (Shift Handoff)

매주 교대 시 **이전 담당자 → 신규 담당자** 라이브 인계 미팅 30분 진행. 결과는 `handoff-YYYY-MM-DD.md`로 `docs/oncall/`에 커밋.

**핸드오프 체크리스트**:

1. **활성 인시던트**
   - [ ] 진행 중 incident 티켓 ID, 현재 상태(investigating/mitigating/monitoring), 다음 액션 오너
   - [ ] 임시 mitigation(circuit breaker open, feature flag off, 수동 scale 등) 목록과 원복 조건
2. **최근 1주 알림 요약**
   - [ ] 발생한 critical/warning 건수, 반복 발생 알림 (3회 이상) 식별
   - [ ] False positive로 판정된 알림과 튜닝 TODO
3. **에러 버짓 상태**
   - [ ] 28일 SLO 잔여 에러 버짓 % (섹션 9.3)
   - [ ] 50% 미만이면 신기능 배포 중단 권고 active 여부
4. **예정 변경**
   - [ ] 다음 주 배포 예정 (날짜·범위·롤백 담당자)
   - [ ] 진행 중 마이그레이션, 시크릿 로테이션 일정 (섹션 11)
   - [ ] 외부 의존성(Langfuse, LiteLLM, ClickHouse) 점검 공지
5. **알려진 이슈 / 임시 조치**
   - [ ] 알려진 버그와 워크어라운드, 관련 티켓 링크
   - [ ] silence 처리된 알림 목록과 만료 시각 (`amtool silence query`)
6. **접근 권한 확인**
   - [ ] PagerDuty schedule에 신규 담당자 등록 확인
   - [ ] kubectl context, Grafana, Sentry, Langfuse, AWS, Redis CLI 접근 검증 (`scripts/oncall-preflight.sh` 실행)
   - [ ] Runbook(섹션 10.2) 최근 변경 사항 공유
7. **연락 체계**
   - [ ] escalation 대상 연락처 최신화 확인
   - [ ] 휴가/부재로 인한 대체 담당자 사전 지정
8. **핵심 메트릭 베이스라인**
   - [ ] 현재 DAU, 실험 처리량, p95 지연, 비용 burn rate 스냅샷 (대시보드 캡처 첨부)

**문서화 규약**:
- 핸드오프 노트는 `docs/oncall/handoff-YYYY-MM-DD.md`에 PR로 머지 (리뷰어: 신규 담당자)
- 인시던트 발생 시 24시간 내 postmortem 초안 작성 (섹션 10.2 runbook 업데이트 포함)
- 분기 1회 핸드오프 양식 retrospective

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
- Secret Manager 버전 업데이트 + 감사 로그 기록 (actor_user_id, project_id, 신/구 키 hash, rotation_reason)

### 11.3 JWT 서명 키 (Auth 서비스 담당)

- Auth 서비스가 JWKS에서 새 키로 회전
- Backend는 JWKS 캐시 만료(5분) 후 자동 반영
- 이전 키로 서명된 유효한 토큰은 exp까지 사용 가능 (grace period)
- JWKS 회전 이벤트는 Auth 서비스 감사 로그 + Labs Backend `jwks_refresh` INFO 로그(kid 변경 전/후)로 이중 기록

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
