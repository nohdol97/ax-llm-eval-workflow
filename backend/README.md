# Backend (FastAPI)

본 프로젝트의 Python 3.12+ FastAPI 백엔드. 사내 공용 인프라(Langfuse / LiteLLM / Prometheus / OTel / Loki / ClickHouse)와 자체 운영 Redis를 외부 의존성으로 사용한다.

## 디렉터리 구조

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 앱, 라우터 등록, 라이프사이클
│   ├── api/v1/                 # API 라우터 (Phase 3+)
│   │   └── health.py
│   ├── core/                   # 설정·보안·관측성
│   │   ├── config.py           # pydantic-settings BaseSettings
│   │   ├── security.py         # JWKS 기반 JWT 검증 + RBAC
│   │   ├── deps.py             # FastAPI 의존성 주입
│   │   ├── observability.py    # OTel SDK + Prometheus + structlog 초기화
│   │   └── logging.py          # JSON formatter
│   ├── services/               # 외부 시스템 클라이언트
│   │   ├── langfuse_client.py
│   │   ├── litellm_client.py
│   │   ├── redis_client.py
│   │   ├── clickhouse_client.py
│   │   └── score_registry.py   # 부팅 시 Langfuse score config idempotent 등록
│   └── models/                 # Pydantic 요청/응답 스키마
└── tests/
    ├── conftest.py
    ├── fixtures/               # Mock fixtures (Phase 0)
    ├── unit/
    ├── integration/
    └── infra/
```

## 빠른 시작

```bash
# 가상환경 + 의존성
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test,dev]"

# 테스트 (Mock 기반)
pytest -v

# 개발 서버
uvicorn app.main:app --reload --port 8000

# 헬스체크
curl http://localhost:8000/api/v1/health
```

## 환경 변수

`docker/.env.example` 참조. 사내 endpoint(`LANGFUSE_HOST`, `LITELLM_BASE_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT` 등)는 `.env`에 주입.

## 테스트 정책

- **Mock 우선**: `tests/fixtures/`의 Mock 6종(Langfuse / LiteLLM / ClickHouse / Redis / OTel / Loki)으로 사내 의존 없이 단위·통합 테스트
- 테스트 작성은 Codex에 위임 (`/codex:rescue`) — 프로젝트 CLAUDE.md TDD 정책 참조
- 마커: `@pytest.mark.unit` / `integration` / `infra`

## 관측성

- **Prometheus**: `/metrics` 엔드포인트 (FastAPI Instrumentator)
- **OpenTelemetry**: OTLP/HTTP export, sampling 환경별 (dev=0, staging=1.0, prod=0.1)
- **Loki**: structlog JSON 출력, 사내 수집기가 stdout pickup
- **PII 미포함**: 프롬프트/모델 출력 원본 로그 금지
