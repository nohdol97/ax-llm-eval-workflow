# 시스템 아키텍처

## 1. 전체 구조

```
                          ┌──────────────┐
                          │   사용자      │
                          │ (도메인 전문가)│
                          └──────┬───────┘
                                 │
                    ┌────────────▼────────────┐
                    │     Frontend (Next.js)   │
                    │                          │
                    │  ┌────────┐ ┌─────────┐  │
                    │  │Prompt  │ │Experiment│  │
                    │  │Editor  │ │Dashboard │  │
                    │  └────────┘ └─────────┘  │
                    │  ┌────────┐ ┌─────────┐  │
                    │  │Dataset │ │Result    │  │
                    │  │Manager │ │Analyzer  │  │
                    │  └────────┘ └─────────┘  │
                    └────────────┬────────────┘
                                 │ REST API + SSE
                    ┌────────────▼────────────┐
                    │     Backend (FastAPI)     │
                    │                          │
                    │  ┌──────────────────┐    │
                    │  │ Experiment Runner │    │
                    │  │  ├─ Single Test   │    │
                    │  │  └─ Batch Runner  │    │
                    │  └──────────────────┘    │
                    │  ┌──────────────────┐    │
                    │  │ Evaluation Engine │    │
                    │  │  ├─ Built-in      │    │
                    │  │  ├─ LLM-as-Judge  │    │
                    │  │  └─ Custom Code   │    │
                    │  └──────────────────┘    │
                    │  ┌──────────────────┐    │
                    │  │ Context Engine    │    │
                    │  │  └─ Variable Bind │    │
                    │  └──────────────────┘    │
                    │  ┌──────────────────┐    │
                    │  │ Dataset Manager   │    │
                    │  └──────────────────┘    │
                    │  ┌──────────────────┐    │
                    │  │ Langfuse Client   │    │
                    │  │ (SDK 래퍼)        │    │
                    │  └──────────────────┘    │
                    │  ┌──────────────────┐    │
                    │  │ Auth (JWT 검증)   │    │
                    │  └──────────────────┘    │
                    └──┬─────────┬──────────────────┘
                       │         │
              ┌────────▼──┐ ┌────▼──────────────────────────────┐
              │  LiteLLM   │ │           Langfuse v3              │
              │  Proxy     │ │                                    │
              │            │ │  ┌──────────┐  ┌──────────────┐   │
              │ ┌────────┐ │ │  │Langfuse  │  │  PostgreSQL   │   │
              │ │Azure   │ │ │  │Web/API   │  │  (메타: 프로젝 │   │
              │ │OpenAI  │ │ │  │(Prompt/  │  │  트/프롬프트/ │   │
              │ ├────────┤ │ │  │Dataset/  │  │  데이터셋 정의)│   │
              │ │Gemini  │ │ │  │Trace/    │  └──────────────┘   │
              │ ├────────┤ │ │  │Score/Run)│  ┌──────────────┐   │
              │ │Bedrock │ │ │  └──────────┘  │  ClickHouse   │   │
              │ ├────────┤ │ │  ┌──────────┐  │  (Trace/Obs/  │   │
              │ │Claude  │ │ │  │Ingest    │  │   Score 시계열)│   │
              │ └────────┘ │ │  │Worker    │  └──────┬───────┘   │
              └────────────┘ │  └──────────┘         │            │
                             │  ┌──────────────────────────────┐  │
                             │  │  Redis (공유 인스턴스)         │  │
                             │  │  ├─ DB 0: Langfuse 인제스트    │  │
                             │  │  │         큐 / 캐시           │  │
                             │  │  └─ DB 1 (ax:*): Labs 실험     │  │
                             │  │           상태/진행률/알림 큐  │  │
                             │  └──────────────────────────────┘  │
                             │  ┌──────────────┐ ┌─────────────┐  │
                             │  │  MinIO (S3)  │ │ Prometheus  │  │
                             │  │ blob storage │ │  +Grafana   │  │
                             │  │ 멀티모달/대형│ │ 메트릭/비용 │  │
                             │  │ 출력 페이로드│ │ /지연/에러율│  │
                             │  └──────────────┘ └─────────────┘  │
                             └─────────────────┬──────────────────┘
                                               │
                    Backend → ClickHouse 직접 쿼리 (읽기 전용 계정,
                              분석/비교 전용, 쓰기는 Langfuse SDK 경유)
                    Backend/LiteLLM/Langfuse → Prometheus scrape (OBSERVABILITY.md §6)
```

