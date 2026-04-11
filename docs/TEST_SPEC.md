# 테스트 명세 (PART 1: Phase 0-3)

ax-llm-eval-workflow 프로젝트의 테스트 전략, 인프라, 그리고 Phase 1~3에 해당하는 상세 테스트 케이스를 정의한다.

---

## Phase 0: 테스트 인프라

### 0.1 Backend 테스트 구조 (pytest)

```
backend/
├── tests/
│   ├── conftest.py                 # 글로벌 fixture, 테스트 설정
│   ├── fixtures/                   # 공용 fixture 모듈
│   │   ├── __init__.py
│   │   ├── auth.py                 # JWT 생성/검증 fixture
│   │   ├── langfuse.py             # Mock Langfuse Client fixture
│   │   ├── redis.py                # Mock/실제 Redis fixture
│   │   ├── litellm.py              # Mock LiteLLM fixture
│   │   └── clickhouse.py           # Mock ClickHouse fixture
│   ├── utils/                      # 테스트 유틸리티
│   │   ├── __init__.py
│   │   ├── factories.py            # 테스트 데이터 팩토리
│   │   └── assertions.py           # 커스텀 assertion 헬퍼
│   ├── unit/                       # 단위 테스트
│   │   ├── test_security.py        # JWT 미들웨어
│   │   ├── test_langfuse_client.py # Langfuse Client Wrapper
│   │   ├── test_redis_client.py    # Redis Client Wrapper
│   │   └── test_config.py          # Config 모듈
│   ├── integration/                # 통합 테스트
│   │   ├── test_prompts_api.py     # 프롬프트 API
│   │   ├── test_datasets_api.py    # 데이터셋 API
│   │   ├── test_models_api.py      # 모델 API
│   │   ├── test_projects_api.py    # 프로젝트 API
│   │   ├── test_search_api.py      # 검색 API
│   │   └── test_health_api.py      # 헬스체크 API
│   └── infra/                      # 인프라 연결 테스트
│       ├── test_redis_connection.py
│       ├── test_clickhouse_connection.py
│       ├── test_langfuse_connection.py
│       ├── test_litellm_connection.py
│       └── test_postgres_connection.py
```

#### conftest.py 핵심 fixture

```python
# -- 앱 인스턴스 --
@pytest.fixture
def app() -> FastAPI:
    """테스트용 FastAPI 앱 인스턴스 (의존성 오버라이드 적용)."""

@pytest.fixture
def client(app) -> TestClient:
    """FastAPI TestClient."""

@pytest.fixture
def async_client(app) -> AsyncClient:
    """httpx AsyncClient (비동기 테스트용)."""

# -- 인증 --
@pytest.fixture
def jwt_admin() -> str:
    """admin 역할의 유효한 JWT 토큰."""

@pytest.fixture
def jwt_user() -> str:
    """user 역할의 유효한 JWT 토큰."""

@pytest.fixture
def jwt_viewer() -> str:
    """viewer 역할의 유효한 JWT 토큰."""

@pytest.fixture
def jwt_expired() -> str:
    """만료된 JWT 토큰."""

@pytest.fixture
def auth_headers_admin(jwt_admin) -> dict:
    """admin Authorization 헤더."""
    return {"Authorization": f"Bearer {jwt_admin}"}

# -- 서비스 Mock --
@pytest.fixture
def mock_langfuse() -> MockLangfuseClient:
    """Langfuse Client mock 객체."""

@pytest.fixture
def mock_redis() -> MockRedisClient:
    """Redis Client mock 객체."""

@pytest.fixture
def mock_litellm() -> MockLiteLLMProxy:
    """LiteLLM Proxy mock 객체."""

@pytest.fixture
def mock_clickhouse() -> MockClickHouseClient:
    """ClickHouse Client mock 객체."""
```

