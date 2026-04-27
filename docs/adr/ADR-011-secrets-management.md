# ADR-011: 시크릿 관리 정책

## 상태

Accepted (2026-04-26)

## 컨텍스트

ax-llm-eval-workflow(이하 "본 프로젝트")는 사내 공용 인프라(Langfuse / LiteLLM /
Prometheus / OpenTelemetry Collector / Loki / ClickHouse / Auth)를 외부 엔드포인트로
참조하며, 자체 운영 컴포넌트(Backend / Frontend / 옵션 Redis / sandbox 이미지)만
관리한다. 따라서 본 프로젝트가 다루는 시크릿은 다음 두 범주로 나뉜다.

1. **본 프로젝트가 직접 관리하는 시크릿** — 사내 인프라팀이 발급하지만 본 프로젝트의
   환경변수 / Docker secrets 로 주입되어 사용된다.
2. **본 프로젝트가 절대 보관하지 않는 시크릿** — LLM Provider API Key, JWT 서명 키
   등은 사내 LiteLLM / Auth 서비스가 단독 보관한다.

이 경계가 모호하면 다음과 같은 위험이 발생한다:

- 보관 책임 중복으로 인한 키 누출 표면적 확대 (특히 LLM Provider 키는 비용 손실 직결)
- 키 로테이션 주기/책임자 부재로 인한 stale credential 운영
- 환경별 주입 경로(개발 `.env` / 데모 환경변수 / 운영 secret store) 가 불일치하여
  운영 사고 시 복구 절차 수립 곤란
- CI 파이프라인에서 본 프로젝트 리포지토리에 LLM Provider 키가 실수로 추가되어도
  검출되지 않음

본 ADR은 BUILD_ORDER.md Phase 1 작업 1-9의 직접 산출물로, 작업 1-1(compose 4종) /
1-3(.env.example) 와 동시에 확정되어야 환경변수 명명·주입 경로 정합성이 유지된다.

## 결정

본 프로젝트가 관리하는 시크릿 카탈로그를 명시적으로 정의하고, 카탈로그 외 시크릿은
원천 서비스가 단독 보관하도록 책임을 분리한다. 환경별 주입 경로(개발/데모/운영)와
로테이션 RACI를 본 ADR에 고정한다.

## 시크릿 카탈로그 (본 프로젝트 관리 대상)

| 환경변수 | 발급 주체 | 로테이션 주기 | 비고 |
|---|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | 사내 Langfuse 운영팀 | 90일 | 프로젝트별 키 분리. UI 노출 OK (public). |
| `LANGFUSE_SECRET_KEY` | 사내 Langfuse 운영팀 | 90일 | UI / 로그 / 클라이언트 노출 절대 금지. |
| `LITELLM_VIRTUAL_KEY` | 사내 LiteLLM 운영팀 | 90일 | 본 프로젝트 전용 Virtual Key (예산/rate limit 분리). |
| `CLICKHOUSE_READONLY_PASSWORD` | 사내 인프라팀 | 180일 | `labs_readonly` 계정 전용. SELECT 권한만. |
| `REDIS_PASSWORD` | 사내 Redis 임차 시 사내, 자체 운영 시 본 프로젝트 platform owner | 180일 | TLS / network policy 와 함께 사용. |
| `OTEL_EXPORTER_OTLP_HEADERS` | 사내 Observability 팀 | 90일 | `Authorization=Bearer <token>` 형식. URL-encoded. |

### 카탈로그 제외 (본 프로젝트가 절대 보관하지 않음)

| 시크릿 | 단독 보관 주체 | 사유 |
|---|---|---|
| LLM Provider API Key (Azure / Gemini / Anthropic / OpenAI / AWS Bedrock 등) | 사내 LiteLLM Proxy | LiteLLM이 Provider 호출 단일 진입점. 본 프로젝트는 Virtual Key로만 호출. CLAUDE.md "보안 규칙". |
| JWT 서명 키 (private key) | 사내 Auth 서비스 | 본 프로젝트는 JWKS 공개키로 검증만 수행. |
| Langfuse 내부 PostgreSQL / S3 자격증명 | 사내 Langfuse 운영팀 | Langfuse 내부 의존성. 본 프로젝트는 public API만 사용. |
| 사내 Auth 데이터베이스 자격증명 | 사내 Auth 운영팀 | 본 프로젝트와 무관. |