> **주의**: Redis 인스턴스는 Langfuse v3와 Labs가 **공유**하되, DB 번호(`LABS_REDIS_DB=1`)와
> `ax:` 키 접두사로 네임스페이스를 분리한다. Langfuse 인제스트 큐(DB 0)와
> Labs 실험 상태(DB 1)는 서로 키 공간이 겹치지 않으며, 장애 시 영향 범위는
> 공유 인스턴스 자체의 가용성에 한정된다 (격리 전략은 §6 참조).

## 2. 레이어별 역할

### 2.1 Frontend Layer

**역할**: 사용자 인터페이스, 실험 설정, 결과 시각화

| 모듈 | 역할 |
|------|------|
| Prompt Editor | 프롬프트 편집, 변수 바인딩 UI, 멀티모달 입력 (이미지 업로드) |
| Experiment Dashboard | 실험 목록, 실행 상태, 실험 간 비교 차트 |
| Dataset Manager | 데이터셋 업로드 (CSV/JSON), 컬럼 매핑, 미리보기 |
| Result Analyzer | 개별 결과 상세 보기, 스코어 분포, 비용/지연 분석 |

**기술 선택 근거**:
- Next.js App Router: SSR/ISR로 대시보드 초기 로딩 최적화
- SSE (Server-Sent Events): 스트리밍 응답 및 배치 실험 진행 상태 실시간 반영
- Tailwind CSS: 빠른 UI 개발, 일관된 디자인 시스템

### 2.2 Backend Layer

**역할**: 실험 실행, 평가, Langfuse/LLM 연동 오케스트레이션

| 모듈 | 역할 |
|------|------|
| Experiment Runner | 단일 테스트/배치 실험 실행, 병렬 처리, 재시도 |
| Evaluation Engine | 내장/LLM-as-Judge/커스텀 평가 함수 실행 |
| Context Engine | Prompt Variables 바인딩, 동적 컨텍스트 조립 |
| Dataset Manager | 파일 파싱, Langfuse Dataset API 연동, 매핑 |
| Langfuse Client | Langfuse SDK 래퍼, 모든 Langfuse 호출 중앙 관리 |
| Idempotency Middleware | `Idempotency-Key` 헤더 기반 중복 요청 차단 (Redis 저장, TTL 24h) |
| Error Envelope | 표준 에러 응답 포맷 통일 (`code`, `message`, `request_id`, `details`) |
| Score Config Registry | 평가 스코어 정의(name/dataType/range)를 Langfuse Score Config에 등록·동기화 |
| Evaluator Protocol | Built-in/LLM-Judge/Custom 공통 인터페이스 (`evaluate(input, output, expected) → Score`) |

**기술 선택 근거**:
- FastAPI: async 지원으로 LLM 호출 병렬 처리, SSE 스트리밍 네이티브 지원
- Python: LLM SDK 생태계 (langfuse, litellm, openai 등)가 가장 풍부

### 2.3 LLM Gateway Layer (LiteLLM Proxy)

**역할**: 멀티 프로바이더 LLM 호출 통합, 키 관리, 속도 제한

**지원 프로바이더**:
- Azure OpenAI (GPT-4o, GPT-4.1)
- Google Gemini (Gemini 2.5 Pro/Flash)
- AWS Bedrock (Claude 4.5 Sonnet, Llama 3.3)
- Anthropic Direct (Claude 4.5 Sonnet, Claude 4.6 Opus)
- OpenAI Direct (GPT-5.4, o3/o4-mini)

**LiteLLM을 사용하는 이유**:
- 단일 API 인터페이스로 모든 프로바이더 호출
- 프로바이더별 API 키를 중앙 관리
- 자동 fallback, 속도 제한
- `completion_cost()` 함수로 비용 계산 지원

**Langfuse callback 비활성화**:
- LiteLLM의 Langfuse success_callback을 사용하지 않음
- trace/generation 기록은 Labs Backend에서 전담하여 중복 기록 방지
- 비용/토큰 추적은 LiteLLM 응답의 `usage` 필드 + `completion_cost()`로 Labs에서 직접 계산

### 2.4 Data Layer (Langfuse v3)

**역할**: 프롬프트 저장, 실험 데이터 기록, 분석 데이터 제공

| 컴포넌트 | 역할 | 데이터 |
|----------|------|--------|
| PostgreSQL | 메타데이터 저장 | 프로젝트, 사용자, 프롬프트 정의, 데이터셋 정의 |
| ClickHouse | 시계열 분석 데이터 | Trace, Generation, Score, 비용, 지연 시간 |
| Redis | 비동기 큐잉 | 이벤트 인제스트, 워커 큐 |