#### pytest 설정 (pyproject.toml)

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"
python_functions = "test_*"
asyncio_mode = "auto"
markers = [
    "unit: 단위 테스트",
    "integration: 통합 테스트 (TestClient 사용)",
    "infra: 인프라 연결 테스트 (실제 서비스 필요)",
    "slow: 실행 시간이 긴 테스트",
]
filterwarnings = ["ignore::DeprecationWarning"]
```

### 0.2 Frontend 테스트 구조 (vitest)

```
frontend/
├── vitest.config.ts               # vitest 설정
├── tests/
│   ├── setup.ts                   # 글로벌 setup (MSW 등)
│   ├── mocks/                     # API mock 핸들러
│   │   ├── handlers.ts
│   │   └── server.ts              # MSW setupServer
│   ├── utils/                     # 테스트 유틸
│   │   ├── render.tsx             # 커스텀 render (providers 포함)
│   │   └── factories.ts           # 테스트 데이터 팩토리
│   ├── components/                # 컴포넌트 단위 테스트
│   ├── hooks/                     # 커스텀 hook 테스트
│   └── pages/                     # 페이지 통합 테스트
```

#### vitest.config.ts

```typescript
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      exclude: ['tests/**', 'node_modules/**'],
      thresholds: {
        lines: 80,
        functions: 80,
        branches: 70,
        statements: 80,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
```

### 0.3 공통 테스트 유틸리티

#### Mock Langfuse Client

```python
class MockLangfuseClient:
    """Langfuse SDK 호출을 가로채는 mock 클라이언트."""

    def __init__(self):
        self.prompts: dict[str, list] = {}       # name -> [versions]
        self.datasets: dict[str, list] = {}      # name -> [items]
        self.traces: list[dict] = []
        self.scores: list[dict] = []
        self._connected = True

    def get_prompt(self, name: str, version: int = None, label: str = None):
        """프롬프트 조회 mock."""

    def create_dataset(self, name: str, **kwargs):
        """데이터셋 생성 mock."""

    def create_dataset_item(self, dataset_name: str, **kwargs):
        """데이터셋 아이템 생성 mock."""

    def trace(self, **kwargs):
        """Trace 생성 mock."""

    def score(self, **kwargs):
        """Score 기록 mock."""

    def flush(self):
        """flush mock (no-op)."""

    def simulate_connection_failure(self):
        """연결 실패 시뮬레이션."""
        self._connected = False

    def simulate_not_found(self, resource_type: str, name: str):
        """리소스 미존재 시뮬레이션."""
```

#### Mock Redis Client

```python
class MockRedisClient:
    """Redis 명령을 인메모리 dict로 시뮬레이션."""

    def __init__(self):
        self._store: dict[str, Any] = {}
        self._ttls: dict[str, float] = {}
        self._connected = True

    def hset(self, key: str, mapping: dict): ...
    def hget(self, key: str, field: str): ...
    def hgetall(self, key: str) -> dict: ...
    def hincrby(self, key: str, field: str, amount: int): ...
    def hincrbyfloat(self, key: str, field: str, amount: float): ...
    def expire(self, key: str, seconds: int): ...
    def ttl(self, key: str) -> int: ...
    def exists(self, key: str) -> bool: ...
    def sadd(self, key: str, *members): ...
    def smembers(self, key: str) -> set: ...
    def zrevrangebyscore(self, key: str, max: float, min: float, start: int, num: int): ...
    def eval(self, script: str, numkeys: int, *keys_and_args): ...

    def simulate_connection_failure(self): ...
```

#### Mock LiteLLM Proxy

```python
class MockLiteLLMProxy:
    """LiteLLM Proxy HTTP 응답을 시뮬레이션."""

    def __init__(self):
        self.models = [
            {"id": "gpt-4o", "provider": "azure", "supports_vision": True},
            {"id": "gemini-2.5-pro", "provider": "google", "supports_vision": True},
        ]

    def get_model_info(self) -> dict:
        """GET /model/info 응답."""

    def health(self) -> dict:
        """GET /health 응답."""
```

#### Test JWT Generator

```python
import jwt
from datetime import datetime, timedelta

def create_test_jwt(
    sub: str = "user_001",
    role: str = "user",
    groups: list[str] = None,
    exp_delta: timedelta = timedelta(hours=1),
    issuer: str = "https://auth.example.com",
    audience: str = "ax-llm-eval-workflow",
    secret: str = "test-secret-key-for-testing",
    algorithm: str = "HS256",
    extra_claims: dict = None,
) -> str:
    """테스트용 JWT 토큰 생성.

    Args:
        sub: 사용자 ID
        role: 역할 (admin, user, viewer)
        groups: 그룹 목록
        exp_delta: 만료 시간 델타 (음수이면 만료된 토큰)
        issuer: JWT issuer
        audience: JWT audience
        secret: 서명용 비밀 키 (테스트에서는 HS256, 프로덕션은 RS256/JWKS)
        algorithm: 서명 알고리즘
        extra_claims: 추가 claim

    Returns:
        JWT 문자열
    """
    now = datetime.utcnow()
    payload = {
        "sub": sub,
        "role": role,
        "groups": groups or [],
        "iat": now,
        "exp": now + exp_delta,
        "iss": issuer,
        "aud": audience,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm=algorithm)
```

### 0.4 CI 파이프라인 (GitHub Actions)

```yaml
# .github/workflows/test.yml
name: Test Suite

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  backend-unit:
    name: Backend Unit Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r backend/requirements-dev.txt
      - run: cd backend && pytest tests/unit/ -m "not slow" --tb=short -q

  backend-integration:
    name: Backend Integration Tests
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7-alpine
        ports: ['6379:6379']
        options: --health-cmd "redis-cli ping" --health-interval 10s
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r backend/requirements-dev.txt
      - run: cd backend && pytest tests/integration/ --tb=short -q
        env:
          REDIS_URL: redis://localhost:6379/1

  backend-infra:
    name: Backend Infra Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose -f docker/docker-compose.yml up -d postgres clickhouse redis langfuse litellm
      - run: docker compose -f docker/docker-compose.yml exec -T clickhouse bash /scripts/setup-clickhouse-readonly.sh
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r backend/requirements-dev.txt
      - run: cd backend && pytest tests/infra/ --tb=short -q
      - run: docker compose -f docker/docker-compose.yml down -v

  frontend-unit:
    name: Frontend Unit Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: cd frontend && npm ci
      - run: cd frontend && npx vitest run --coverage

  lint:
    name: Lint & Type Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r backend/requirements-dev.txt
      - run: cd backend && ruff check . && mypy app/
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: cd frontend && npm ci && npm run lint && npx tsc --noEmit
```

---

## Phase 1: 인프라 테스트

인프라 서비스(Redis, ClickHouse, Langfuse, LiteLLM, PostgreSQL)의 연결, 헬스체크, 인증을 검증한다.
이 테스트들은 `@pytest.mark.infra` 마커를 사용하며, 실제 Docker 서비스가 기동된 환경에서 실행한다.

### 1.1 Redis 연결 테스트

**파일**: `tests/infra/test_redis_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_connect_successfully_when_valid_credentials` | REDIS_URL 환경변수 (password 포함) | `PONG` 응답, 연결 성공 | 실제 Redis 인스턴스 | - |
| 2 | `test_should_fail_connection_when_wrong_password` | 잘못된 password를 포함한 REDIS_URL | `AuthenticationError` 예외 발생 | 실제 Redis 인스턴스 | 빈 문자열 password |
| 3 | `test_should_fail_connection_when_host_unreachable` | 존재하지 않는 host (`redis://invalid-host:6379`) | `ConnectionError` 예외, 타임아웃 이내 발생 | 없음 | DNS 미해석, 포트 미응답 |
| 4 | `test_should_respond_to_healthcheck_ping_when_connected` | 정상 연결된 Redis 클라이언트 | `ping()` 결과 `True` | 실제 Redis 인스턴스 | - |
| 5 | `test_should_use_correct_db_number_when_labs_redis_db_set` | `LABS_REDIS_DB=1` 환경변수 | DB 1에 데이터 저장 확인 (DB 0과 격리) | 실제 Redis 인스턴스 | DB 번호 0과의 키 충돌 없음 |
| 6 | `test_should_authenticate_with_password_when_requirepass_set` | `--requirepass` 설정된 Redis + 올바른 password | 인증 성공, SET/GET 동작 | 실제 Redis 인스턴스 | password 없이 접근 시 `NOAUTH` 에러 |

### 1.2 ClickHouse 연결 테스트

**파일**: `tests/infra/test_clickhouse_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_connect_successfully_when_valid_readonly_credentials` | `CLICKHOUSE_READONLY_USER`, `CLICKHOUSE_READONLY_PASSWORD` | `SELECT 1` 결과 `1` 반환 | 실제 ClickHouse + 읽기 전용 계정 | - |
| 2 | `test_should_fail_connection_when_wrong_password` | 잘못된 비밀번호 | 인증 실패 예외 | 실제 ClickHouse | 빈 문자열 password |
| 3 | `test_should_respond_to_healthcheck_when_connected` | HTTP GET `http://clickhouse:8123/ping` | 응답 `Ok.\n` (200) | 실제 ClickHouse | - |
| 4 | `test_should_reject_write_when_readonly_account` | `labs_readonly` 계정으로 `INSERT INTO traces VALUES (...)` | 권한 오류 예외 발생 (`READONLY`) | 실제 ClickHouse + 읽기 전용 계정 | `CREATE TABLE`, `DROP TABLE`, `ALTER TABLE` 모두 거부 확인 |
| 5 | `test_should_allow_select_on_langfuse_db_when_readonly_account` | `labs_readonly` 계정으로 `SELECT * FROM system.tables LIMIT 1` | 정상 결과 반환 | 실제 ClickHouse + 읽기 전용 계정 | `langfuse` 데이터베이스 이외의 DB 접근 거부 확인 |
| 6 | `test_should_fail_connection_when_host_unreachable` | 잘못된 host | 연결 실패 예외, 적절한 타임아웃 | 없음 | - |

### 1.3 Langfuse 연결 테스트

**파일**: `tests/infra/test_langfuse_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_ok_when_langfuse_health_endpoint_called` | HTTP GET `http://langfuse:3000/api/public/health` | 200 응답, `status: "OK"` | 실제 Langfuse 인스턴스 | - |
| 2 | `test_should_connect_sdk_when_valid_api_keys` | 유효한 `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | SDK 초기화 성공, `auth_check()` 통과 | 실제 Langfuse 인스턴스 | - |
| 3 | `test_should_fail_sdk_when_invalid_api_keys` | 잘못된 API 키 | 인증 실패 예외 | 실제 Langfuse 인스턴스 | 빈 문자열 키, 형식이 다른 키 |
| 4 | `test_should_fail_connection_when_langfuse_host_unreachable` | 잘못된 `LANGFUSE_HOST` | 연결 실패 예외 | 없음 | - |

### 1.4 LiteLLM Proxy 연결 테스트

**파일**: `tests/infra/test_litellm_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_ok_when_litellm_health_called` | HTTP GET `http://litellm:4000/health` | 200 응답 | 실제 LiteLLM Proxy | - |
| 2 | `test_should_return_model_list_when_model_info_called` | HTTP GET `http://litellm:4000/model/info` + Master Key 헤더 | 200 응답, `data` 배열에 모델 목록 존재 | 실제 LiteLLM Proxy | 모델이 0개인 경우 빈 배열 |
| 3 | `test_should_reject_when_invalid_master_key` | 잘못된 Master Key로 `/model/info` 호출 | 401 또는 403 응답 | 실제 LiteLLM Proxy | Master Key 없이 호출 |
| 4 | `test_should_fail_connection_when_litellm_unreachable` | 잘못된 `LITELLM_BASE_URL` | 연결 실패 예외 | 없음 | - |

### 1.5 PostgreSQL 연결 테스트

**파일**: `tests/infra/test_postgres_connection.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_connect_successfully_when_valid_credentials` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `SELECT 1` 결과 반환 | 실제 PostgreSQL | - |
| 2 | `test_should_respond_to_healthcheck_when_connected` | `pg_isready` 커맨드 | 정상 응답 | 실제 PostgreSQL | - |
| 3 | `test_should_fail_connection_when_wrong_credentials` | 잘못된 password | 인증 실패 예외 | 실제 PostgreSQL | - |

---

## Phase 2: Backend 기초 테스트

### 2.1 JWT 미들웨어 테스트

**파일**: `tests/unit/test_security.py`

#### 2.1.1 유효한 JWT 처리

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_200_when_valid_jwt_with_admin_role` | `Authorization: Bearer <valid_admin_jwt>` | 200, 요청 처리 성공. `request.state.user.role == "admin"` | `jwt_admin`, mock JWKS 엔드포인트 | - |
| 2 | `test_should_return_200_when_valid_jwt_with_user_role` | `Authorization: Bearer <valid_user_jwt>` | 200, `request.state.user.role == "user"` | `jwt_user`, mock JWKS 엔드포인트 | - |
| 3 | `test_should_return_200_when_valid_jwt_with_viewer_role` | `Authorization: Bearer <valid_viewer_jwt>` | 200, `request.state.user.role == "viewer"` | `jwt_viewer`, mock JWKS 엔드포인트 | - |
| 4 | `test_should_extract_user_id_from_sub_claim_when_valid_jwt` | `sub: "user_001"` 포함 JWT | `request.state.user.sub == "user_001"` | `create_test_jwt(sub="user_001")` | - |

#### 2.1.2 만료/잘못된 JWT 처리

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 5 | `test_should_return_401_when_jwt_expired` | 만료 시간이 과거인 JWT (`exp_delta=timedelta(hours=-1)`) | 401, `{"error": {"code": "AUTH_REQUIRED", "message": ...}}` | `jwt_expired` | 방금 만료된 토큰 (1초 전) |
| 6 | `test_should_return_401_when_jwt_signature_invalid` | 다른 secret으로 서명된 JWT | 401, `{"error": {"code": "AUTH_REQUIRED"}}` | `create_test_jwt(secret="wrong-secret")` | - |
| 7 | `test_should_return_401_when_jwt_issuer_mismatch` | 잘못된 issuer (`iss: "https://evil.com"`) | 401 | `create_test_jwt(issuer="https://evil.com")` | - |
| 8 | `test_should_return_401_when_jwt_audience_mismatch` | 잘못된 audience (`aud: "other-app"`) | 401 | `create_test_jwt(audience="other-app")` | - |
| 9 | `test_should_return_401_when_no_authorization_header` | 헤더 없음 | 401, `{"error": {"code": "AUTH_REQUIRED", "message": "인증 토큰이 필요합니다"}}` | 없음 | - |
| 10 | `test_should_return_401_when_authorization_header_malformed` | `Authorization: InvalidFormat abc123` | 401 | 없음 | `Authorization: Bearer` (토큰 누락), `Authorization: Basic abc` (잘못된 스킴) |
| 11 | `test_should_return_401_when_authorization_header_empty_bearer` | `Authorization: Bearer ` (공백만) | 401 | 없음 | - |
| 12 | `test_should_return_401_when_jwt_payload_missing_required_claims` | `sub` 또는 `role` claim이 없는 JWT | 401 | `create_test_jwt`에서 claim 제거 | `exp` 누락, `iat` 누락 |

#### 2.1.3 RBAC 권한 검증

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 13 | `test_should_return_403_when_viewer_calls_admin_endpoint` | viewer JWT로 `PATCH /api/v1/prompts/{name}/versions/{v}/labels` 호출 | 403, `{"error": {"code": "FORBIDDEN"}}` | `jwt_viewer` | - |
| 14 | `test_should_return_403_when_user_calls_admin_endpoint` | user JWT로 `DELETE /api/v1/datasets/{name}` 호출 | 403 | `jwt_user` | - |
| 15 | `test_should_return_403_when_viewer_calls_user_endpoint` | viewer JWT로 `POST /api/v1/prompts` 호출 | 403 | `jwt_viewer` | - |
| 16 | `test_should_allow_admin_to_access_all_endpoints` | admin JWT로 admin/user/viewer 엔드포인트 각각 호출 | 모든 요청 200 (또는 적절한 성공 응답) | `jwt_admin` | - |
| 17 | `test_should_allow_user_to_access_user_and_viewer_endpoints` | user JWT로 user/viewer 엔드포인트 호출 | 성공 응답 | `jwt_user` | - |
| 18 | `test_should_return_403_when_role_claim_is_unknown` | `role: "superadmin"` (존재하지 않는 역할) | 403 | `create_test_jwt(role="superadmin")` | `role: ""` (빈 문자열), `role: null` |

### 2.2 Langfuse Client Wrapper 테스트

**파일**: `tests/unit/test_langfuse_client.py`

#### 2.2.1 프로젝트 전환

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_correct_client_when_valid_project_id` | `project_id="proj_1"` (PROJECTS_CONFIG에 등록된 프로젝트) | 해당 프로젝트의 public_key/secret_key로 초기화된 Langfuse 클라이언트 반환 | PROJECTS_CONFIG fixture | - |
| 2 | `test_should_switch_client_when_different_project_id` | `project_id="proj_1"` 호출 후 `project_id="proj_2"` 호출 | 각각 다른 API 키로 초기화된 클라이언트 반환 | PROJECTS_CONFIG fixture (2개 프로젝트) | - |
| 3 | `test_should_raise_error_when_project_id_not_found` | `project_id="non_existent"` | `PROJECT_NOT_FOUND` 에러 (404) | PROJECTS_CONFIG fixture | `project_id=""` (빈 문자열), `project_id=None` |
| 4 | `test_should_cache_client_when_same_project_id_called_repeatedly` | 동일 `project_id`로 3회 연속 호출 | 동일 클라이언트 인스턴스 반환 (매번 새로 생성하지 않음) | PROJECTS_CONFIG fixture | - |

#### 2.2.2 연결 실패 처리

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 5 | `test_should_return_graceful_error_when_langfuse_unreachable` | Langfuse 서버가 응답하지 않는 상태에서 `get_prompt()` 호출 | `LANGFUSE_ERROR` 예외 (502), 에러 메시지에 연결 실패 원인 포함 | `mock_langfuse.simulate_connection_failure()` | 타임아웃, DNS 실패, 서버 500 |
| 6 | `test_should_return_graceful_error_when_langfuse_returns_500` | Langfuse SDK가 500 에러를 반환하도록 설정 | `LANGFUSE_ERROR` 예외 (502), 원본 에러 메시지 전달 | mock Langfuse (500 응답) | - |
| 7 | `test_should_retry_on_transient_failure_when_configured` | 첫 호출 실패, 두 번째 성공 | 최종 성공 결과 반환 | mock Langfuse (순차적 실패/성공) | 최대 재시도 횟수 초과 시 최종 실패 |

#### 2.2.3 핵심 메서드 동작

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 8 | `test_should_parse_variables_when_get_prompt_called` | `get_prompt("test-prompt")` (프롬프트에 `{{input_text}}`, `{{rules}}` 포함) | 결과에 `variables: ["input_text", "rules"]` 포함 | mock Langfuse | 변수 없는 프롬프트, 중복 변수, 중첩 중괄호 `{{{var}}}` |
| 9 | `test_should_call_flush_when_flush_invoked` | `flush()` 호출 | Langfuse SDK의 `flush()` 메서드가 호출됨 | mock Langfuse | - |
| 10 | `test_should_create_trace_with_correct_metadata_when_called` | `create_trace(name, metadata)` 호출 | metadata에 `source: "ax-llm-eval-workflow"` 자동 추가 | mock Langfuse | metadata가 None인 경우 |

### 2.3 Redis Client 테스트

**파일**: `tests/unit/test_redis_client.py`

#### 2.3.1 연결 관리

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_connect_when_valid_redis_url` | 유효한 `REDIS_URL` | 연결 성공, `ping()` 응답 `True` | mock_redis 또는 실제 Redis | - |
| 2 | `test_should_raise_error_when_connection_failed` | 잘못된 `REDIS_URL` | `ConnectionError` 예외, 명확한 에러 메시지 | 없음 | 호스트 미응답, 인증 실패 |
| 3 | `test_should_use_connection_pool_when_initialized` | 클라이언트 초기화 | 연결 풀 사용 확인 (max_connections 설정) | mock_redis | - |

#### 2.3.2 HINCRBY 원자적 증가

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 4 | `test_should_increment_field_atomically_when_hincrby_called` | `HINCRBY ax:experiment:exp1 completed_items 1` | 필드 값 1 증가, 증가 후 값 반환 | mock_redis | - |
| 5 | `test_should_create_field_with_value_when_hincrby_on_nonexistent_field` | 존재하지 않는 필드에 `HINCRBY` | 필드 생성, 값 = increment | mock_redis | - |
| 6 | `test_should_increment_float_atomically_when_hincrbyfloat_called` | `HINCRBYFLOAT ax:experiment:exp1 total_cost_usd 0.023` | float 값 정확하게 누적 | mock_redis | 부동소수점 정밀도 문제 (0.1 + 0.2) |
| 7 | `test_should_handle_concurrent_increments_when_multiple_calls` | 동시에 10개 `HINCRBY` 호출 | 최종 값이 정확히 10 증가 | mock_redis (또는 실제 Redis) | - |

#### 2.3.3 Lua Script 실행 (상태 전이)

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 8 | `test_should_transition_status_when_valid_transition` | 현재 상태 `running`, 전이 요청 `running` -> `paused` | 상태가 `paused`로 변경, `updated_at` 갱신 | mock_redis | - |
| 9 | `test_should_reject_transition_when_invalid_current_status` | 현재 상태 `cancelled`, 전이 요청 `cancelled` -> `running` | `STATE_CONFLICT` 에러 반환 | mock_redis | - |
| 10 | `test_should_set_completed_at_when_terminal_status_reached` | `running` -> `completed` 전이 | `completed_at` 필드 설정, TTL 1시간으로 단축 | mock_redis | `failed`, `cancelled` 전이도 동일 동작 |
| 11 | `test_should_shorten_ttl_when_terminal_status` | `running` -> `completed` 전이 | TTL이 3600초(1시간)로 변경 | mock_redis | 기존 TTL이 86400초였는지 확인 |
| 12 | `test_should_set_error_message_when_failed_with_message` | `running` -> `failed`, `error_message="LLM timeout"` | `error_message` 필드에 값 저장 | mock_redis | 빈 문자열 error_message는 무시 |
| 13 | `test_should_return_experiment_not_found_when_key_missing` | 존재하지 않는 experiment_id | `EXPERIMENT_NOT_FOUND` 에러 | mock_redis | - |
| 14 | `test_should_support_multiple_allowed_statuses_when_comma_separated` | `expected_current_status="running,paused"`, 현재 `paused` | 전이 성공 | mock_redis | 현재 상태가 허용 목록에 없는 경우 거부 |

#### 2.3.4 TTL 설정/만료

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 15 | `test_should_set_ttl_when_experiment_created` | 실험 생성 시 Hash 저장 | TTL = 86400초 (24시간) | mock_redis | - |
| 16 | `test_should_refresh_ttl_when_item_completed` | 아이템 완료 이벤트 처리 | TTL이 86400초로 재설정 | mock_redis | - |
| 17 | `test_should_expire_key_when_ttl_reached` | TTL이 0이 된 키 조회 | `None` 반환 (키 만료) | mock_redis (TTL 시뮬레이션) | - |
| 18 | `test_should_apply_same_ttl_to_related_keys_when_terminal` | 실험 종료 시 Run Hash, Run Set, Failed Items Set | 모든 관련 키의 TTL이 3600초 | mock_redis | - |

---

## Phase 3: Core API 테스트

모든 API 테스트는 FastAPI TestClient를 사용한 통합 테스트이다.
공통 전제: 유효한 JWT (user 또는 admin)로 요청, `project_id` 파라미터 필수.

### 3.1 헬스체크 API

**파일**: `tests/integration/test_health_api.py`

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_ok_when_all_services_healthy` | `GET /api/v1/health` (인증 불필요) | 200, `{"status": "ok", "version": "1.0.0"}` | mock_langfuse, mock_redis, mock_litellm, mock_clickhouse (모두 정상) | - |
| 2 | `test_should_return_ok_without_auth_when_health_called` | `GET /api/v1/health` (Authorization 헤더 없음) | 200 | 모든 서비스 mock | 인증이 필요한 다른 엔드포인트와 구별 |
| 3 | `test_should_include_service_statuses_when_detailed_health` | `GET /api/v1/health` | 응답에 각 서비스별 상태 포함 (langfuse, litellm, clickhouse, redis) | 일부 서비스 실패 mock | 개별 서비스 장애 시 해당 서비스만 `fail` 표시 |

### 3.2 프롬프트 API

**파일**: `tests/integration/test_prompts_api.py`

#### 3.2.1 GET /api/v1/prompts -- 목록 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_prompt_list_when_prompts_exist` | `GET /api/v1/prompts?project_id=proj_1` | 200, `{"status": "success", "data": {"prompts": [...]}}`. 각 프롬프트에 `name`, `latest_version`, `labels`, `tags`, `created_at` 포함 | mock_langfuse (프롬프트 3개 등록) | - |
| 2 | `test_should_return_empty_list_when_no_prompts` | `GET /api/v1/prompts?project_id=proj_empty` | 200, `{"data": {"prompts": []}}` | mock_langfuse (프롬프트 0개) | - |
| 3 | `test_should_return_404_when_project_not_found` | `GET /api/v1/prompts?project_id=non_existent` | 404, `{"error": {"code": "PROJECT_NOT_FOUND"}}` | mock_langfuse | - |
| 4 | `test_should_return_422_when_project_id_missing` | `GET /api/v1/prompts` (project_id 누락) | 422, `{"error": {"code": "VALIDATION_ERROR"}}` | 없음 | - |
| 5 | `test_should_return_401_when_no_auth` | `GET /api/v1/prompts?project_id=proj_1` (JWT 없음) | 401 | 없음 | - |

#### 3.2.2 GET /api/v1/prompts/{name} -- 상세 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 6 | `test_should_return_prompt_detail_when_exists` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1` | 200, `name`, `version`, `type`, `prompt`, `config`, `labels`, `variables` 포함 | mock_langfuse (해당 프롬프트 등록) | - |
| 7 | `test_should_return_specific_version_when_version_param_set` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1&version=3` | 200, `version: 3` | mock_langfuse (v1, v2, v3 존재) | - |
| 8 | `test_should_return_labeled_version_when_label_param_set` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1&label=production` | 200, `labels` 배열에 `"production"` 포함 | mock_langfuse (production 라벨 설정) | - |
| 9 | `test_should_return_latest_version_when_no_version_or_label` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1` | 200, 가장 높은 version 반환 | mock_langfuse (v1~v5 존재) | - |
| 10 | `test_should_extract_variables_when_prompt_contains_template_vars` | 프롬프트 텍스트: `"{{input_text}}를 분석하세요. 규칙: {{rules}}"` | `variables: ["input_text", "rules"]` | mock_langfuse | - |
| 11 | `test_should_return_empty_variables_when_no_template_vars` | 프롬프트 텍스트: `"안녕하세요"` (변수 없음) | `variables: []` | mock_langfuse | - |
| 12 | `test_should_deduplicate_variables_when_same_var_used_multiple_times` | 프롬프트 텍스트: `"{{name}}의 {{name}} 분석"` | `variables: ["name"]` (중복 제거) | mock_langfuse | - |
| 13 | `test_should_return_404_when_prompt_not_found` | `GET /api/v1/prompts/non-existent?project_id=proj_1` | 404, `{"error": {"code": "PROMPT_NOT_FOUND"}}` | mock_langfuse | - |
| 14 | `test_should_return_404_when_version_not_found` | `GET /api/v1/prompts/sentiment-analysis?project_id=proj_1&version=999` | 404 | mock_langfuse | version=0, version=-1 |
| 15 | `test_should_handle_chat_type_prompt_when_type_is_chat` | chat 타입 프롬프트 (messages 배열) | `type: "chat"`, `prompt`이 messages 배열 형태 | mock_langfuse | - |

#### 3.2.3 GET /api/v1/prompts/{name}/versions -- 버전 목록

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 16 | `test_should_return_version_list_when_versions_exist` | `GET /api/v1/prompts/sentiment-analysis/versions?project_id=proj_1` | 200, `{"data": {"versions": [{"version": 1, "labels": [...], "created_at": "...", "created_by": "..."}]}}` | mock_langfuse (3개 버전) | - |
| 17 | `test_should_return_empty_when_prompt_has_no_versions` | 존재하지 않는 프롬프트 이름 | 404, `PROMPT_NOT_FOUND` | mock_langfuse | - |

#### 3.2.4 POST /api/v1/prompts -- 생성

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 18 | `test_should_create_prompt_when_valid_request` | `POST` body: `{"project_id": "proj_1", "name": "new-prompt", "prompt": "...", "type": "text", "labels": ["staging"]}` | 200, `{"data": {"name": "new-prompt", "version": 1, "labels": ["staging"]}}` | mock_langfuse, `jwt_user` | - |
| 19 | `test_should_increment_version_when_prompt_name_exists` | 이미 존재하는 프롬프트 이름으로 `POST` | 새 버전 번호 반환 (기존 + 1) | mock_langfuse (기존 v2 존재) | - |
| 20 | `test_should_return_403_when_viewer_creates_prompt` | viewer JWT로 `POST /api/v1/prompts` | 403, `FORBIDDEN` | `jwt_viewer` | - |
| 21 | `test_should_return_422_when_missing_required_fields` | `POST` body에서 `name` 누락 | 422, `VALIDATION_ERROR` | `jwt_user` | `prompt` 누락, `type` 누락, `project_id` 누락 |
| 22 | `test_should_return_422_when_invalid_type` | `type: "invalid"` | 422 | `jwt_user` | `type` 필드가 `text` 또는 `chat` 외의 값 |
| 23 | `test_should_create_chat_prompt_when_type_is_chat` | `type: "chat"`, `prompt: [{"role": "system", "content": "..."}]` | 성공, chat 타입 프롬프트 생성 | mock_langfuse, `jwt_user` | - |

#### 3.2.5 PATCH /api/v1/prompts/{name}/versions/{version}/labels -- 라벨 승격

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 24 | `test_should_update_labels_when_admin_calls` | admin JWT, `PATCH` body: `{"project_id": "proj_1", "labels": ["production"]}` | 200, 라벨 업데이트 성공 | mock_langfuse, `jwt_admin` | - |
| 25 | `test_should_return_403_when_user_promotes_label` | user JWT로 `PATCH` 호출 | 403 | `jwt_user` | - |
| 26 | `test_should_return_403_when_viewer_promotes_label` | viewer JWT로 `PATCH` 호출 | 403 | `jwt_viewer` | - |
| 27 | `test_should_return_404_when_prompt_version_not_found` | 존재하지 않는 프롬프트/버전 | 404 | mock_langfuse, `jwt_admin` | - |
| 28 | `test_should_allow_multiple_labels_when_array_provided` | `labels: ["production", "reviewed"]` | 200, 여러 라벨 설정 | mock_langfuse, `jwt_admin` | - |
| 29 | `test_should_allow_empty_labels_when_removing_all` | `labels: []` | 200, 기존 라벨 제거 | mock_langfuse, `jwt_admin` | - |

### 3.3 데이터셋 API

**파일**: `tests/integration/test_datasets_api.py`

#### 3.3.1 GET /api/v1/datasets -- 목록 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_dataset_list_when_datasets_exist` | `GET /api/v1/datasets?project_id=proj_1` | 200, `{"data": {"datasets": [{"name": "...", "item_count": 100, "created_at": "...", "metadata": {...}}]}}` | mock_langfuse (데이터셋 2개) | - |
| 2 | `test_should_return_empty_list_when_no_datasets` | `GET /api/v1/datasets?project_id=proj_empty` | 200, `{"data": {"datasets": []}}` | mock_langfuse (데이터셋 0개) | - |
| 3 | `test_should_return_422_when_project_id_missing` | `GET /api/v1/datasets` | 422 | 없음 | - |

#### 3.3.2 GET /api/v1/datasets/{name}/items -- 아이템 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 4 | `test_should_return_items_with_pagination_when_valid_request` | `GET /api/v1/datasets/golden-100/items?project_id=proj_1&page=1&page_size=20` | 200, `items` 배열 (최대 20개), `total: 100`, 각 아이템에 `id`, `input`, `expected_output`, `metadata` 포함 | mock_langfuse (100개 아이템) | - |
| 5 | `test_should_return_second_page_when_page_2_requested` | `page=2&page_size=20` | 200, 21~40번째 아이템 반환 | mock_langfuse | - |
| 6 | `test_should_return_empty_when_page_exceeds_total` | `page=999&page_size=20` | 200, `items: []`, `total: 100` | mock_langfuse | - |
| 7 | `test_should_return_404_when_dataset_not_found` | `GET /api/v1/datasets/non-existent/items?project_id=proj_1` | 404, `DATASET_NOT_FOUND` | mock_langfuse | - |
| 8 | `test_should_use_default_pagination_when_no_params` | `GET /api/v1/datasets/golden-100/items?project_id=proj_1` (page/page_size 미지정) | 200, 기본 page=1, page_size=20 적용 | mock_langfuse | - |

#### 3.3.3 POST /api/v1/datasets/upload -- 업로드

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 9 | `test_should_upload_csv_when_valid_file_and_mapping` | multipart: CSV 파일 (100행) + mapping JSON `{"input_columns": ["input"], "output_column": "expected", "metadata_columns": ["difficulty"]}` | 200, `{"data": {"dataset_name": "test-ds", "items_created": 100, "status": "completed"}}` | mock_langfuse, `jwt_user` | - |
| 10 | `test_should_upload_json_when_valid_json_file` | multipart: JSON 파일 (배열 형태) + mapping | 200, 아이템 생성 성공 | mock_langfuse, `jwt_user` | - |
| 11 | `test_should_upload_jsonl_when_valid_jsonl_file` | multipart: JSONL 파일 (줄 단위 JSON) + mapping | 200, 아이템 생성 성공 | mock_langfuse, `jwt_user` | - |
| 12 | `test_should_return_400_when_csv_mapping_column_not_found` | mapping의 `input_columns`에 존재하지 않는 컬럼명 | 400, `{"error": {"code": "MAPPING_ERROR"}}` | `jwt_user` | `output_column`이 없는 경우, `metadata_columns`에 없는 컬럼 |
| 13 | `test_should_return_413_when_file_exceeds_50mb` | 51MB 파일 업로드 | 413, `{"error": {"code": "FILE_TOO_LARGE"}}` | `jwt_user` | 정확히 50MB (경계값), 50MB - 1 byte (허용) |
| 14 | `test_should_return_400_when_rows_exceed_10000` | 10,001행 CSV 파일 | 400, 행 수 초과 에러 메시지 | `jwt_user` | 정확히 10,000행 (허용), 빈 행 포함 시 카운트 |
| 15 | `test_should_return_400_when_file_encoding_undetectable` | 잘못된 인코딩 바이너리 파일 | 400, `{"error": {"code": "FILE_ENCODING_ERROR"}}` | `jwt_user` | - |
| 16 | `test_should_detect_encoding_when_euckr_file` | EUC-KR 인코딩 CSV | 200, 한글 데이터 정상 파싱 | mock_langfuse, `jwt_user` | - |
| 17 | `test_should_return_400_when_file_format_invalid` | 확장자가 .txt인 파일, 또는 파싱 불가능한 내용 | 400, `{"error": {"code": "FILE_PARSE_ERROR"}}` | `jwt_user` | XML, Excel 파일 등 미지원 포맷 |
| 18 | `test_should_return_400_when_csv_has_no_rows` | 헤더만 있고 데이터 행이 없는 CSV | 400, 데이터 없음 에러 | `jwt_user` | - |
| 19 | `test_should_return_403_when_viewer_uploads` | viewer JWT로 업로드 시도 | 403 | `jwt_viewer` | - |
| 20 | `test_should_handle_unicode_data_when_csv_contains_special_chars` | CSV에 이모지, 한자, 특수문자 포함 | 200, 데이터 정상 저장 | mock_langfuse, `jwt_user` | null 바이트 포함 데이터 |
| 21 | `test_should_map_multiple_input_columns_when_mapping_has_array` | `input_columns: ["text", "context"]` | 200, input이 `{"text": "...", "context": "..."}` 형태로 저장 | mock_langfuse, `jwt_user` | - |
| 22 | `test_should_return_422_when_mapping_json_invalid` | mapping 파라미터가 유효하지 않은 JSON 문자열 | 422, `VALIDATION_ERROR` | `jwt_user` | - |

#### 3.3.4 POST /api/v1/datasets/upload/preview -- 미리보기

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 23 | `test_should_return_preview_when_valid_file` | CSV 파일 + mapping | 200, `{"data": {"columns": [...], "preview": [5건], "total_rows": 100}}` | 없음 (파싱만, Langfuse 호출 없음) | - |
| 24 | `test_should_return_max_5_items_when_file_has_many_rows` | 1000행 CSV | 200, `preview` 배열 길이 = 5 | 없음 | - |
| 25 | `test_should_show_mapped_structure_when_mapping_applied` | mapping 적용 | 200, 각 preview 아이템이 `input`, `expected_output`, `metadata` 구조 | 없음 | - |
| 26 | `test_should_return_columns_when_file_parsed` | CSV 파일 | 200, `columns` 배열에 모든 컬럼명 포함 | 없음 | - |
| 27 | `test_should_return_400_when_preview_file_unparseable` | 잘못된 형식의 파일 | 400, `FILE_PARSE_ERROR` | 없음 | - |
| 28 | `test_should_not_require_auth_at_same_level_when_preview` | user JWT | 200, 인증 성공 (viewer도 미리보기 가능해야 함) | `jwt_viewer` | 미리보기는 데이터 저장 없으므로 viewer 허용 검토 |

#### 3.3.5 DELETE /api/v1/datasets/{name} -- 삭제

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 29 | `test_should_delete_dataset_when_admin_calls` | admin JWT, `DELETE /api/v1/datasets/test-ds?project_id=proj_1` | 200, 삭제 성공 | mock_langfuse, `jwt_admin` | - |
| 30 | `test_should_return_403_when_user_deletes_dataset` | user JWT | 403 | `jwt_user` | - |
| 31 | `test_should_return_403_when_viewer_deletes_dataset` | viewer JWT | 403 | `jwt_viewer` | - |
| 32 | `test_should_return_404_when_deleting_nonexistent_dataset` | admin JWT, 존재하지 않는 데이터셋 이름 | 404, `DATASET_NOT_FOUND` | mock_langfuse, `jwt_admin` | - |
| 33 | `test_should_return_422_when_project_id_missing_on_delete` | `DELETE /api/v1/datasets/test-ds` (project_id 누락) | 422 | `jwt_admin` | - |

### 3.4 모델 API

**파일**: `tests/integration/test_models_api.py`

#### 3.4.1 GET /api/v1/models -- 모델 목록

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_model_list_when_litellm_available` | `GET /api/v1/models` | 200, `{"data": {"models": [{"id": "gpt-4o", "provider": "azure", "display_name": "...", "supports_vision": true, "supports_streaming": true, "max_tokens": 128000, "cost_per_1k_input": 0.0025, "cost_per_1k_output": 0.01}]}}` | mock_litellm (모델 5개) | - |
| 2 | `test_should_return_empty_list_when_no_models_configured` | LiteLLM에 모델 0개 | 200, `{"data": {"models": []}}` | mock_litellm (빈 목록) | - |
| 3 | `test_should_return_502_when_litellm_unreachable` | LiteLLM Proxy 연결 실패 | 502, `{"error": {"code": "LLM_ERROR"}}` | mock_litellm (연결 실패) | - |
| 4 | `test_should_include_vision_support_flag_when_model_supports_it` | vision 지원 모델 포함 | 각 모델의 `supports_vision` 필드 정확 | mock_litellm | - |
| 5 | `test_should_group_models_by_provider_when_multiple_providers` | Azure, Google, Anthropic 모델 혼합 | `provider` 필드별 정확한 분류 | mock_litellm | - |

### 3.5 프로젝트 API

**파일**: `tests/integration/test_projects_api.py`

#### 3.5.1 GET /api/v1/projects -- 목록 조회

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_project_list_when_projects_configured` | `GET /api/v1/projects` | 200, `{"data": {"projects": [{"id": "proj_1", "name": "서비스A", "created_at": "..."}]}}` | PROJECTS_CONFIG fixture (2개 프로젝트) | - |
| 2 | `test_should_return_empty_list_when_no_projects` | PROJECTS_CONFIG가 비어있음 | 200, `{"data": {"projects": []}}` | 빈 PROJECTS_CONFIG | - |

#### 3.5.2 POST /api/v1/projects/switch -- 전환

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 3 | `test_should_switch_project_when_valid_project_id` | `POST` body: `{"project_id": "proj_1"}` | 200, 프로젝트 전환 성공 응답. 해당 프로젝트의 Langfuse API Key 검증 통과 | PROJECTS_CONFIG fixture, mock_langfuse | - |
| 4 | `test_should_return_404_when_project_not_found` | `POST` body: `{"project_id": "non_existent"}` | 404, `{"error": {"code": "PROJECT_NOT_FOUND"}}` | PROJECTS_CONFIG fixture | - |
| 5 | `test_should_return_422_when_project_id_missing` | `POST` body: `{}` | 422, `VALIDATION_ERROR` | 없음 | - |
| 6 | `test_should_verify_langfuse_connectivity_when_switching` | 유효한 project_id이지만 해당 프로젝트의 Langfuse 연결 실패 | 502, `LANGFUSE_ERROR` 또는 연결 실패 메시지 | mock_langfuse (연결 실패 시뮬레이션) | - |
| 7 | `test_should_be_stateless_when_project_switched` | switch 후 다른 API 호출 시 project_id 파라미터 확인 | 이후 API 호출은 project_id 파라미터로 프로젝트를 지정해야 함 (서버에 상태 저장하지 않음) | PROJECTS_CONFIG fixture | - |

### 3.6 검색 API

**파일**: `tests/integration/test_search_api.py`

#### 3.6.1 GET /api/v1/search -- 통합 검색

| # | 테스트 이름 | 입력 | 기대 출력 | 필요 fixture/mock | 엣지케이스 |
|---|------------|------|----------|-------------------|-----------|
| 1 | `test_should_return_results_across_all_types_when_query_matches` | `GET /api/v1/search?project_id=proj_1&q=sentiment` | 200, `{"data": {"results": {"prompts": [...], "datasets": [...], "experiments": [...]}}}` | mock_langfuse + mock_redis ("sentiment" 키워드 매칭 데이터) | - |
| 2 | `test_should_return_empty_results_when_no_match` | `GET /api/v1/search?project_id=proj_1&q=xyznonexistent` | 200, `{"data": {"results": {"prompts": [], "datasets": [], "experiments": []}}}` | mock_langfuse + mock_redis | - |
| 3 | `test_should_filter_by_type_when_type_param_set` | `GET /api/v1/search?project_id=proj_1&q=test&type=prompt` | 200, `prompts` 배열만 결과 포함, `datasets`와 `experiments`는 빈 배열 | mock_langfuse | - |
| 4 | `test_should_filter_dataset_only_when_type_is_dataset` | `q=golden&type=dataset` | 200, `datasets`만 결과 포함 | mock_langfuse | - |
| 5 | `test_should_filter_experiment_only_when_type_is_experiment` | `q=v3-vs-v4&type=experiment` | 200, `experiments`만 결과 포함 | mock_redis | - |
| 6 | `test_should_return_422_when_query_missing` | `GET /api/v1/search?project_id=proj_1` (q 누락) | 422, `VALIDATION_ERROR` | 없음 | - |
| 7 | `test_should_return_422_when_project_id_missing` | `GET /api/v1/search?q=test` | 422 | 없음 | - |
| 8 | `test_should_return_422_when_invalid_type_param` | `type=invalid` | 422, `VALIDATION_ERROR` | 없음 | `type=PROMPT` (대문자) |
| 9 | `test_should_include_match_context_when_results_found` | 매칭 결과 존재 | 각 결과 아이템에 `match_context` 필드 포함 | mock_langfuse | - |
| 10 | `test_should_handle_special_characters_in_query_when_searched` | `q=test%20"prompt"` (특수문자, 공백, 따옴표) | 200, 에러 없이 처리 | mock_langfuse | SQL injection 시도 문자열, HTML 태그, 한글 검색어 |
| 11 | `test_should_handle_empty_query_when_q_is_empty_string` | `q=` (빈 문자열) | 422, `VALIDATION_ERROR` 또는 빈 결과 | 없음 | 공백만 포함된 쿼리 `q=%20%20` |

---

## 테스트 실행 가이드

### 전체 테스트 실행

```bash
# Backend 단위 테스트
cd backend && pytest tests/unit/ -v

# Backend 통합 테스트
cd backend && pytest tests/integration/ -v

# Backend 인프라 테스트 (Docker 서비스 필요)
cd backend && pytest tests/infra/ -v -m infra

# Frontend 테스트
cd frontend && npx vitest run

# 전체 (단위 + 통합)
cd backend && pytest tests/unit/ tests/integration/ -v --tb=short
```

### 마커별 실행

```bash
# 단위 테스트만
pytest -m unit

# 통합 테스트만
pytest -m integration

# 인프라 테스트만 (느림, 실제 서비스 필요)
pytest -m infra

# 빠른 테스트만 (infra 제외)
pytest -m "not infra and not slow"
```

### 커버리지 리포트

```bash
# Backend
cd backend && pytest --cov=app --cov-report=html --cov-report=term-missing

# Frontend
cd frontend && npx vitest run --coverage
```