CI 단계에서 위 제외 키 패턴(`AZURE_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `AWS_BEDROCK_*`)이 변경 파일에 포함되면 빌드를 실패시킨다.
(scripts 디렉터리에 lint hook 추가 — Phase 0 후속 작업.)

## 환경별 주입 경로

| 환경 | 주입 경로 | 파일/메커니즘 | 비고 |
|---|---|---|---|
| 개발 (development) | 로컬 `.env` 파일 | `docker/.env` (gitignore) | `docker/.env.example` 참조하여 개인이 수동 작성. 평문 보관 허용 (로컬 한정). |
| 데모 (demo) | 환경변수 직접 주입 | `docker/.env.demo` 또는 CI/CD 변수 | 데모 호스트에 한정. 평문 보관 가능하나 리포지토리 커밋 금지. |
| 운영 (production) | 사내 secret store (Vault/KMS) → Docker secrets | `docker-compose.prod.yml`의 `secrets:` 블록 | `.env`에 평문 보관 금지. 사내 배포 파이프라인이 `/run/secrets/labs/*` 경로에 파일로 주입. |

`.env.example` 의 모든 시크릿 슬롯은 빈 값으로 유지하며, placeholder URL만 기록한다.
(`docker/.env.example` 참조)

## 로테이션 RACI

| 시크릿 | Responsible (실행) | Accountable (승인) | Consulted | Informed |
|---|---|---|---|---|
| `LANGFUSE_*` | 사내 Langfuse 운영팀 | 사내 Langfuse PO | 본 프로젝트 platform owner | 본 프로젝트 사용자 |
| `LITELLM_VIRTUAL_KEY` | 사내 LiteLLM 운영팀 | 사내 LiteLLM PO | 본 프로젝트 platform owner | 본 프로젝트 사용자 |
| `CLICKHOUSE_READONLY_PASSWORD` | 사내 인프라팀 | 사내 인프라팀장 | 본 프로젝트 platform owner | — |
| `REDIS_PASSWORD` | 사내 Redis 임차: 사내 인프라팀 / 자체 운영: 본 프로젝트 platform owner | 위와 동일 | — | — |
| `OTEL_EXPORTER_OTLP_HEADERS` | 사내 Observability 팀 | 사내 Observability PO | 본 프로젝트 platform owner | — |
| LLM Provider Key (제외) | 사내 LiteLLM 운영팀 | 사내 LiteLLM PO | — | 본 프로젝트는 Virtual Key 회전 알림만 수신 |
| JWT 서명 키 (제외) | 사내 Auth 운영팀 | 사내 Auth PO | — | JWKS 갱신 알림 수신 |

본 프로젝트 platform owner의 책임:
1. 분기별 시크릿 카탈로그 audit (실제 사용 환경변수 vs 본 ADR 카탈로그 일치 확인)
2. 로테이션 주기 도래 30일 전 사내 발급 주체에 갱신 요청
3. 운영 환경 secret store 동기화 점검 (Vault → Docker secrets 주입 검증)

## 결과

### 긍정적 영향

- **명확한 책임 분리**: LLM Provider 키 보관은 사내 LiteLLM 단독 책임으로 못박아,
  본 프로젝트 리포지토리/이미지/로그에서 해당 키가 노출될 표면적을 0으로 만든다.
- **자동 검증 가능**: CI에서 제외 키 패턴 lint hook으로 실수 커밋을 차단할 수 있다.
- **로테이션 추적성**: RACI로 발급 주체/승인자가 명확하므로, 분기별 audit 시 누락
  탐지가 쉬워진다.
- **환경 일관성**: 개발/데모/운영의 주입 경로가 표 한 장으로 정의되어, 신규 시크릿
  추가 시에도 동일 패턴을 따르면 된다.

### 부정적 영향 / Trade-off

- **본 프로젝트 platform owner 부담 증가**: 분기별 audit + 로테이션 알림 수신·요청
  업무가 추가된다. 자동화(예: 사내 Vault API 연동)는 후속 작업으로 고려한다.
- **개발 환경 평문 허용**: 로컬 `.env` 파일 평문 보관을 허용하므로, 개발자 PC 보안에
  의존한다. 차단 정책(예: pre-commit hook으로 `.env` 변경 거부)으로 보완한다.
- **Docker secrets 의존**: 운영 환경에서 `/run/secrets/labs/*` 파일 마운트 실패 시
  Backend가 부팅에 실패한다. 사내 배포 파이프라인의 사전 검증이 전제되어야 한다.

## 대안 검토

### 대안 A — 모든 시크릿을 본 프로젝트가 보관

LLM Provider 키 포함 모든 시크릿을 본 프로젝트 `.env`/Vault에 보관.

- 거부 사유: LLM Provider 키 누출 시 비용 손실 직결. LiteLLM Proxy의 존재 의의(
  Provider 추상화 + 사용량/예산 관리)를 무력화한다. CLAUDE.md "보안 규칙"과 정면
  충돌.

### 대안 B — `.env` 단일화 (운영도 평문)

운영 환경에서도 `.env` 파일에 평문 시크릿을 보관하고 Docker volume으로 마운트.

- 거부 사유: 사내 보안 정책상 운영 환경 평문 시크릿 금지. 시크릿 로테이션 시
  컨테이너 재배포 외에 회전 절차가 없어 운영 비용이 높다.

### 대안 C — Sealed Secrets / SOPS in Git

암호화된 시크릿 파일을 리포지토리에 커밋하고 배포 시 복호화.

- 거부 사유: 사내가 이미 Vault/KMS를 운영하므로 추가 인프라 도입 비용 대비 이점이
  적다. 본 프로젝트는 사내 secret store와의 통합을 우선한다.

### 대안 D — Backend가 LLM Provider 키 직접 보관 후 LiteLLM 우회 호출

LiteLLM을 거치지 않고 Backend가 Provider SDK를 직접 호출.

- 거부 사유: 멀티 프로바이더 추상화 / 비용 추적 / rate limit 관리가 모두 본 프로젝트
  몫이 된다. 사내 표준에 역행.

## 참조

- BUILD_ORDER.md Phase 1 작업 1-1 / 1-3 / 1-9
- INFRA_INTEGRATION_CHECKLIST.md (사내 의존성 명세)
- CLAUDE.md "보안 규칙" 절
- docker/.env.example (시크릿 슬롯 정의)
- docker/docker-compose.prod.yml (Docker secrets 마운트)