**데이터 저장 원칙**:
- 프롬프트/데이터셋/trace/score → Langfuse (source of truth)
- 실험 상태/진행률/세션 → Redis (TTL 기반, 실시간 상태)
- 자체 RDBMS는 두지 않음 — Langfuse 데이터 중복 방지

**Labs용 Redis 활용**:
- Langfuse v3가 사용하는 Redis 인스턴스를 공유하되, 별도 DB 번호(`LABS_REDIS_DB`, 기본 `1`)와 `ax:` 키 접두사로 네임스페이스를 분리하여 충돌 방지
- 실험 상태 (running/paused/cancelled + 진행률): TTL 24시간, 완료 후 1시간
- 완료된 실험의 최종 상태는 Langfuse trace metadata로 영속화
- 알림/감사 로그는 Redis Stream으로 append-only 저장, ClickHouse로 주기 백업 (IMPLEMENTATION.md 참고)
- 변수 프리셋, 사용자 설정: Frontend localStorage로 관리 (서버 저장 불필요)

## 3. 데이터 흐름

### 3.1 단일 테스트 흐름

```
사용자 → [프롬프트 + 변수 + 이미지 + 모델 설정]
  → Frontend (SSE 연결)
  → Backend: Auth 미들웨어 (JWT 검증, JWKS 캐시)
  → Backend: Idempotency 미들웨어 (Idempotency-Key 조회/저장)
  → Backend: Context Engine (변수 바인딩)
  → Backend: Experiment Runner
      → LiteLLM Proxy → LLM Provider
      ← 스트리밍 응답 (SSE)
  → Backend: Langfuse Client → Langfuse Web/API
      (trace/generation/usage + completion_cost 기록)
  → Backend: Evaluation Engine (evaluators 지정 시)
      → Langfuse Client → score 기록
  → Frontend: 실시간 응답 렌더링
```

### 3.2 배치 실험 흐름

```
사용자 → [프롬프트 + 데이터셋 + 모델 설정 + 평가 함수 선택]
  → Frontend (SSE 연결, 진행 상태 수신)
  → Backend: Auth 미들웨어 (JWT 검증)
  → Backend: Idempotency 미들웨어 (Idempotency-Key 조회/저장)
  → Backend: Score Config Registry → 사용 evaluator의 score config를 Langfuse에 등록(없으면 생성)
  → Backend: Langfuse Client → 데이터셋 로드
  → Backend: 실험 상태를 Redis(ax:experiment:{id})에 기록, TTL 24h
  → Backend: 각 아이템에 대해 asyncio 병렬 실행 (semaphore로 동시성 제한):
      1. Context Engine: 변수 바인딩
      2. Experiment Runner → LiteLLM → LLM Provider
      3. Evaluation Engine: 스코어 산출 (Custom Code는 Docker 샌드박스)
      4. Langfuse Client → trace + score + dataset run item 기록
      5. Redis HINCRBY로 진행률 갱신 → SSE로 Frontend 브로드캐스트
  → Backend: 실험 완료 시 최종 상태를 Langfuse trace metadata로 영속화
  → Frontend: 결과 테이블 + 차트 렌더링 (ClickHouse 직접 쿼리)
```

### 3.3 실험 비교 흐름

```
사용자 → [비교할 실험 Run 선택 (2개 이상)]
  → Frontend
  → Backend: ClickHouse 직접 쿼리
      - run별 avg latency, total cost, avg score, token count
      - 아이템별 상세 비교 (output diff, score diff)
  → Frontend: 비교 차트 + 상세 테이블 렌더링
```

### 3.4 데이터셋 업로드 흐름

```
사용자 → [CSV/JSON 파일 + 컬럼 매핑 설정]
  → Frontend: 파일 파싱, 미리보기
  → Backend: 매핑 적용, 검증
  → Backend: Langfuse Dataset API로 업로드
      - create_dataset()
      - create_dataset_item() × N
  → Frontend: 완료 알림
```

## 4. 인프라 구성

### 4.1 개발 환경 (Docker Compose)

```yaml
# 구성 요소
services:
  frontend:     # Next.js dev server (port 3000)
  backend:      # FastAPI (port 8000)
  litellm:      # LiteLLM Proxy (port 4000)
  langfuse:     # Langfuse Web (내부 3000, 호스트 노출 3001)
  postgres:     # PostgreSQL (port 5432)
  clickhouse:   # ClickHouse (port 8123)
  redis:        # Redis (port 6379)
```

