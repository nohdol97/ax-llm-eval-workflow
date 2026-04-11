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
                    └──┬─────────┬─────────┬──┘
                       │         │         │
              ┌────────▼──┐ ┌───▼────┐ ┌──▼──────────┐
              │  LiteLLM   │ │Langfuse│ │ ClickHouse   │
              │  Proxy     │ │  v3    │ │ (직접 쿼리)  │
              │            │ │        │ │              │
              │ ┌────────┐ │ │ Prompt │ │ 실험 결과    │
              │ │Azure   │ │ │ Dataset│ │ 집계/비교    │
              │ │OpenAI  │ │ │ Trace  │ │ 분석 쿼리    │
              │ ├────────┤ │ │ Score  │ │              │
              │ │Gemini  │ │ │ Run    │ └──────────────┘
              │ ├────────┤ │ │        │
              │ │Bedrock │ │ └────────┘
              │ ├────────┤ │
              │ │Claude  │ │
              │ └────────┘ │
              └────────────┘
```

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
- Langfuse v3 인프라의 Redis를 공유하거나, Labs 전용 Redis 인스턴스 사용
- 실험 상태 (running/paused/cancelled + 진행률): TTL 24시간
- 완료된 실험의 최종 상태는 Langfuse trace metadata로 영속화
- 변수 프리셋, 사용자 설정: Frontend localStorage로 관리 (서버 저장 불필요)

## 3. 데이터 흐름

### 3.1 단일 테스트 흐름

```
사용자 → [프롬프트 + 변수 + 이미지 + 모델 설정]
  → Frontend (SSE 연결)
  → Backend: Context Engine (변수 바인딩)
  → Backend: Experiment Runner
      → LiteLLM Proxy → LLM Provider
      ← 스트리밍 응답
  → Backend: Langfuse에 trace/generation 기록
  → Backend: Evaluation Engine (evaluators 지정 시)
  → Frontend: 실시간 응답 렌더링
```

### 3.2 배치 실험 흐름

```
사용자 → [프롬프트 + 데이터셋 + 모델 설정 + 평가 함수 선택]
  → Frontend (SSE 연결, 진행 상태 수신)
  → Backend: Langfuse에서 데이터셋 로드
  → Backend: 각 아이템에 대해 반복:
      1. Context Engine: 변수 바인딩
      2. Experiment Runner → LiteLLM → LLM Provider
      3. Evaluation Engine: 스코어 산출
      4. Langfuse: trace + score + dataset run 기록
  → Backend: 실험 결과 집계
  → Frontend: 결과 테이블 + 차트 렌더링
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

| 컴포넌트 | 배포 방식 |
|----------|-----------|
| Frontend | Vercel |
| Backend | Cloud Run / ECS |
| LiteLLM Proxy | Cloud Run / ECS |
| Langfuse | 셀프호스팅 (VM 또는 K8s) |
| PostgreSQL | Cloud SQL / RDS |
| ClickHouse | ClickHouse Cloud / 셀프호스팅 |
| Redis | Cloud Memorystore / ElastiCache |

### 4.3 네트워크 구성

```
[Internet]
    │
    ├── Frontend (Vercel CDN)
    │       │
    │       ▼
    ├── Backend API (Cloud Run)
    │       │
    │       ├── LiteLLM Proxy (내부 네트워크)
    │       │       └── LLM Providers (외부)
    │       │
    │       ├── Langfuse API (내부 네트워크)
    │       │       ├── PostgreSQL (내부)
    │       │       ├── ClickHouse (내부)
    │       │       └── Redis (내부)
    │       │
    │       └── ClickHouse (직접 쿼리, 내부)
    │
    └── Langfuse Web UI (내부 접근만)
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