### 4.2 운영 환경

| 컴포넌트 | 배포 방식 | HA/확장성 |
|----------|-----------|-----------|
| Frontend | Vercel | CDN edge, 오토스케일 |
| Backend | Cloud Run / ECS | **최소 2 replica**, 4 vCPU / 4GB (OBSERVABILITY.md §7). Stateless, Redis로 세션 공유 |
| LiteLLM Proxy | Cloud Run / ECS | 2+ replica, Master Key는 Secret Manager |
| Langfuse Web/API | 셀프호스팅 (VM 또는 K8s) | 2+ replica |
| Langfuse Ingest Worker | 셀프호스팅 | 별도 파드/컨테이너, Redis 큐 consumer |
| PostgreSQL | Cloud SQL / RDS | Multi-AZ, 자동 백업 |
| ClickHouse | ClickHouse Cloud / 셀프호스팅 | replica 2+, 읽기 전용 계정 분리 |
| Redis | Cloud Memorystore / ElastiCache | 1GB+ 인스턴스, AOF 활성화. DB 0=Langfuse, DB 1=Labs(`ax:*`) |
| MinIO | 셀프호스팅 (S3 호환) | Langfuse v3 blob storage, 멀티모달/대형 페이로드 (LANGFUSE.md, ADR-009) |
| Prometheus + Grafana | 셀프호스팅 / 매니지드 | Backend·LiteLLM·Langfuse scrape, 비용/지연/에러율 대시보드 (OBSERVABILITY.md §6, ADR-010) |
| Evaluator Sandbox Host | Backend 호스트 내 Docker 데몬 | 호스트당 `EVAL_SANDBOX_MAX_CONCURRENT=10` |

**동기성·확장성 전략**:
- Backend는 자체 워커 큐(Celery 등) 없이 **FastAPI asyncio + semaphore**로 배치 실험 병렬 처리. Redis는 상태 저장소이며 작업 분배 큐로 쓰지 않는다.
- 수평 확장은 Backend replica 증설로 대응. 각 실험은 단일 replica에 고정(sticky)되며, 재시작 시 Redis 상태로 UI 복구 + Langfuse trace metadata로 최종 영속화.
- SSE 연결은 실험을 실행 중인 replica로 라우팅 필요 → 로드밸런서에 `experiment_id` 기반 세션 어피니티 설정.

### 4.3 네트워크 구성

```
[Internet]
    │
    ├── Frontend (Vercel CDN)
    │       │  HTTPS (REST + SSE, session affinity by experiment_id)
    │       ▼
    ├── Backend API (Cloud Run, 2+ replica)
    │       │
    │       ├── LiteLLM Proxy (내부 네트워크, Master Key 인증)
    │       │       └── LLM Providers (외부, egress만)
    │       │
    │       ├── Langfuse Web/API (내부 네트워크, 프로젝트 API Key)
    │       │       ├── PostgreSQL (내부, Langfuse 전용)
    │       │       ├── ClickHouse (내부, Langfuse 소유)
    │       │       └── Redis (내부, Labs와 공유·DB 분리)
    │       │
    │       ├── Redis (내부, DB 1, ax:* 접두사)
    │       │
    │       └── ClickHouse 직접 쿼리 (읽기 전용 계정, 분석 전용)
    │
    └── Langfuse Web UI (내부 VPN/SSO 접근만)
```

## 5. 보안

### 5.1 인증/인가
- **사내 Auth 서비스 연동**: Labs는 JWT를 발급하지 않음. 별도 Auth 프로젝트에서 발급된 JWT를 수신하여 검증만 수행
- **JWT 검증**: JWKS 엔드포인트에서 공개키를 가져와 서명 검증
- **RBAC**: JWT payload의 role/groups 기반으로 권한 제어
  - `admin`: Custom Code Evaluator 실행 권한, 설정 변경
  - `user`: 실험 생성/실행, 데이터셋 업로드
  - `viewer`: 읽기 전용 (결과 조회, 비교 분석)
- Langfuse: 프로젝트별 API Key (public/secret key pair)
- LiteLLM Proxy: Master Key로 접근 제어 (최소 32자, Secret Manager에서 관리, 90일 로테이션)
- LLM Provider 키: LiteLLM Proxy에서만 보유, Backend 코드에서 직접 접근 금지

### 5.2 시크릿 관리
- 환경변수로 주입 (`.env` 파일은 gitignore)
- 운영 환경: Cloud Secret Manager (GCP) 또는 AWS Secrets Manager
- LLM API 키는 LiteLLM Proxy 설정에서만 관리
- Langfuse 프로젝트별 API Key는 Secret Manager에 저장, Backend에서 필요 시 조회
- CI/CD에 시크릿 스캔 (gitleaks) 적용

### 5.3 네트워크 보안

**Docker Compose 네트워크 분리**:
```
frontend_net (외부 노출):
  - frontend (port 3000)
  - backend (port 8000)

backend_net (운영 환경에서는 VPC/방화벽으로 외부 차단):
  - backend
  - litellm
  - langfuse
  - postgres
  - clickhouse
  - redis
```
- 운영 환경에서 내부 서비스는 `expose:`만 사용, `ports:` 사용 금지
- 개발 환경에서는 Langfuse Web UI 접근을 위해 `ports: 3001:3000` 허용
- LiteLLM Proxy: backend_net에서만 접근 가능
- ClickHouse: backend_net에서만 접근, 읽기 전용 계정 필수

**CORS 정책**:
- 운영: Frontend 도메인만 허용 (환경변수로 관리)
- 개발: `localhost:3000`만 허용 (와일드카드 `*` 금지)
- `credentials: true`, `allow_methods: ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]`
- `allow_headers: ["Authorization", "Content-Type"]`

### 5.4 Custom Evaluator 보안

**Docker 컨테이너 격리**:
- 평가 코드는 별도 Docker 컨테이너에서 실행
- 실험 시작 시 컨테이너 생성, 전체 아이템 실행 후 삭제 (아이템마다 생성하지 않음)
- 제약: 네트워크 없음, 볼륨 없음, non-root, 5초 타임아웃, 128MB 메모리
- Custom Evaluator 실행 권한은 `admin` 역할로 제한

### 5.5 데이터 프라이버시
- 프롬프트/모델 출력에 PII 포함 가능성을 사용자에게 경고 표시
- Backend 로그에 프롬프트/출력 원본 기록 금지 (trace_id만 기록)
- LLM-as-Judge 사용 시 데이터가 외부 LLM Provider로 전송된다는 경고를 UI에 표시
- ClickHouse 쿼리는 파라미터화된 쿼리(parameterized query) 필수

## 6. 장애 격리 및 복구

| 장애 지점 | 영향 범위 | 격리/복구 전략 |
|-----------|-----------|----------------|
| LLM Provider 하나 장애 | 해당 모델 실험만 실패 | LiteLLM fallback, 실험별 재시도, 다른 모델은 정상 |
| LiteLLM Proxy 다운 | 모든 신규 LLM 호출 실패 | 2+ replica로 HA, Backend는 `/health/ready`에서 차단 |
| Langfuse Web/API 다운 | trace 기록 실패, Prompt/Dataset 조회 불가 | SDK 재시도 + 로컬 버퍼링, 실험 실행은 Redis 상태로 계속 진행 후 복구 시 백필 |
| ClickHouse 다운 | 분석/비교 API만 실패 | 실험 실행은 영향 없음 (쓰기는 Langfuse API 경유). 프론트는 분석 섹션만 에러 표시 |
| PostgreSQL 다운 | Langfuse 전체 장애 | Langfuse 자체의 HA로 대응, Labs는 Redis 상태로 진행 중 실험을 UI 복구만 제공 |
| Redis 다운 | 실험 상태/진행률 유실, 신규 실험 생성 불가 | AOF 스냅샷 + 장애 시 Langfuse trace metadata로 완료 실험 조회 가능. 알림은 best-effort |
| Evaluator Sandbox (Docker) 장애 | Custom Code 평가만 실패 | 실험 생성 시 preflight check, Built-in/LLM Judge 평가는 영향 없음 |
| Backend replica 크래시 | 해당 replica에 고정된 실험 일시 중단 | LB가 healthy replica로 재라우팅, Redis 상태는 유지되어 사용자는 재시작 가능 |

**격리 원칙**:
- Labs Backend는 Langfuse/ClickHouse/Redis 각각에 대한 circuit breaker와 retry policy를 갖는다 (상세: OBSERVABILITY.md §8).
- 쓰기 경로(Langfuse SDK)와 읽기 경로(ClickHouse 직접 쿼리)를 분리하여 분석 부하가 실험 실행을 간섭하지 않도록 한다.
- Custom Evaluator는 네트워크 없는 Docker 네임스페이스에서 실행되어 호스트/다른 실험에 side-effect를 전파할 수 없다.

**백업 및 복구 목표 (RTO/RPO)**:

| 컴포넌트 | RPO (데이터 손실 허용) | RTO (복구 목표 시간) | 백업 전략 |
|----------|------------------------|----------------------|-----------|
| PostgreSQL (Langfuse 메타) | 5분 | 30분 | Cloud SQL/RDS PITR + 일 1회 스냅샷 (보관 30일) |
| ClickHouse (trace/score) | 1시간 | 2시간 | 일 1회 풀백업 + 시간별 증분, 보관 14일 |
| Redis (실험 상태) | 15분 | 15분 | AOF everysec + RDB 1h 스냅샷, 손실 시 Langfuse trace metadata로 복구 가능 |
| MinIO (blob storage) | 1시간 | 1시간 | S3 호환 버전관리 + 일 1회 스냅샷, 보관 30일 |
| Backend/LiteLLM (stateless) | 0 | 5분 | 컨테이너 이미지 재배포, IaC로 환경 재구성 |

> 분기 1회 복구 훈련(restore drill)을 수행하여 RTO/RPO 충족 여부를 검증한다 (운영 핸드오프 체크리스트는 IMPLEMENTATION.md Phase 8 참조).

## 7. 아키텍처 결정 기록 (ADR)

주요 기술 결정과 근거. 상세 ADR 문서는 `docs/adr/`에 별도 관리.

| ID | 결정 | 근거 |
|----|------|------|
| ADR-001 | 자체 RDBMS 없이 Langfuse를 source of truth로 사용 | 데이터 중복 방지, Langfuse가 trace/score/dataset의 1차 저장소 (LANGFUSE.md §1) |
| ADR-002 | Celery 대신 FastAPI asyncio + semaphore로 배치 처리 | 운영 복잡도 감소, 단일 replica sticky 모델로 충분 (EVALUATION.md §4) |
| ADR-003 | Redis는 Langfuse 인스턴스 공유 + DB 분리(`ax:*`) | 인프라 비용/운영 단순화, 네임스페이스로 충돌 방지 (LANGFUSE.md §3) |
| ADR-004 | LiteLLM Langfuse callback 비활성화, Backend가 trace 전담 | 중복 기록 방지, 비용 계산 일관성 (OBSERVABILITY.md §6 비용 메트릭, LANGFUSE.md §4) |
| ADR-005 | Custom Evaluator는 Docker 컨테이너 격리, 실험 단위 컨테이너 재사용 | 보안(네트워크/볼륨 차단) + 아이템별 생성 오버헤드 회피 (EVALUATION.md §5 Protocol) |
| ADR-006 | Idempotency-Key 미들웨어를 모든 변경 API에 적용 | 네트워크 재시도/사용자 더블클릭에 의한 중복 실험 방지 (EVALUATION.md §3) |
| ADR-007 | Evaluator Protocol 단일 인터페이스 + Score Config 사전 등록 | Built-in/LLM-Judge/Custom 통일, Langfuse Score 데이터 일관성 (LANGFUSE.md Score Config, EVALUATION.md §5) |
| ADR-008 | 표준 Error Envelope 포맷 강제 (`code`/`message`/`request_id`) | Frontend 에러 핸들링 단순화, 추적성 확보 (OBSERVABILITY.md §4 로그 스키마) |
| ADR-009 | MinIO를 Langfuse v3 blob storage로 도입 (멀티모달 입력/대형 출력) | S3 호환, 셀프호스팅 비용 절감 (LANGFUSE.md MinIO, OBSERVABILITY.md §6) |
| ADR-010 | Prometheus + Grafana로 Backend/LiteLLM/Langfuse 메트릭 수집 | 비용/지연/에러율 단일 대시보드, ClickHouse 부하 분리 (OBSERVABILITY.md §6 비용 메트릭) |
| ADR-011 | 시크릿은 Cloud Secret Manager 전용 + 90일 로테이션 + gitleaks 스캔 강제 | LLM/Langfuse/JWT 키 노출 방지, .env 커밋 차단, LiteLLM Master Key 일원화 (§5.2, OBSERVABILITY.md §9) |

> ADR 상세 및 cross-link은 `docs/adr/` 및 LANGFUSE.md / EVALUATION.md / OBSERVABILITY.md 해당 절 참조.
